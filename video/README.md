# Telegram Video Delivery System

A production-ready, two-bot Telegram automation system for video content delivery with anti-ban architecture.

## Overview

This system extracts TaraBox links from a private Telegram channel, converts them to videos via a third-party converter bot, archives them in a private channel, and delivers them to users through a secure two-bot architecture.

## Architecture

```
PRIVATE SOURCE CHANNEL
        ↓
Telethon Scraper (Userbot)
        ↓
Extract TaraBox Links → SQLite Database
        ↓
Converter Service (Queue-based)
        ↓
Third-party Converter Bot
        ↓
Receive Telegram VIDEO
        ↓
Archive Service (copy_message)
        ↓
PRIVATE ARCHIVE CHANNEL
        ↓
Main Bot (/start?id) → Secure Token
        ↓
Delivery Bot (/start=token) → copy_message()
        ↓
User receives video
```

## Two-Bot Design (Anti-Ban)

| Bot | Role | Sends Media? | Risk Level |
|-----|------|-------------|------------|
| **Main Bot** | Public entry, handles /start, membership, redirects | ❌ NEVER | Low |
| **Delivery Bot** | Isolated video sender via copy_message() | ✅ Yes | Medium (replaceable) |

**Why two bots?**
- Main bot never touches media → cannot be banned for content
- Delivery bot is isolated → can be replaced instantly if banned
- No forward attribution → copy_message() creates fresh messages
- Rate limiting and token expiry prevent abuse

## Folder Structure

```
project/
├── bots/
│   ├── main_bot.py          # Safe bot: handles users, never sends video
│   └── delivery_bot.py      # Isolated bot: sends videos via copy_message()
│
├── services/
│   ├── scraper.py           # Telethon userbot scraper
│   ├── converter.py         # Queue-based converter bot integration
│   ├── archive.py           # Private archive channel manager
│   ├── router.py            # Pipeline coordinator
│   └── membership.py        # Channel membership verification
│
├── database/
│   ├── db.py                # Async SQLite layer (aiosqlite)
│   └── schema.sql           # Database schema
│
├── utils/
│   ├── config.py            # Environment configuration
│   ├── logger.py            # Structured logging (structlog)
│   └── tokens.py            # Signed, expiring token system
│
├── sessions/
│   └── telegram.session     # Telethon session (keep persistent!)
│
├── .env                     # Environment variables (not in git)
├── .env.example             # Example configuration
├── requirements.txt         # Python dependencies
├── run.py                   # Main entry point
└── README.md                # This file
```

## Prerequisites

