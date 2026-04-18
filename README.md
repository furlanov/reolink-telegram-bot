# Reolink Telegram Bot

Telegram notifications with snapshots for Reolink cameras. Self-hosted, no Reolink cloud, no NVR, no Home Assistant.

Connects directly to the camera over the Reolink Baichuan TCP push protocol, so alerts arrive sub-second without polling or email relay.

The camera itself does all the detection. The bot just listens for push events the camera sends, grabs a snapshot, and forwards it.

> **Tested on:** Reolink **RLC-810A** only. Other Reolink cameras that speak Baichuan and report detection events should work, but nothing else has been verified.

---

## Features

- Sub-second push via Reolink's native TCP push (Baichuan), not email or polling.
- Forwards the camera's detection categories: person, vehicle, animal (dog/cat), and motion.
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
2. Camera pushes whenever its on-board detection fires.
3. Bot pulls a JPEG snapshot and broadcasts it to every registered Telegram chat.
4. A long-polling Telegram handler manages `/start`, `/stop`, `/snap`, etc.

## Setup

### 1. Create a Telegram bot

Message [@BotFather](https://t.me/BotFather) and send `/newbot`. Follow the prompts to pick a name and username. BotFather replies with a bot token — keep it for the next step.

### 2. Clone the repo

```bash
git clone https://github.com/furlanov/reolink-telegram-bot.git
cd reolink-telegram-bot
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```
CAMERA_HOST=192.168.1.72          # your camera's LAN IP
CAMERA_USER=admin                 # camera login (default is admin)
CAMERA_PASS=your_camera_password
BOT_TOKEN=123456:ABC-...          # from BotFather
REGISTER_PASSWORD=pick_something  # shared password users send to /start
```

Pick any value you like for `REGISTER_PASSWORD` — it's the shared secret that gates who can subscribe to notifications.

### 4. Start it

```bash
docker compose up -d
docker logs -f rlbot
```

You should see `Connected to RLC-810A` and `Subscribed to TCP push events`. If not, check the log line — most issues are wrong camera credentials or an unreachable camera IP.

### 5. Subscribe in Telegram

Open a chat with the bot you created and send:

```
/start your_register_password
```

The bot replies `Registered`. Trigger a detection in front of the camera — you should get a snapshot within a second. You're done.

## Configuration reference

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
