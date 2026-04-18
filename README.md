# Reolink Telegram Bot

Telegram notifications with snapshots for Reolink cameras. Self-hosted, no Reolink cloud, no NVR, no Home Assistant.

Connects directly to the camera over the Reolink Baichuan TCP push protocol, so alerts arrive sub-second without polling or email relay.

> **Tested on:** Reolink **RLC-810A** only. Other Reolink cameras that speak Baichuan and expose AI detection should work, but nothing else has been verified.

---

## Features

- Instant push via Reolink's native TCP push (Baichuan), not email or polling.
- AI detections: person, vehicle, animal (dog/cat), plus motion.
- Each notification includes a fresh JPEG snapshot.
- Multi-user — anyone with the registration password can `/start` to subscribe.
- On-demand snapshots via `/snap`.
- Per-event-type cooldown to suppress repeat alerts on the same subject.
- Auto-reconnect with exponential backoff.
- Single Python file, runs on a Raspberry Pi.
- Docker Compose deploy.

## How it works

```
┌──────────────┐   TCP push    ┌────────┐   sendPhoto   ┌──────────┐
│ Reolink cam  │ ────────────▶ │ rlbot  │ ────────────▶ │ Telegram │
│ (Baichuan)   │   snapshot    │        │   snapshot    │  chats   │
└──────────────┘ ◀──────────── └────────┘               └──────────┘
```

1. Bot connects to the camera on port 80 and subscribes to TCP push events.
2. Camera pushes on AI/motion detection.
3. Bot pulls a JPEG snapshot and broadcasts to every registered Telegram chat.
4. A long-polling Telegram handler manages `/start`, `/stop`, `/snap`, etc.

## Quick start (Docker)

```bash
git clone https://github.com/furlanov/reolink-telegram-bot.git
cd reolink-telegram-bot
cp .env.example .env
# edit .env with your camera + Telegram credentials
docker compose up -d
```

Logs: `docker logs -f rlbot`.

## Getting a Telegram bot token

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`.
2. Pick a name and username. Paste the returned token into `.env` as `BOT_TOKEN`.
3. Open a chat with your bot and send `/start <REGISTER_PASSWORD>`.

## Configuration

Environment variables (see `.env.example`):

| Variable            | Required | Description                                                  |
| ------------------- | :------: | ------------------------------------------------------------ |
| `CAMERA_HOST`       |    yes   | Camera IP address                                            |
| `CAMERA_USER`       |    yes   | Camera username (usually `admin`)                            |
| `CAMERA_PASS`       |    yes   | Camera password                                              |
| `BOT_TOKEN`         |    yes   | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `REGISTER_PASSWORD` |    yes   | Shared password required to subscribe via `/start`           |
| `LOG_LEVEL`         |          | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`                |
| `TZ`                |          | Timezone for log timestamps                                  |

## Telegram commands

| Command             | Description                          |
| ------------------- | ------------------------------------ |
| `/start <password>` | Register this chat for notifications |
| `/stop`             | Unsubscribe                          |
| `/snap`             | On-demand snapshot                   |
| `/iam <name>`       | Set display name                     |
| `/status`           | Check registration status            |
| `/help`             | Show help                            |

The bot registers these via `setMyCommands` on startup, so they appear in the Telegram `/` autocomplete menu.

## Project layout

```
.
├── rlbot_reolink.py     # single-file application
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── data/                # runtime state (chat IDs, logs), created on first run
```

## Tuning

Constants at the top of `rlbot_reolink.py`:

- `EVENT_COOLDOWN` — seconds between notifications of the same type (default `20.0`)
- `SNAPSHOT_DELAY` — pause before snapshot so the subject is in frame (default `0.3`)
- `SNAPSHOT_RETRIES` — retries if the camera is briefly busy (default `2`)
- `RECONNECT_DELAY` / `MAX_RECONNECT_DELAY` — reconnect backoff bounds

## Running without Docker

```bash
pip install -r requirements.txt
export CAMERA_HOST=192.168.1.72 CAMERA_USER=admin CAMERA_PASS=...
export BOT_TOKEN=... REGISTER_PASSWORD=...
python rlbot_reolink.py
```

## Credits

Uses [`reolink_aio`](https://github.com/starkillerOG/reolink_aio), which implements the Baichuan TCP push protocol.