- Python 3.11+
- Telegram API ID and Hash (from https://my.telegram.org)
- Phone number for Telethon user authentication
- Two Telegram bot tokens (from @BotFather)
- Private source channel ID (numeric, with -100 prefix)
- Private archive channel ID (numeric, with -100 prefix)
- Converter bot username

## Setup Instructions

### 1. Clone/Create Project

```bash
mkdir telegram_video_system
cd telegram_video_system
```

### 2. Create Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
nano .env  # or your preferred editor
```

Fill in ALL required variables:

```env
API_ID=12345678
API_HASH=your_api_hash
PHONE_NUMBER=+1234567890

MAIN_BOT_TOKEN=123456:ABC...
DELIVERY_BOT_TOKEN=123456:XYZ...

SOURCE_CHANNEL=-1001234567890
ARCHIVE_CHANNEL=-1001234567891

CONVERTER_BOT=converter_bot_username

TOKEN_SECRET=$(openssl rand -hex 32)
```

### 5. Initialize Database

The database is auto-initialized on first run via `schema.sql`.

### 6. First Run - Authenticate Telethon

On first run, Telethon will ask for a verification code:

```bash
python run.py
```

You\'ll see:
```
Please enter your phone (or bot token): +1234567890
Please enter the code you received: 12345
```

Enter the code sent to your Telegram app. The session is saved to `sessions/telegram.session`.

### 7. Add Bots to Channels

**Main Bot:**
- Add as admin to source channel (for membership checks, optional)

**Delivery Bot:**
- Add as admin to archive channel (required for copy_message)
- Must have "Post Messages" permission in archive channel

**Your User Account:**
- Must be member of source channel
- Must be admin of archive channel

### 8. Production Deployment

**Using systemd:**

Create `/etc/systemd/system/telegram-video.service`:

```ini
[Unit]
Description=Telegram Video Delivery System
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/project
Environment=PYTHONPATH=/path/to/project
ExecStart=/path/to/project/venv/bin/python run.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable telegram-video
sudo systemctl start telegram-video
sudo systemctl status telegram-video
```

**Using screen/tmux (simple):**

```bash
screen -S video_bot
source venv/bin/activate
python run.py
# Ctrl+A, D to detach
```

## How It Works

### 1. Scraping

The Telethon scraper reads your private source channel continuously:
- On first run: processes ALL historical messages
- On subsequent runs: only new messages since last check
- Extracts TaraBox links from text, captions, and hidden URLs
- Saves links to database with `status=pending`

### 2. Conversion

The router finds pending videos and enqueues them:
- Queue ensures only ONE conversion at a time
- Sends TaraBox link to converter bot
- Waits up to 5 minutes for video response
- Handles timeouts, retries (max 3), and FloodWait

### 3. Archiving

When video is received from converter:
- Immediately copied to archive channel via `copy_message()`
- `copy_message()` creates a fresh message without "Forwarded from" attribution
- Archive message ID saved to database
- Status updated to `ready`

### 4. User Access

**Main Bot flow:**
1. User clicks `/start 123` from channel post
2. Main bot validates user (membership, rate limit)
3. Checks video is `ready` in database
4. Generates signed, 10-minute expiring token
5. Sends inline button with deep link to Delivery Bot

**Delivery Bot flow:**
1. User clicks link: `https://t.me/DeliveryBot?start=<token>`
2. Delivery bot validates token (signature + expiry)
3. Looks up `archive_message_id` from database
4. Sends video via `copy_message(from_chat_id=archive, message_id=...)`
5. Increments request count and logs delivery

## Security Features

- **Signed tokens**: HMAC-SHA256 prevents tampering
- **Token expiry**: 10-minute lifetime prevents replay attacks
- **Rate limiting**: Per-user hourly limits (configurable)
- **Membership checks**: Verify users are in required channels
- **No ID exposure**: Raw database IDs never exposed to users
- **Audit logging**: All deliveries logged with user_id, video_id, success/failure

## Anti-Ban Strategy

| Measure | Implementation |
|---------|---------------|
| Main bot never sends media | Only text + inline buttons |
| Delivery bot isolated | Separate token, replaceable |
| No forward attribution | `copy_message()` instead of `forward_message()` |
| Private archive | Channel is private, not discoverable |
| Rate limiting | Per-user limits + Telegram retry handling |
| Queue-based conversion | Only 1 conversion at a time |
| FloodWait handling | Exponential backoff everywhere |
| Token expiry | Prevents link scraping and abuse |

## Database Schema

### videos
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Internal ID |
| tarabox_link | TEXT UNIQUE | Original link |
| archive_message_id | INTEGER | Archive channel message ID |
| status | TEXT | pending/processing/ready/failed |
| request_count | INTEGER | Delivery count |
| created_at | DATETIME | When added |

### scraper_state
| Column | Type | Description |
|--------|------|-------------|
| last_message_id | INTEGER | Last processed message |

### delivery_bots
| Column | Type | Description |
|--------|------|-------------|
| bot_username | TEXT | For future multi-bot scaling |
| is_active | BOOLEAN | Bot availability |
| total_deliveries | INTEGER | Usage stats |

### delivery_logs
| Column | Type | Description |
|--------|------|-------------|
| video_id | INTEGER FK | Which video |
| user_id | INTEGER | Who requested |
| success | BOOLEAN | Delivery result |
| delivered_at | DATETIME | Timestamp |

## Monitoring

Check logs for:
- `scraper_found_pending` - New videos discovered
- `converter_video_received` - Successful conversions
- `delivery_success` - Successful deliveries
- `delivery_rate_limited` - Rate limit events
- `converter_failed_permanently` - Failed conversions

## Scaling

### Multiple Delivery Bots

The `delivery_bots` table is ready for multi-bot deployment:

1. Register new bots: `INSERT INTO delivery_bots (bot_username, bot_token)`
2. Modify delivery bot to select from active bots
3. Distribute load round-robin or by availability

### PostgreSQL Migration

Replace `aiosqlite` with `asyncpg`:
1. Update `database/db.py` connection logic
2. Keep same schema (mostly compatible)
3. Update `requirements.txt`

### Multiple Converter Bots

If converter bot has rate limits:
1. Maintain a pool of converter bot usernames
2. Rotate between them in the converter service
3. Track which bot handled which video

## Troubleshooting

### "Not authorized" error
- Delete `sessions/telegram.session`
- Re-run and enter verification code

### "Bot is not a member of the channel"
- Add delivery bot to archive channel as admin
- Ensure bot has "Post Messages" permission

### "Video not found in archive"
- Check converter bot is responding
- Check archive channel permissions
- Review `converter_*` logs for errors

### Rate limit errors
- Normal behavior - system handles them automatically
- Increase `SCRAPE_INTERVAL` if needed
- Consider multiple converter bots

## License

MIT License - Use at your own risk. Ensure compliance with Telegram ToS and local laws.
