# Reolink Telegram Bot

Telegram notifications with snapshots for Reolink cameras. Self-hosted, no Reolink cloud, no NVR, no Home Assistant.

Connects directly to the camera over the Reolink Baichuan TCP push protocol, so alerts arrive sub-second without polling or email relay.

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
- Docker Compose deploy.

## How it works

```
┌──────────────┐   TCP push    ┌────────┐   sendPhoto   ┌──────────┐
│ Reolink cam  │ ────────────▶ │ rlbot  │ ────────────▶ │ Telegram │
│ (Baichuan)   │   snapshot    │        │   snapshot    │  chats   │
└──────────────┘ ◀──────────── └────────┘               └──────────┘
```

1. Bot connects to the camera on port 443 (HTTPS) and subscribes to TCP push events on port 9000.
2. Camera pushes whenever its on-board detection fires.
3. Bot pulls a JPEG snapshot and broadcasts it to every registered Telegram chat.
4. A long-polling Telegram handler manages `/start`, `/stop`, `/snap`, etc.

## Setup

### 1. Enable the required services on the camera

In the Reolink web UI or mobile app, make sure the following are on (menu names are from the RLC-810A; other models are similar):

- **HTTPS enabled (recommended).** `Settings → Network → Advanced → Server Settings → HTTPS`. The bot defaults to port 443 + HTTPS so the camera password isn't sent in cleartext over your LAN. If you can't enable HTTPS, set `CAMERA_USE_HTTPS=false` in `.env` to fall back to HTTP on port 80.
- **Motion detection enabled.** `Settings → Surveillance → Motion Detection`.
- **Smart detection enabled** for whichever categories you want alerts on — person, vehicle, animal. `Settings → Surveillance → Smart Detection` (also called "AI Detection" on some firmwares).
- **A user account with admin rights**, since the login needs to read snapshots and receive push. The default `admin` account works.

TCP push itself is always on as part of the camera's native protocol — there's no separate toggle for it.

### 2. Create a Telegram bot

Message [@BotFather](https://t.me/BotFather) and send `/newbot`. Follow the prompts to pick a name and username. BotFather replies with a bot token — keep it for the next step.

### 3. Clone the repo

```bash
git clone https://github.com/furlanov/reolink-telegram-bot.git
cd reolink-telegram-bot
```

### 4. Configure

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

### 5. Start it

```bash
docker compose up -d
docker logs -f rlbot
```

You should see `Connected to RLC-810A` and `Subscribed to TCP push events`. If not, check the log line — most issues are wrong camera credentials or an unreachable camera IP.

### 6. Subscribe in Telegram

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
| `CAMERA_USE_HTTPS`  |          | `true` (default) or `false`. Turn off only if the camera can't serve HTTPS. |
| `CAMERA_PORT`       |          | Override camera port. Defaults to `443` with HTTPS, `80` without. |
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
