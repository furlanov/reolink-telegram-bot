#!/usr/bin/env python3
"""
rlbot: Reolink camera TCP push events to Telegram.
Connects directly to camera, subscribes to events, sends snapshots to Telegram.
Also runs a Telegram bot to manage chat ID registrations.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
import time
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp
from reolink_aio.api import Host

# Config from environment
CAMERA_HOST = os.environ.get("CAMERA_HOST", "192.168.1.72")
CAMERA_USER = os.environ.get("CAMERA_USER", "admin")
CAMERA_PASS = os.environ.get("CAMERA_PASS", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
REGISTER_PASSWORD = os.environ.get("REGISTER_PASSWORD", "")
DATA_DIR = "/data"
CHAT_IDS_FILE = f"{DATA_DIR}/chat_ids.json"
LOG_FILE = f"{DATA_DIR}/rlbot.log"
CHANNEL = 0  # Camera channel (0 for single camera)

# Reconnect settings
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60

# Event handling settings
EVENT_COOLDOWN = 20.0  # Seconds between notifications per event type
SNAPSHOT_DELAY = 0.3   # Seconds to wait after event before taking snapshot
SNAPSHOT_RETRIES = 2   # Number of retry attempts for snapshot

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Command definitions: single source of truth for /help and setMyCommands.
COMMANDS: List[tuple] = [
    ("start",  "<password>", "Register for notifications"),
    ("stop",   "",            "Unsubscribe from notifications"),
    ("snap",   "",            "Take a camera snapshot"),
    ("iam",    "<name>",      "Set your name"),
    ("status", "",            "Check registration status"),
    ("help",   "",            "Show this help"),
]

os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)
logging.getLogger("reolink_aio").setLevel(logging.WARNING)


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep for up to `seconds`, returning early if `stop` is set."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


class ChatStore:
    """In-memory cache of {chat_id: name}, persisted atomically to disk."""

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            if isinstance(data, list):  # legacy format
                return {str(x): "" for x in data}
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError) as e:
            log.error("Failed to load chat data: %s", e)
        return {}

    def _save(self) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self.path), prefix=".chat_ids_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self.path)
        except OSError as e:
            log.error("Failed to save chat data: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def ids(self) -> List[str]:
        return list(self._data.keys())

    def contains(self, chat_id: str) -> bool:
        return chat_id in self._data

    def name(self, chat_id: str) -> str:
        return self._data.get(chat_id, "")

    def register(self, chat_id: str) -> bool:
        if chat_id in self._data:
            return False
        self._data[chat_id] = ""
        self._save()
        return True

    def unregister(self, chat_id: str) -> bool:
        if chat_id not in self._data:
            return False
        del self._data[chat_id]
        self._save()
        return True

    def set_name(self, chat_id: str, name: str) -> None:
        self._data[chat_id] = name
        self._save()


async def send_photo(
    session: aiohttp.ClientSession,
    photo_data: bytes,
    caption: str,
    chat_id: str,
) -> None:
    try:
        data = aiohttp.FormData()
        data.add_field("chat_id", chat_id)
        data.add_field("caption", caption)
        data.add_field("photo", photo_data, filename="snapshot.jpg", content_type="image/jpeg")
        async with session.post(
            f"{API_BASE}/sendPhoto",
            data=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            log.info("Sent to %s: status=%s", chat_id, r.status)
    except Exception as e:
        log.error("Failed to send to %s: %s", chat_id, e)


async def broadcast_photo(
    session: aiohttp.ClientSession,
    photo_data: bytes,
    caption: str,
    chat_ids: List[str],
) -> None:
    if not chat_ids:
        log.warning("No chat IDs registered")
        return
    await asyncio.gather(*(send_photo(session, photo_data, caption, cid) for cid in chat_ids))


class ReolinkBot:
    """Camera connection, event subscription, snapshot dispatch."""

    def __init__(self, session: aiohttp.ClientSession, store: ChatStore):
        self.session = session
        self.store = store
        self.host: Optional[Host] = None
        self.last_event_times: Dict[str, float] = {}
        self._event_tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()

    async def connect(self) -> bool:
        try:
            self.host = Host(
                host=CAMERA_HOST,
                username=CAMERA_USER,
                password=CAMERA_PASS,
                port=80,
            )
            await self.host.get_host_data()
            log.info("Connected to %s (%s)", self.host.nvr_name, CAMERA_HOST)
            return True
        except Exception as e:
            log.error("Failed to connect to camera: %s", e)
            if self.host:
                try:
                    await self.host.logout()
                except Exception as logout_err:
                    log.debug("Logout during failed connect: %s", logout_err)
            self.host = None
            return False

    async def get_snapshot(self) -> Optional[bytes]:
        if not self.host:
            return None
        for attempt in range(SNAPSHOT_RETRIES):
            try:
                snapshot = await self.host.get_snapshot(channel=CHANNEL)
                if snapshot:
                    log.info("Snapshot captured (%d bytes)", len(snapshot))
                    return snapshot
            except Exception as e:
                log.error("Snapshot attempt %d failed: %s", attempt + 1, e)
                if attempt < SNAPSHOT_RETRIES - 1:
                    await asyncio.sleep(0.2)
        return None

    async def handle_event(self, event_type: str) -> None:
        now = time.time()
        last_time = self.last_event_times.get(event_type, 0.0)
        if now - last_time < EVENT_COOLDOWN:
            log.debug("Event debounced (%s): %.1fs since last", event_type, now - last_time)
            return
        self.last_event_times[event_type] = now

        log.info("Event: %s", event_type)
        await asyncio.sleep(SNAPSHOT_DELAY)

        snapshot = await self.get_snapshot()
        if not snapshot:
            log.error("No snapshot available after retries")
            return

        await broadcast_photo(self.session, snapshot, event_type, self.store.ids())
        log.info("Complete (%.2fs total)", time.time() - now)

    def _spawn_event(self, event_type: str) -> None:
        task = asyncio.create_task(self.handle_event(event_type))
        self._event_tasks.add(task)
        task.add_done_callback(self._event_tasks.discard)

    def tcp_push_callback(self) -> None:
        """Called by reolink_aio on TCP push from camera."""
        if not self.host:
            return
        try:
            # Check all detection types independently so simultaneous events
            # (e.g. person + vehicle) both trigger. Per-type cooldown
            # prevents duplicate sends when the same push repeats.
            if self.host.ai_detected(CHANNEL, "people"):
                self._spawn_event("Person")
            if self.host.ai_detected(CHANNEL, "vehicle"):
                self._spawn_event("Vehicle")
            if self.host.ai_detected(CHANNEL, "dog_cat"):
                self._spawn_event("Animal")
            if self.host.motion_detected(CHANNEL):
                self._spawn_event("Motion")
        except Exception as e:
            log.error("Error in TCP push callback: %s", e)

    async def send_snapshot_to_chat(self, chat_id: str) -> None:
        log.info("Manual snapshot requested by %s", chat_id)
        snapshot = await self.get_snapshot()
        if not snapshot:
            return
        await send_photo(self.session, snapshot, "Snapshot", chat_id)

    async def subscribe_events(self) -> bool:
        if not self.host:
            return False
        try:
            self.host.baichuan.register_callback("rlbot", self.tcp_push_callback)
            await self.host.baichuan.subscribe_events()
            log.info("Subscribed to TCP push events")
            return True
        except Exception as e:
            log.error("Failed to subscribe to TCP push: %s", e)
            return False

    async def unsubscribe_events(self) -> None:
        if not self.host:
            return
        try:
            await self.host.baichuan.unsubscribe_events()
            self.host.baichuan.unregister_callback("rlbot")
            log.info("Unsubscribed from events")
        except Exception as e:
            log.debug("Unsubscribe error (ignored): %s", e)

    async def disconnect(self) -> None:
        if self.host:
            try:
                await self.unsubscribe_events()
                await self.host.logout()
                log.info("Disconnected from camera")
            except Exception as e:
                log.debug("Disconnect error: %s", e)
            self.host = None

    async def run(self) -> None:
        reconnect_delay = RECONNECT_DELAY
        while not self._stop.is_set():
            if await self.connect() and await self.subscribe_events():
                reconnect_delay = RECONNECT_DELAY
                while not self._stop.is_set() and self.host:
                    await sleep_or_stop(self._stop, 10)
                    if self._stop.is_set():
                        break
                    if not self.host.baichuan.events_active:
                        log.warning("TCP push connection lost, reconnecting...")
                        break

            await self.disconnect()
            if self._stop.is_set():
                break

            log.info("Reconnecting in %d seconds...", reconnect_delay)
            await sleep_or_stop(self._stop, reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

    def stop(self) -> None:
        self._stop.set()


class TelegramBot:
    """Long-polling Telegram bot for registration and /snap."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        store: ChatStore,
        snapshot_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.session = session
        self.store = store
        self.snapshot_callback = snapshot_callback
        self.last_update_id = 0
        self._stop = asyncio.Event()

    async def send_message(self, chat_id: str, text: str) -> None:
        try:
            async with self.session.post(
                f"{API_BASE}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    log.warning("sendMessage %s: status=%s", chat_id, r.status)
        except Exception as e:
            log.error("Failed to send message to %s: %s", chat_id, e)

    async def handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        if text.startswith("/start"):
            parts = text.split()
            if len(parts) == 1:
                await self.send_message(chat_id, "Send /start <password>")
            elif len(parts) == 2 and parts[1] == REGISTER_PASSWORD:
                if self.store.register(chat_id):
                    log.info("Registered chat ID: %s", chat_id)
                    await self.send_message(chat_id, "✅ Registered")
                else:
                    await self.send_message(chat_id, "ℹ️ Already registered")
            else:
                await self.send_message(chat_id, "❌ Wrong password")

        elif text == "/stop":
            if self.store.unregister(chat_id):
                log.info("Unregistered chat ID: %s", chat_id)
                await self.send_message(chat_id, "✅ Unregistered")
            else:
                await self.send_message(chat_id, "ℹ️ Not registered")

        elif text.startswith("/iam"):
            if not self.store.contains(chat_id):
                await self.send_message(chat_id, "❌ Register first with /start <password>")
                return
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                name = parts[1].strip()
                self.store.set_name(chat_id, name)
                log.info("Set name for %s: %s", chat_id, name)
                await self.send_message(chat_id, f"✅ Name set: {name}")
            else:
                current = self.store.name(chat_id)
                if current:
                    await self.send_message(chat_id, f"ℹ️ Your name: {current}\nChange with /iam <name>")
                else:
                    await self.send_message(chat_id, "Send /iam <name>")

        elif text == "/status":
            if self.store.contains(chat_id):
                name = self.store.name(chat_id)
                await self.send_message(chat_id, f"✅ Registered as: {name}" if name else "✅ Registered")
            else:
                await self.send_message(chat_id, "❌ Not registered")

        elif text == "/help":
            await self.send_message(chat_id, self._help_text())

        elif text == "/snap":
            if not self.store.contains(chat_id):
                await self.send_message(chat_id, "❌ Register first")
                return
            if self.snapshot_callback:
                await self.snapshot_callback(chat_id)
            else:
                await self.send_message(chat_id, "❌ Camera not connected")

    @staticmethod
    def _help_text() -> str:
        lines = []
        for cmd, args, desc in COMMANDS:
            usage = f"/{cmd} {args}".strip()
            lines.append(f"{usage} — {desc}")
        return "\n".join(lines)

    async def poll_updates(self) -> None:
        try:
            async with self.session.get(
                f"{API_BASE}/getUpdates",
                params={"offset": self.last_update_id + 1, "timeout": 30},
                timeout=aiohttp.ClientTimeout(total=35),
            ) as r:
                if r.status != 200:
                    log.warning("getUpdates status=%s", r.status)
                    await sleep_or_stop(self._stop, 5)
                    return
                data = await r.json()
                for update in data.get("result", []):
                    self.last_update_id = update.get("update_id", self.last_update_id)
                    if "message" in update:
                        await self.handle_message(update["message"])
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Telegram poll error: %s", e)
            await sleep_or_stop(self._stop, 5)

    async def set_commands(self) -> None:
        """Register the command list so Telegram clients show `/` autocomplete."""
        payload = [
            {
                "command": cmd,
                "description": f"{desc} (/{cmd} {args})".strip() if args else desc,
            }
            for cmd, args, desc in COMMANDS
        ]
        try:
            async with self.session.post(
                f"{API_BASE}/setMyCommands",
                json={"commands": payload},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    log.info("Registered %d bot commands for autocomplete", len(payload))
                else:
                    log.warning("setMyCommands status=%s", r.status)
        except Exception as e:
            log.error("setMyCommands failed: %s", e)

    async def run(self) -> None:
        log.info("Telegram bot started")
        await self.set_commands()
        while not self._stop.is_set():
            await self.poll_updates()

    def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set")
        sys.exit(1)
    if not CAMERA_PASS:
        log.error("CAMERA_PASS not set")
        sys.exit(1)
    if not REGISTER_PASSWORD:
        log.error("REGISTER_PASSWORD not set")
        sys.exit(1)

    log.info("Starting rlbot (reolink_aio)")
    log.info("Camera: %s@%s", CAMERA_USER, CAMERA_HOST)

    store = ChatStore(CHAT_IDS_FILE)

    # One session shared across both bots: keeps TCP + TLS to api.telegram.org warm.
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        reolink_bot = ReolinkBot(session, store)
        telegram_bot = TelegramBot(session, store, reolink_bot.send_snapshot_to_chat)

        loop = asyncio.get_running_loop()

        def shutdown() -> None:
            log.info("Shutdown signal received")
            reolink_bot.stop()
            telegram_bot.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown)

        try:
            await asyncio.gather(reolink_bot.run(), telegram_bot.run())
        finally:
            await reolink_bot.disconnect()
            log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
