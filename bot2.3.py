"""
KKStories Telegram Bot
======================
Site    : https://kkstories.com
Library : pyTelegramBotAPI (telebot)
Python  : 3.10+  (works in Termux / Python 3.13)
Install : pip install pyTelegramBotAPI requests beautifulsoup4

Set your token in BOT_TOKEN below (or export as env-var KKBOT_TOKEN).
"""
from flask import Flask, request
import os
import re
import time
import schedule
import threading
import logging
from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageFilter
)
import textwrap
import random
from time import sleep

import requests
import telebot
from datetime import datetime
from telebot import types
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
ADMIN_KEY = os.getenv(
    "ADMIN_KEY"
)

# Admin whitelist: comma-separated Telegram IDs (e.g., "123456789,987654321")
# If set, ONLY these IDs can use admin commands (key still required as backup)
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]

# Alert admin on critical errors (Telegram ID)
ALERT_ADMIN_ID = os.getenv("ALERT_ADMIN_ID")
if ALERT_ADMIN_ID:
    ALERT_ADMIN_ID = int(ALERT_ADMIN_ID)
SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY"
)
CHANNEL_ID = int(
    os.getenv("CHANNEL_ID")
)

POST_INTERVAL_MINUTES = int(
    os.getenv(
        "POST_INTERVAL_MINUTES",
        120
    )
)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


PER_PAGE    = 10
REQ_TIMEOUT = 15          # seconds per API call
REQ_RETRIES = 3

JUNK_RE = re.compile(
    r"(PREVIOUS\s+PART|NEXT\s+PART|WWW\.KKSTORIES\.COM|KKSTORIES\.COM)",
    re.IGNORECASE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML",
    threaded=False
)

app = Flask(__name__)
# ──────────────────────────────────────────────────────────────────────────────
# NAVIGATION STATE  { chat_id: {...} }
# ──────────────────────────────────────────────────────────────────────────────
# Tracks the last list message so we can delete it when opening a story.
# Keys:
#   type   : "latest" | "category" | "search"
#   page   : int
#   cat_id : int   (category only)
#   query  : str   (search only)
#   msg_id : int   (message_id of current list message)
nav_state: dict[int, dict] = {}

# Pending search input  { chat_id: prompt_message_id }
search_pending: dict[int, int] = {}

# Story content cache: {post_id: {"pages": [...], "title": "...", "author_id": N, "fetched_at": timestamp}}
# TTL: 30 minutes — stories don't change often, saves API calls
story_content_cache: dict[int, dict] = {}


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: WordPress API
# ──────────────────────────────────────────────────────────────────────────────
def generate_story_cover(
    title: str
):

    width = 1080
    height = 1350

    bg_colors = [
        (15, 15, 15),
        (25, 15, 15),
        (20, 10, 10)
    ]

    bg_color = random.choice(
        bg_colors
    )

    img = Image.new(
        "RGB",
        (width, height),
        bg_color
    )

    draw = ImageDraw.Draw(img)

    # Red cinematic glow
    glow = Image.new(
        "RGBA",
        (width, height),
        (0, 0, 0, 0)
    )

    gdraw = ImageDraw.Draw(glow)

    gdraw.ellipse(
        (
            -200,
            300,
            900,
            1400
        ),
        fill=(170, 0, 0, 70)
    )

    glow = glow.filter(
        ImageFilter.GaussianBlur(
            120
        )
    )

    img = Image.alpha_composite(
        img.convert("RGBA"),
        glow
    ).convert("RGB")

    draw = ImageDraw.Draw(img)

    try:

       title_font = (
        ImageFont.truetype(
            "fonts/NotoSansMalayalam-Regular.ttf",
            64
        )
       )

    except Exception as e:

           print(
                "title font error:",
                e
            )

           title_font = (
           ImageFont.load_default()
           )

    try:

      subtitle_font = (
        ImageFont.truetype(
            "fonts/NotoSansMalayalam-Regular.ttf",
            30
        )
      )

    except Exception as e:

      print(
          "subtitle font error:",
          e
      )

      subtitle_font = (
        ImageFont.load_default()
      )

    wrapped = textwrap.fill(
        title,
        width=18
    )

    bbox = draw.textbbox(
        (0, 0),
        wrapped,
        font=title_font
    )

    text_width = (
        bbox[2] - bbox[0]
    )

    x = (
        width - text_width
    ) // 2

    y = 320

    draw.text(
        (x, y),
        wrapped,
        fill="white",
        font=title_font
    )

    draw.text(
        (80, 1100),
        "Read Full Story",
        fill=(180, 180, 180),
        font=subtitle_font
    )

    try:

        logo = Image.open(
            "logo.png"
        ).convert("RGBA")

        logo.thumbnail(
            (260, 260)
        )

        alpha = logo.getchannel(
            "A"
        )

        alpha = alpha.point(
            lambda p:
            int(p * 0.65)
        )

        logo.putalpha(alpha)

        lx = (
            width
            - logo.width
            - 40
        )

        ly = (
            height
            - logo.height
            - 40
        )

        img.paste(
            logo,
            (lx, ly),
            logo
        )

    except Exception as e:

        print(
            "logo error:",
            e
        )

    out = (
        "/tmp/story_cover.jpg"
    )

    img.save(
        out,
        quality=95
    )

    return out

def extract_teaser(
    html_content,
    title=""
):

    content = BeautifulSoup(
        html_content,
        "html.parser"
    ).get_text("\n")

    # Remove junk
    content = re.sub(
        r"\[.*?\]",
        "",
        content
    )

    content = re.sub(
        r"www\.[^\s]+",
        "",
        content
    )

    content = re.sub(
        r"Part\s+\d+",
        "",
        content,
        flags=re.I
    )

    content = re.sub(
        r"Author\s*:.*",
        "",
        content,
        flags=re.I
    )

    # Remove repeated title
    if title:

        content = re.sub(
            re.escape(
                title.strip()
            ),
            "",
            content,
            count=1,
            flags=re.I
        )

    # Remove romanized title line
    content = re.sub(
        r"^[A-Za-z0-9\s|:,'\-]+$",
        "",
        content,
        flags=re.M
    )

    # Remove summary heading
    if "കഥാ സംഗ്രഹം" in content:

        content = content.split(
            "കഥാ സംഗ്രഹം",
            1
        )[1]

    # Clean line-by-line
    lines = []

    for line in content.splitlines():

        line = line.strip()

        # skip empty / tiny junk
        if len(line) < 15:
            continue

        # normalize spaces
        line = re.sub(
            r"\s+",
            " ",
            line
        )

        lines.append(
            line
        )

    # Keep only first meaningful lines
    teaser = "\n\n".join(
        lines[:8]
    )

    # Safety limit for Telegram
    teaser = teaser[:400]

    return teaser

def batch_check_posted(post_ids: list[int]) -> set[int]:
    """
    Check which post IDs are already posted in a SINGLE Supabase query.
    Returns set of posted post_ids.
    """
    if not post_ids:
        return set()

    try:
        # Build comma-separated list for Supabase "in" filter
        ids_str = ",".join(str(pid) for pid in post_ids)

        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/posted_stories"
            f"?post_id=in.({ids_str})"
            f"&select=post_id",
            headers=HEADERS,
            timeout=10
        )

        data = res.json()
        return {item["post_id"] for item in data}

    except Exception as e:
        print("batch_check_posted error:", e)
        report_db_error("batch_check_posted", str(e))
        # Fallback: empty set (will try all posts individually)
        return set()


def get_new_story():
    """
    Get newest unposted story from page 1.
    Batches the posted check to avoid N+1 Supabase queries.
    """
    # Fetch 50 posts per page (WordPress max)
    posts, _ = fetch_posts(page=1, per_page=50)

    if not posts:
        return None

    # Batch check: get all posted IDs in ONE query
    post_ids = [p["id"] for p in posts]
    posted_ids = batch_check_posted(post_ids)

    # Return first unposted
    for post in posts:
        if post["id"] not in posted_ids:
            return post

    return None
    



def get_random_story():
    """
    Get random unposted story.
    Batches the posted check and retries on different pages if needed.
    """
    # Try up to 3 random pages
    for _ in range(3):
        page = random.randint(1, 30)

        try:
            posts, total_pages = fetch_posts(page=page, per_page=50)
        except Exception as e:
            print(f"random fetch error page {page}:", e)
            continue

        if not posts:
            continue

        random.shuffle(posts)

        # Batch check posted status
        post_ids = [p["id"] for p in posts]
        posted_ids = batch_check_posted(post_ids)

        for post in posts:
            if post["id"] not in posted_ids:
                return post

    return None    
    
    
    
def today_post_count() -> int:
    """
    Count posts made today (UTC).
    Uses explicit UTC to avoid timezone ambiguity.
    """
    today = datetime.utcnow().date().isoformat()

    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/posted_stories"
            f"?posted_date=eq.{today}"
            f"&select=id",
            headers=HEADERS,
            timeout=10
        )
        return len(res.json())
    except Exception as e:
        print("today_post_count error:", e)
        report_db_error("today_post_count", str(e))
        return 0    




def is_story_posted(
    post_id: int
) -> bool:

    try:

        res = requests.get(
            f"{SUPABASE_URL}"
            "/rest/v1/"
            "posted_stories"
            f"?post_id=eq.{post_id}"
            "&select=id",
            headers=HEADERS,
            timeout=10
        )

        return len(
            res.json()
        ) > 0

    except Exception as e:

        print(
            "is_story_posted:",
            e
        )

        return False


def scheduler_loop():
    """
    Main scheduler with jitter to avoid predictable posting times.
    Catches and reports errors to prevent silent failures.
    """
    # Initial schedule
    _schedule_next_post()
    _schedule_new_user_flush()
    _schedule_daily_summary()

    consecutive_errors = 0
    max_consecutive_errors = 10

    while True:
        try:
            schedule.run_pending()
            consecutive_errors = 0  # Reset on success
        except Exception as e:
            consecutive_errors += 1
            error_msg = f"Scheduler error ({consecutive_errors}/{max_consecutive_errors}): {str(e)[:200]}"
            print(error_msg)

            if consecutive_errors >= max_consecutive_errors:
                report_scheduler_error(
                    f"Scheduler failed {consecutive_errors} times in a row. "
                    f"Auto-posting may be stopped. Last error: {str(e)[:200]}"
                )
                consecutive_errors = 0  # Reset after alerting

            time.sleep(5)  # Brief pause before retry
            continue

        time.sleep(30)


def _schedule_next_post():
    """
    Schedule next post with random jitter (±30 minutes).
    This makes posting times look natural instead of robotic.
    """
    # Base interval from env, default 120 min
    base_minutes = POST_INTERVAL_MINUTES

    # Add jitter: ±30 minutes (but not less than 30 min total)
    jitter = random.randint(-30, 30)
    next_interval = max(30, base_minutes + jitter)

    schedule.clear("posting")  # Clear old posting job
    schedule.every(next_interval).minutes.tag("posting").do(_post_with_reschedule)

    print(f"[SCHEDULER] Next post in {next_interval} minutes (base: {base_minutes}, jitter: {jitter})")


def _post_with_reschedule():
    """
    Run posting job, then schedule next one with fresh jitter.
    """
    check_new_stories()
    _schedule_next_post()        
def mark_story_posted(post_id: int, post_type: str = "new") -> bool:
    """
    Mark story as posted with retry and verification.
    Returns True if successful, False otherwise.
    """
    max_retries = 3

    for attempt in range(max_retries):
        try:
            # Insert into posted_stories
            res = requests.post(
                f"{SUPABASE_URL}/rest/v1/posted_stories",
                headers=HEADERS,
                json={
                    "post_id": post_id,
                    "type": post_type,
                    "posted_date": str(datetime.now().date())
                },
                timeout=10
            )

            # Check if insert succeeded
            if res.status_code in (200, 201):
                # Verify: query back to confirm
                verify = requests.get(
                    f"{SUPABASE_URL}/rest/v1/posted_stories"
                    f"?post_id=eq.{post_id}"
                    f"&select=id",
                    headers=HEADERS,
                    timeout=10
                )

                if verify.status_code == 200 and len(verify.json()) > 0:
                    print(f"Marked post {post_id} as posted (verified)")
                    return True

            print(f"mark_story_posted attempt {attempt + 1} failed: HTTP {res.status_code}")

        except Exception as e:
            print(f"mark_story_posted attempt {attempt + 1} error:", e)

        # Wait before retry
        if attempt < max_retries - 1:
            time.sleep(1)

    error_msg = f"Failed to mark post {post_id} after {max_retries} attempts"
    print(f"CRITICAL: {error_msg}")
    report_db_error("mark_story_posted", error_msg)
    return False
def post_story_to_channel(post) -> bool:
    """
    Post story to channel with full error handling and verification.
    Returns True if posted successfully, False otherwise.
    """
    try:
        post_id = post["id"]

        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()

        # Get FULL post
        full_post, _ = _wp_get(f"posts/{post_id}")

        # Pre-cache the story content for instant bot reads
        content_html = full_post.get("content", {}).get("rendered", "")
        author_id = fetch_real_author_id(post_id, content_html)
        _cache_story(post_id, full_post["title"]["rendered"], content_html, author_id)

        teaser = extract_teaser(content_html, title)

        poster = "logo.png"

        text = f"""
📖 <b>{title}</b>

{teaser}

<i>ബാക്കി കഥ വായിക്കാൻ താഴെ ക്ലിക്ക് ചെയ്യൂ 👇</i>

🔞 Previous parts available in bot
"""

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            "📖 കഥ വായിക്കൂ",
            url=f"https://t.me/kambikathaa_bot?start=story_{post_id}"
        ))

        # Send to channel
        bot.send_photo(
            CHANNEL_ID,
            photo=open(poster, "rb"),
            caption=text,
            parse_mode="HTML",
            reply_markup=kb
        )

        # Mark as posted with verification
        marked = mark_story_posted(post_id, "new")

        if not marked:
            # Posting succeeded but marking failed — alert admin
            error_msg = f"Posted {post_id} but failed to mark as posted"
            print(f"CRITICAL: {error_msg}")
            report_posting_failure(post_id, "Marked as posted failed after verification")
            return False

        print(f"Posted story {post_id}")
        return True

    except Exception as e:
        print("post_story_to_channel:", repr(e))
        return False
    
def check_new_stories():
    """
    Main posting job with full error handling.
    Only counts toward daily limit if posting actually succeeds.
    """
    try:
        # Check daily limit
        if today_post_count() >= 20:
            print("Daily limit reached")
            return

        # Select story
        post = choose_story_to_post()
        if not post:
            print("No story found")
            return

        print("POSTING:", post["id"])

        # Post to channel
        success = post_story_to_channel(post)

        if not success:
            print(f"Posting failed for {post['id']}, not counting toward daily limit")
            return

    except Exception as e:
        print("check_new_stories:", repr(e))
        report_scheduler_error(f"check_new_stories crashed: {repr(e)[:200]}")
def choose_story_to_post():
    """
    Select story type with weighted random:
    - 50% new story (fresh content from latest)
    - 50% random story (older content for variety)
    """
    r = random.random()

    if r < 0.5:
        return get_new_story()

    return get_random_story()
    
    
def track_user(user):
    """
    Create/update user.
    """

    data = {
        "telegram_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_seen": datetime.utcnow().isoformat()
    }

    try:
        # Check if user exists first (to detect new users)
        check_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{user.id}&select=id",
            headers=HEADERS,
            timeout=10
        )
        is_new = len(check_res.json()) == 0

        requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={
                **HEADERS,
                "Prefer": "resolution=merge-duplicates"
            },
            json=data,
            timeout=10
        )

        # Report new user
        if is_new:
            report_new_user(user)

    except Exception as e:
        print("track_user:", e)
def increment_user_reads(user_id: int):

    try:
        # Get current reads
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/users"
            f"?telegram_id=eq.{user_id}"
            f"&select=total_reads",
            headers=HEADERS,
            timeout=10
        )

        data = res.json()

        current_reads = 0

        if data:
            current_reads = data[0].get(
                "total_reads", 0
            )

        # Update reads
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/users"
            f"?telegram_id=eq.{user_id}",
            headers=HEADERS,
            json={
                "total_reads":
                current_reads + 1
            },
            timeout=10
        )

    except Exception as e:
        print("increment_user_reads:", e)
def track_event(user_id: int,
                event_type: str,
                value: str = None):

    data = {
        "telegram_id": user_id,
        "event_type": event_type,
        "value": value
    }

    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/events",
            headers=HEADERS,
            json=data,
            timeout=10
        )

    except Exception as e:
        print("track_event:", e)
def track_story_read(post_id: int, title: str):

    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/story_stats",
            headers={
                **HEADERS,
                "Prefer": "resolution=merge-duplicates"
            },
            json={
                "post_id": post_id,
                "title": title
            },
            timeout=10
        )

        requests.patch(
            f"{SUPABASE_URL}/rest/v1/story_stats?post_id=eq.{post_id}",
            headers=HEADERS,
            json={
                "reads": 1
            }
        )

    except Exception as e:
        print("track_story_read:", e)
# TTL Cache: entries expire after 5 minutes, max 10000 entries
# Uses OrderedDict for O(1) eviction of oldest entries when at capacity
from collections import OrderedDict

class TTLCache:
    def __init__(self, maxsize=10000, ttl=300):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache = OrderedDict()

    def _cleanup(self):
        now = time.time()
        expired = [k for k, v in self._cache.items() if now - v > self.ttl]
        for k in expired:
            del self._cache[k]

    def get(self, key):
        self._cleanup()
        if key in self._cache:
            now = time.time()
            if now - self._cache[key] < self.ttl:
                return self._cache[key]
            else:
                del self._cache[key]
        return None

    def set(self, key):
        self._cleanup()
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self.maxsize:
            self._cache.popitem(last=False)
        self._cache[key] = time.time()

story_read_cache = TTLCache(maxsize=10000, ttl=300)

def can_count_story_read(user_id, post_id):
    key = f"{user_id}_{post_id}"
    if story_read_cache.get(key) is not None:
        return False
    story_read_cache.set(key)
    return True
    

# ──────────────────────────────────────────────────────────────────────────────
# ADMIN AUTHENTICATION
# ──────────────────────────────────────────────────────────────────────────────

def is_admin(message) -> bool:
    """
    Check if user is authorized admin.
    Priority:
    1. Telegram ID in ADMIN_IDS whitelist
    2. ADMIN_KEY provided (legacy, for flexibility)
    """
    user_id = message.from_user.id

    # Whitelist check (preferred)
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True

    # Key check (legacy fallback)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) >= 2 and parts[1] == ADMIN_KEY:
        return True

    return False


def require_admin(message) -> bool:
    """
    Check admin access and send denial if not authorized.
    Returns True if authorized.
    """
    if is_admin(message):
        return True

    bot.reply_to(message, "🚫 <b>Access Denied</b>\nYou are not authorized to use admin commands.", parse_mode="HTML")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# ADMIN REPORTING SYSTEM
# ──────────────────────────────────────────────────────────────────────────────

# Throttle alerts: {alert_type: last_sent_timestamp}
_alert_throttle: dict[str, float] = {}
_ALERT_THROTTLE_SECONDS = 300  # 5 minutes between same alert type

def alert_admin(text: str, alert_type: str = "general", force: bool = False) -> None:
    """
    Send critical alert to admin via DM with throttling.

    Args:
        text: Alert message text
        alert_type: Category for throttling (e.g., "posting", "webhook", "db_error")
        force: Send even if throttled
    """
    if not ALERT_ADMIN_ID:
        return

    now = time.time()

    # Check throttle
    if not force:
        last_sent = _alert_throttle.get(alert_type, 0)
        if now - last_sent < _ALERT_THROTTLE_SECONDS:
            log.warning("Alert throttled [%s]: %s", alert_type, text[:100])
            return

    _alert_throttle[alert_type] = now

    try:
        # Add timestamp and type badge
        timestamp = datetime.utcnow().strftime("%H:%M:%S UTC")
        type_emoji = {
            "posting": "📢",
            "webhook": "🌐",
            "db_error": "🗄️",
            "new_user": "👤",
            "broadcast": "📣",
            "scheduler": "⏰",
            "api_error": "⚡",
            "general": "🚨",
        }.get(alert_type, "🚨")

        formatted = f"""{type_emoji} <b>Bot Alert</b> <code>[{alert_type}]</code>
<i>{timestamp}</i>

{text}"""

        bot.send_message(
            ALERT_ADMIN_ID,
            formatted,
            parse_mode="HTML"
        )
    except Exception as e:
        log.error("Failed to alert admin: %s", e)


def report_new_user(user) -> None:
    """Report new user join to admin (throttled batch)."""
    if not ALERT_ADMIN_ID:
        return

    # Use daily batch key
    today = datetime.utcnow().date().isoformat()
    alert_type = f"new_user_{today}"

    # Always update a pending counter instead of sending immediately
    # We'll batch these
    _new_user_batch["count"] = _new_user_batch.get("count", 0) + 1
    _new_user_batch["last_user"] = user
    _new_user_batch["last_time"] = time.time()


# Pending new user batch for throttled reporting
_new_user_batch: dict = {"count": 0, "last_user": None, "last_time": 0}


def _flush_new_user_report() -> None:
    """Send batched new user report if any pending."""
    count = _new_user_batch.get("count", 0)
    if count == 0:
        return

    user = _new_user_batch.get("last_user")
    if user:
        name = user.first_name or "Unknown"
        username = f"@{user.username}" if user.username else "no username"
        text = (
            f"👤 <b>{count} new user(s)</b> today\n\n"
            f"Latest: <b>{name}</b> ({username})\n"
            f"🆔 <code>{user.id}</code>"
        )
    else:
        text = f"👤 <b>{count} new user(s)</b> joined today"

    alert_admin(text, alert_type="new_user", force=True)
    _new_user_batch["count"] = 0


def report_posting_failure(post_id: int, reason: str) -> None:
    """Report story posting failure to admin."""
    text = (
        f"📢 <b>Posting Failed</b>\n\n"
        f"Post ID: <code>{post_id}</code>\n"
        f"Reason: {reason}\n\n"
        f"<i>Story may need manual posting.</i>"
    )
    alert_admin(text, alert_type="posting")


def report_db_error(operation: str, error: str) -> None:
    """Report database/Supabase error to admin."""
    text = (
        f"🗄️ <b>Database Error</b>\n\n"
        f"Operation: <code>{operation}</code>\n"
        f"Error: <code>{str(error)[:200]}</code>\n\n"
        f"<i>Check Supabase status.</i>"
    )
    alert_admin(text, alert_type="db_error")


def report_webhook_error(error: str) -> None:
    """Report webhook processing error to admin."""
    text = (
        f"🌐 <b>Webhook Error</b>\n\n"
        f"<code>{str(error)[:300]}</code>\n\n"
        f"<i>Bot may be missing updates.</i>"
    )
    alert_admin(text, alert_type="webhook")


def report_scheduler_error(error: str) -> None:
    """Report scheduler failure to admin."""
    text = (
        f"⏰ <b>Scheduler Error</b>\n\n"
        f"<code>{str(error)[:300]}</code>\n\n"
        f"<i>Auto-posting may be stopped.</i>"
    )
    alert_admin(text, alert_type="scheduler")


def report_api_error(endpoint: str, error: str) -> None:
    """Report WordPress API error to admin."""
    text = (
        f"⚡ <b>WordPress API Error</b>\n\n"
        f"Endpoint: <code>{endpoint}</code>\n"
        f"Error: <code>{str(error)[:200]}</code>\n\n"
        f"<i>Site may be down.</i>"
    )
    alert_admin(text, alert_type="api_error")


def _send_daily_summary():
    """Send daily activity summary to admin."""
    try:
        today = datetime.utcnow().date().isoformat()

        # Get stats
        users = supabase_get("users?select=*")
        events = supabase_get(f"events?created_at=gte.{today}T00:00:00&select=*")
        posted = today_post_count()

        total_users = len(users)
        active_today = len([u for u in users if u.get("last_seen", "")[:10] == today])
        total_reads = sum(u.get("total_reads", 0) for u in users)
        total_searches = sum(u.get("total_searches", 0) for u in users)

        text = (
            f"📊 <b>Daily Summary</b> <code>{today}</code>\n\n"
            f"👥 Users: <b>{total_users}</b> (+{_new_user_batch.get('count', 0)} today)\n"
            f"🔥 Active Today: <b>{active_today}</b>\n"
            f"📖 Total Reads: <b>{total_reads}</b>\n"
            f"🔎 Searches: <b>{total_searches}</b>\n"
            f"📢 Posts Today: <b>{posted}/20</b>\n"
            f"📝 Events: <b>{len(events)}</b>\n\n"
            f"<i>Keep up the good work! 💪</i>"
        )

        alert_admin(text, alert_type="general", force=True)

    except Exception as e:
        print("daily summary error:", e)


def _schedule_daily_summary():
    """Schedule daily summary at 00:00 UTC."""
    schedule.every().day.at("00:00").do(_send_daily_summary)
    print("[SCHEDULER] Daily summary scheduled for 00:00 UTC")


def _schedule_new_user_flush():
    """Schedule periodic new user report flush."""
    schedule.every(1).hours.do(_flush_new_user_report)
    print("[SCHEDULER] New user flush scheduled every 1 hour")



@bot.message_handler(commands=["admin_posting_status"])
def admin_posting_status(message):
    """Check auto-posting scheduler status and next post time."""
    if not require_admin(message):
        return

    try:
        # Get next scheduled job
        next_jobs = [job for job in schedule.jobs if "posting" in str(job.tags)]

        if next_jobs:
            next_run = next_jobs[0].next_run
            if next_run:
                time_until = next_run - datetime.now()
                hours, remainder = divmod(int(time_until.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                next_text = f"{hours}h {minutes}m"
            else:
                next_text = "Unknown"
        else:
            next_text = "Not scheduled"

        # Today's stats
        today_count = today_post_count()
        remaining = max(0, 20 - today_count)

        text = f"""📢 <b>Posting Status</b>

⏰ Next post: <b>{next_text}</b>
📅 Today posted: <b>{today_count}/20</b>
📊 Remaining today: <b>{remaining}</b>
🔄 Interval base: <b>{POST_INTERVAL_MINUTES} min</b>
"""

        bot.send_message(message.chat.id, text, parse_mode="HTML")

    except Exception as e:
        print("posting status error:", e)
        bot.reply_to(message, "⚠️ Failed to get posting status")



@bot.message_handler(commands=["admin_post_now"])
def admin_post_now(message):
    """Force immediate post to channel."""
    if not require_admin(message):
        return

    try:
        loading = bot.send_message(message.chat.id, "⏳ Forcing post...")

        # Run posting logic directly
        post = choose_story_to_post()

        if not post:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading.message_id,
                text="❌ No unposted stories available."
            )
            return

        success = post_story_to_channel(post)

        if success:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading.message_id,
                text=f"✅ Posted story <b>{post['id']}</b> to channel!"
            )
        else:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=loading.message_id,
                text="❌ Posting failed. Check logs."
            )

    except Exception as e:
        print("admin post now error:", e)
        bot.reply_to(message, "⚠️ Failed to force post")



@bot.message_handler(commands=["admin_top_stories"])
def admin_top_stories(message):
    """Show most-read stories from story_stats."""
    if not require_admin(message):
        return

    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/story_stats"
            f"?select=post_id,title,reads"
            f"&order=reads.desc"
            f"&limit=20",
            headers=HEADERS,
            timeout=10
        )

        stats = res.json()

        if not stats:
            bot.reply_to(message, "📊 No story data yet.")
            return

        text = "📚 <b>Top Stories</b>\n\n"

        for i, stat in enumerate(stats, 1):
            title = stat.get("title", "Unknown")[:40]
            reads = stat.get("reads", 0)
            text += f"<b>{i}.</b> {title}...\n   👁 {reads} reads\n\n"

        bot.send_message(message.chat.id, text[:4000], parse_mode="HTML")

    except Exception as e:
        print("top stories error:", e)
        bot.reply_to(message, "⚠️ Failed to load top stories")



@bot.message_handler(commands=["admin_daily_stats"])
def admin_daily_stats(message):
    """Show today's hourly activity breakdown."""
    if not require_admin(message):
        return

    try:
        today = datetime.utcnow().date().isoformat()

        # Get today's events
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/events"
            f"?created_at=gte.{today}T00:00:00"
            f"&select=event_type,created_at"
            f"&order=created_at.desc",
            headers=HEADERS,
            timeout=10
        )

        events = res.json()

        if not events:
            bot.reply_to(message, "📊 No activity today.")
            return

        # Count by hour
        hourly = {}
        event_types = {}

        for e in events:
            hour = e.get("created_at", "")[11:13] if e.get("created_at") else "??"
            hourly[hour] = hourly.get(hour, 0) + 1

            etype = e.get("event_type", "unknown")
            event_types[etype] = event_types.get(etype, 0) + 1

        # Build hourly chart
        text = f"📊 <b>Today's Activity</b> ({today})\n\n"

        text += "<b>By Hour:</b>\n"
        for hour in sorted(hourly.keys()):
            bar = "█" * min(hourly[hour], 20)
            text += f"{hour}:00 {bar} {hourly[hour]}\n"

        text += "\n<b>By Type:</b>\n"
        for etype, count in sorted(event_types.items(), key=lambda x: -x[1]):
            emoji = {"open_story": "📖", "search": "🔎", "failed_search": "❌"}.get(etype, "⚡")
            text += f"{emoji} {etype}: {count}\n"

        bot.send_message(message.chat.id, text[:4000], parse_mode="HTML")

    except Exception as e:
        print("daily stats error:", e)
        bot.reply_to(message, "⚠️ Failed to load daily stats")



@bot.message_handler(commands=["admin_user"])
def admin_user_detail(message):
    """Deep dive on specific user by Telegram ID."""
    if not require_admin(message):
        return

    try:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(message, "Usage: <code>/admin_user [TELEGRAM_ID]</code>", parse_mode="HTML")
            return

        user_id = int(parts[1])

        # Get user info
        users = supabase_get(f"users?telegram_id=eq.{user_id}&select=*")
        if not users:
            bot.reply_to(message, f"❌ User <code>{user_id}</code> not found.", parse_mode="HTML")
            return

        u = users[0]

        # Get recent events
        events = supabase_get(f"events?telegram_id=eq.{user_id}&select=*&order=id.desc&limit=20")

        # Get story reads
        story_reads = supabase_get(f"story_stats?select=title,reads&limit=10")

        text = f"""👤 <b>User Detail</b>

🆔 <code>{user_id}</code>
👤 Name: {u.get('first_name', 'Unknown')}
📛 Username: @{u.get('username', '-')}
📖 Total Reads: {u.get('total_reads', 0)}
🔎 Total Searches: {u.get('total_searches', 0)}
📅 Last Seen: {u.get('last_seen', 'Never')[:16]}

<b>Recent Activity ({len(events)} events):</b>
"""

        for e in events[:10]:
            etype = e.get("event_type", "unknown")
            value = e.get("value", "")[:30]
            emoji = {"open_story": "📖", "search": "🔎", "failed_search": "❌"}.get(etype, "⚡")
            text += f"{emoji} {etype}: {value}\n"

        bot.send_message(message.chat.id, text[:4000], parse_mode="HTML")

    except Exception as e:
        print("admin user detail error:", e)
        bot.reply_to(message, "⚠️ Failed to load user detail")



@bot.message_handler(commands=["admin_broadcast"])
def admin_broadcast(message):
    """Broadcast message to all users."""
    if not require_admin(message):
        return

    try:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(message, "Usage: <code>/admin_broadcast [MESSAGE]</code>\n\n⚠️ This sends to ALL users. Be careful!", parse_mode="HTML")
            return

        broadcast_text = parts[1]

        # Store full message in pending dict (callback data has 64-byte limit)
        broadcast_pending[message.chat.id] = broadcast_text

        # Confirm first
        confirm_kb = types.InlineKeyboardMarkup()
        confirm_kb.row(
            types.InlineKeyboardButton("✅ Confirm Send", callback_data="broadcast_confirm"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")
        )

        preview = f"📢 <b>Broadcast Preview</b>\n\n{broadcast_text[:200]}...\n\n<i>Click confirm to send to all users.</i>"
        bot.send_message(message.chat.id, preview, reply_markup=confirm_kb, parse_mode="HTML")

    except Exception as e:
        print("broadcast error:", e)
        bot.reply_to(message, "⚠️ Broadcast setup failed")


@bot.message_handler(commands=["admin_message"])
def admin_message_user(message):
    """Send message to specific user by Telegram ID."""
    if not require_admin(message):
        return

    try:
        parts = message.text.strip().split(maxsplit=2)
        if len(parts) < 3:
            bot.reply_to(
                message,
                "Usage: <code>/admin_message [USER_ID] [YOUR MESSAGE]</code>\n\n"
                "Example: <code>/admin_message 123456789 Hello!</code>",
                parse_mode="HTML"
            )
            return

        target_id = int(parts[1])
        msg_text = parts[2]

        # Get user info for confirmation
        users = supabase_get(f"users?telegram_id=eq.{target_id}&select=*")
        user_name = users[0].get("first_name", "Unknown") if users else "Unknown"

        # Send message
        bot.send_message(
            target_id,
            f"📩 <b>Message from Admin</b>\n\n{msg_text}",
            parse_mode="HTML"
        )

        bot.reply_to(
            message,
            f"✅ Message sent to <b>{user_name}</b> (<code>{target_id}</code>)",
            parse_mode="HTML"
        )

    except Exception as e:
        print("admin_message error:", e)
        bot.reply_to(message, "⚠️ Failed to send message. User may have blocked the bot.")


@bot.message_handler(
    commands=["admin"]
)
def admin_panel(message):
    """Main admin dashboard with overview analytics."""
    if not require_admin(message):
        return

    try:

        users = supabase_get(
            "users?select=*"
        )

        events = supabase_get(
            "events?select=*"
        )

        total_users = len(users)

        total_reads = sum(
            u.get(
                "total_reads", 0
            )
            for u in users
        )

        total_searches = sum(
            u.get(
                "total_searches", 0
            )
            for u in users
        )

        today = (
            datetime.utcnow()
            .date()
            .isoformat()
        )

        active_today = len([
            u for u in users
            if (
                u.get(
                    "last_seen", ""
                )[:10]
                == today
            )
        ])

        text = f"""
📊 <b>Bot Analytics</b>

👥 Users:
<b>{total_users}</b>

🔥 Active Today:
<b>{active_today}</b>

📖 Total Reads:
<b>{total_reads}</b>

🔎 Searches:
<b>{total_searches}</b>

📝 Events Logged:
<b>{len(events)}</b>
"""

        bot.send_message(
            message.chat.id,
            text
        )

    except Exception as e:

        print(
            "ADMIN ERROR:",
            e
        )

        bot.reply_to(
            message,
            "⚠️ Admin failed"
        )
def increment_user_searches(user_id: int):

    try:
        # get current count
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/users"
            f"?telegram_id=eq.{user_id}"
            f"&select=total_searches",
            headers=HEADERS,
            timeout=10
        )

        data = res.json()

        current_searches = 0

        if data:
            current_searches = data[0].get(
                "total_searches", 0
            )

        # update count
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/users"
            f"?telegram_id=eq.{user_id}",
            headers=HEADERS,
            json={
                "total_searches":
                current_searches + 1
            },
            timeout=10
        )

    except Exception as e:
        print(
            "increment_user_searches:",
            e
        )
def get_user_info(telegram_id):

    users = supabase_get(
        f"users?"
        f"telegram_id=eq.{telegram_id}"
        f"&select=*"
    )

    if users:

        u = users[0]

        name = (
            u.get("first_name")
            or "Unknown"
        )

        username = (
            u.get("username")
            or "-"
        )

        if username != "-":
            return (
                f"{name} "
                f"(@{username})"
            )

        return name

    return str(telegram_id)
@bot.message_handler(
    commands=["admin_users"]
)
def admin_users(message):
    """List users with pagination + action buttons."""
    if not require_admin(message):
        return

    try:
        # Parse page from command: /admin_users [page]
        parts = message.text.strip().split()
        page = 1
        if len(parts) >= 2:
            try:
                page = max(1, int(parts[1]))
            except:
                pass

        per_page = 10
        offset = (page - 1) * per_page

        # Get total count
        all_users = supabase_get("users?select=*")
        total_users = len(all_users)
        total_pages = (total_users + per_page - 1) // per_page

        # Get paginated slice
        users = all_users[offset:offset + per_page]

        if not users:
            bot.reply_to(message, "No users on this page.")
            return

        text = f"👥 <b>Users</b> (Page {page}/{total_pages})\n\n"

        # Build inline keyboard with user action buttons
        kb = types.InlineKeyboardMarkup()

        for i, u in enumerate(users, offset + 1):
            user_id = u.get("telegram_id", "?")
            name = u.get('first_name', 'Unknown')
            username = f"@{u['username']}" if u.get("username") else "no username"
            reads = u.get('total_reads', 0)
            searches = u.get('total_searches', 0)

            text += f"<b>{i}.</b> {name} ({username})\n   📖 {reads} reads | 🔎 {searches} searches\n\n"

            # Action row for each user
            kb.row(
                types.InlineKeyboardButton(
                    f"🔍 Inspect {name[:15]}",
                    callback_data=f"admin_inspect_{user_id}"
                ),
                types.InlineKeyboardButton(
                    f"✉️ Msg {name[:15]}",
                    callback_data=f"admin_msgprompt_{user_id}"
                )
            )

        # Pagination row
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton("⬅ Prev", callback_data=f"admin_users_{page-1}"))
        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton("Next ➡", callback_data=f"admin_users_{page+1}"))
        if nav_row:
            kb.row(*nav_row)

        bot.send_message(
            message.chat.id,
            text[:4000],
            reply_markup=kb,
            parse_mode="HTML"
        )

    except Exception as e:
        print("ADMIN USERS:", e)
        bot.reply_to(message, "⚠️ Failed to load users")

@bot.message_handler(commands=["admin_searches"])
def admin_searches(message):
    """Recent searches with optional date filter."""
    if not require_admin(message):
        return

    try:
        events = supabase_get(
            "events?"
            "event_type=eq.search"
            "&select=*"
            "&order=id.desc"
        )

        if not events:
            bot.reply_to(
                message,
                "No searches."
            )
            return
    except Exception as e:
        print(f"admin_searches error: {e}")
        bot.reply_to(message, "⚠️ Failed to load searches")
        return
        return

    text = (
        "🔎 <b>Recent Searches</b>\n\n"
    )

    for e in events[:50]:

        user_text = get_user_info(
            e.get("telegram_id")
        )

        text += (
            f"👤 {user_text}\n"
            f"🔍 {e.get('value')}\n\n"
        )

    bot.send_message(
        message.chat.id,
        text[:4000]
    )
@bot.message_handler(
    commands=["admin_events"]
)
def admin_events(message):
    """All events with optional type/date filter."""
    if not require_admin(message):
        return

    try:
        events = supabase_get(
            "events?"
            "select=*"
            "&order=id.desc"
        )

        if not events:
            bot.reply_to(
                message,
                "No events."
            )
            return
    except Exception as e:
        print(f"admin_events error: {e}")
        bot.reply_to(message, "⚠️ Failed to load events")
        return

    text = (
        "📝 <b>Recent Events</b>\n\n"
    )

    for e in events[:50]:

        user_text = get_user_info(
            e.get("telegram_id")
        )

        text += (
            f"👤 {user_text}\n"
            f"⚡ {e.get('event_type')}\n"
            f"📌 {e.get('value')}\n\n"
        )

    bot.send_message(
        message.chat.id,
        text[:4000]
    )
@bot.message_handler(
    commands=["admin_help"]
)
def admin_help(message):
    """Admin command reference (no key exposed)."""
    if not require_admin(message):
        return

    text = """
🛠 <b>Admin Commands</b>

📊 <b>Analytics</b>
<code>/admin</code> — Overview dashboard
<code>/admin_top_stories</code> — Most read stories
<code>/admin_daily_stats</code> — Today's hourly breakdown
<code>/admin_growth</code> — User growth (7/30 days)

👥 <b>Users</b>
<code>/admin_users</code> — All users (paginated)
<code>/admin_user [ID]</code> — Deep dive on user

🔎 <b>Activity</b>
<code>/admin_searches</code> — Recent searches
<code>/admin_events</code> — All events

📢 <b>Actions</b>
<code>/admin_post_now</code> — Force post to channel
<code>/admin_posting_status</code> — Check scheduler
<code>/admin_broadcast [msg]</code> — Message all users

ℹ️ <code>/admin_help</code> — This menu
"""

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML"
    )

def _wp_get(
    endpoint: str,
    params: dict | None = None,
    chat_id=None,
    loading_msg_id=None
):
    """
    Wrapper for WordPress REST API requests.
    Returns (json_data, headers)
    """

    url = f"{WP_API}/{endpoint}"

    headers = {
        "User-Agent":
        "Mozilla/5.0"
    }

    last_error = None

    for attempt in range(3):

        try:

            r = requests.get(
                url,
                params=params,
                timeout=30,
                headers=headers
            )

            r.raise_for_status()

            return (
                r.json(),
                r.headers
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ) as e:

            print(
                f"WP retry "
                f"{attempt + 1}/3:",
                e
            )

            last_error = e

# Update loading message
            if (
               chat_id
               and
               loading_msg_id
            ):

               try:

                   bot.edit_message_text(
                     chat_id=chat_id,
                     message_id=
                     loading_msg_id,
                     text=(
                       "⚠️ Site is slow.\n"
                       f"🔄 Retrying ({attempt + 1}/3)..."
                     )
                   )

               except:
                   pass

            time.sleep(2)

        except requests.RequestException as e:

            raise RuntimeError(
                f"WordPress API error: {e}"
            )

    report_api_error(endpoint, f"Timed out after 3 retries: {last_error}")
    raise RuntimeError(
        f"WordPress timed out "
        f"after retries: "
        f"{last_error}"
    )
WP_API = BASE_URL       
def fetch_posts(page: int = 1, per_page: int = PER_PAGE,
                category: int | None = None,
                search: str | None = None,
                chat_id: int | None = None,
                loading_msg_id: int | None = None) -> tuple[list, int]:
    """Return (posts_list, total_pages)."""
    params: dict = {"page": page, "per_page": per_page, "_fields": "id,title,author,categories"}
    if category:
        params["categories"] = category
    if search:
        params["search"] = search
    data, headers = _wp_get("posts", params, chat_id=chat_id, loading_msg_id=loading_msg_id)
    total_pages = int(headers.get("X-WP-TotalPages", 1))
    return data, total_pages
def fetch_real_author_id(post_id: int, content_html: str) -> int:
    """
    Get real author tag id from post content + tags.
    Example:
    Author : Jhon Clinton
    <a href="/tag/Jhon-Clinton">Previous Part</a>
    """

    soup = BeautifulSoup(content_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Extract author name
    match = re.search(
        r"Author\s*:\s*([^\|\[\n]+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return 1  # fallback

    author_name = match.group(1).strip().lower()

    try:
        tags, _ = _wp_get(
            "tags",
            {
                "post": post_id,
                "per_page": 100,
                "_fields": "id,name,slug"
            }
        )

        for tag in tags:
            tag_name = tag.get("name", "").strip().lower()
            tag_slug = tag.get("slug", "").replace("-", " ").strip().lower()

            # strong match
            if (
                author_name == tag_name
                or author_name == tag_slug
                or author_name in tag_name
                or tag_name in author_name
            ):
                return tag["id"]

    except Exception as e:
        log.warning("Failed to resolve real author: %s", e)

    return 1


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: Story Content Caching
# ──────────────────────────────────────────────────────────────────────────────

STORY_CACHE_TTL = 1800  # 30 minutes

def _get_cached_story(post_id: int) -> dict | None:
    """Return cached story pages if not expired."""
    cached = story_content_cache.get(post_id)
    if not cached:
        return None
    if time.time() - cached.get("fetched_at", 0) > STORY_CACHE_TTL:
        del story_content_cache[post_id]
        return None
    return cached

def _cache_story(post_id: int, title: str, content: str, author_id: int) -> list[str]:
    """Parse, cache, and return story pages."""
    pages = get_story_parts(title, content)
    story_content_cache[post_id] = {
        "pages": pages,
        "title": title,
        "author_id": author_id,
        "fetched_at": time.time(),
    }
    return pages

def _clear_story_cache(post_id: int | None = None) -> None:
    """Clear specific or all expired story caches."""
    if post_id:
        story_content_cache.pop(post_id, None)
        return
    now = time.time()
    expired = [pid for pid, data in story_content_cache.items() 
               if now - data.get("fetched_at", 0) > STORY_CACHE_TTL]
    for pid in expired:
        del story_content_cache[pid]

def fetch_post_content(post_id: int) -> tuple[str, str, int]:
    """Return (title_html, content_html, real_author_id)."""

    # Check cache first
    cached = _get_cached_story(post_id)
    if cached:
        return cached["title"], "<!--CACHED-->" + str(post_id), cached["author_id"]

    data, _ = _wp_get(
        f"posts/{post_id}",
        {"_fields": "id,title,content"}
    )

    title = data["title"]["rendered"]
    content = data["content"]["rendered"]

    # Resolve fake author -> real author tag id
    author = fetch_real_author_id(post_id, content)

    # Cache the parsed pages
    _cache_story(post_id, title, content, author)

    return title, content, author


def fetch_categories() -> list[dict]:
    """Return all categories (up to 100)."""
    data, _ = _wp_get("categories", {"per_page": 100, "_fields": "id,name,count"})
    return [c for c in data if c.get("count", 0) > 0]


def fetch_author_posts(author_id: int) -> list[dict]:
    """
    All posts by a REAL author tag
    (pseudo-author id from tags)
    """

    data, _ = _wp_get("posts", {
        "tags": author_id,
        "per_page": 100,
        "_fields": "id,title",
    })

    return data

def fetch_author_posts_paginated(author_id: int,
                                 page: int = 1,
                                 per_page: int = 10,
                                 chat_id: int | None = None,
                                 loading_msg_id: int | None = None):
    """
    Paginated posts by real author tag.
    """

    data, headers = _wp_get("posts", {
        "tags": author_id,
        "page": page,
        "per_page": per_page,
        "_fields": "id,title",
    }, chat_id=chat_id, loading_msg_id=loading_msg_id)

    total_pages = int(headers.get("X-WP-TotalPages", 1))
    return data, total_pages
# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: HTML / Content Cleaning
# ──────────────────────────────────────────────────────────────────────────────

def clean_html(raw: str) -> str:
    """Strip scripts/styles/junk, keep readable HTML safe for Telegram."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Convert to plain text with basic HTML kept
    text = soup.get_text(separator="\n")
    text = JUNK_RE.sub("", text)

    # Collapse excessive blank lines
    lines = [l.rstrip() for l in text.splitlines()]
    cleaned: list[str] = []
    blanks = 0
    for line in lines:
        if line == "":
            blanks += 1
            if blanks <= 1:
                cleaned.append("")
        else:
            blanks = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def get_story_parts(title: str, content: str) -> list[str]:
    """
    Split story content into pages.
    Priority: <!--nextpage-->  →  ~3500-char paragraph chunks.
    """
    # WordPress nextpage split
    if "<!--nextpage-->" in content:
        raw_parts = content.split("<!--nextpage-->")
        return [clean_html(p) for p in raw_parts if p.strip()]

    # Fallback: split cleaned text at ~3500 chars on paragraph boundaries
    text  = clean_html(content)
    pages : list[str] = []
    limit = 3500
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        pages.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pages.append(text)
    return pages if pages else ["(No content)"]

def supabase_get(endpoint):

    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/{endpoint}",
            headers=HEADERS,
            timeout=10
        )

        return res.json()

    except Exception as e:
        print(
            "supabase_get:",
            e
        )

        return []
# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: Part Detection
# ──────────────────────────────────────────────────────────────────────────────

# Matches the trailing [Author Name] tag, e.g. "[രേണുക]" or "[Anamika]"
_AUTHOR_TAG_RE = re.compile(r"\s*\[[^\]]+\]\s*$")

# Patterns that capture a numeric part indicator at the end of a clean title.
# Applied AFTER the author tag has been stripped.
_PART_PATTERNS = [
    re.compile(r"^(.*?)\s+[Pp]art\s*(\d+)\s*$"),
    re.compile(r"^(.*?)\s+[Cc]h(?:apter)?\s*(\d+)\s*$"),
    re.compile(r"^(.*?)\s+[Ee]pisode\s*(\d+)\s*$"),
    re.compile(r"^(.*?)\s+(\d+)\s*$"),           # bare number (any length)
]


def _html_to_text(raw: str) -> str:
    """Decode HTML entities and strip all HTML tags."""
    return BeautifulSoup(raw, "html.parser").get_text()


def _strip_author_tag(text: str) -> str:
    """Remove a trailing [Author Name] bracket from a plain-text title."""
    return _AUTHOR_TAG_RE.sub("", text).strip()

_PART_PATTERNS = [
    re.compile(r"^(.*?)\s+[Pp]art[\s:.-]*(\d+)"),
    re.compile(r"^(.*?)\s+[Cc]h(?:apter)?[\s:.-]*(\d+)"),
    re.compile(r"^(.*?)\s+[Ee]pisode[\s:.-]*(\d+)"),
    re.compile(r"^(.*?)\s+ഭാഗം[\s:.-]*(\d+)"),
    re.compile(r"^(.*?)\s+(\d+)")
]


def normalize_title(text):
    text = re.sub(r"\s*\[.*?\]\s*$", "", text).strip()
    text = text.lower()
    text = " ".join(text.split())
    return text


def _parse_title(raw_title):
    title = normalize_title(raw_title)

    for pattern in _PART_PATTERNS:
        m = pattern.match(title)
        if m:
            base = normalize_title(m.group(1))
            return base, int(m.group(2))

    return None


def find_adjacent_parts(current_id, current_title, author_id):
    author_posts = fetch_author_posts(author_id)
    print(author_id)
    print(current_id)
    parsed = _parse_title(current_title)

    if parsed:
        current_base, current_part = parsed
        is_implicit_first = False
    else:
        current_base = normalize_title(current_title)
        current_part = 1
        is_implicit_first = True

    candidates = {}

    for post in author_posts:
        post_title = BeautifulSoup(
            post["title"]["rendered"],
            "html.parser"
        ).get_text()

        post_id = post.get("id")

        result = _parse_title(post_title)

        if result:
            base, part_no = result

            if base == current_base:
                candidates[part_no] = post

    # If current story has no number,
    # treat it as implicit Part 1
    if is_implicit_first:
        candidates[1] = {
            "id": current_id,
            "title": {
                "rendered": current_title
            }
        }

    # Special handling:
    # Fear, Fear 2, Fear 3
    elif 2 in candidates:
        implicit_title = normalize_title(current_title)

        for post in author_posts:
            post_title = BeautifulSoup(
                post["title"]["rendered"],
                "html.parser"
            ).get_text()

            # exact plain title = implicit part 1
            if normalize_title(post_title) == current_base:
                candidates[1] = post
                break

    if not candidates:
        return None, None

    part_numbers = sorted(candidates.keys())

    prev_num = None
    next_num = None

    for n in part_numbers:
        if n < current_part:
            prev_num = n
        elif n > current_part and next_num is None:
            next_num = n
            break

    prev_post = candidates.get(prev_num)
    next_post = candidates.get(next_num)

    return prev_post, next_post


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: Pagination Keyboard Builder
# ──────────────────────────────────────────────────────────────────────────────

def build_pagination(current: int, total: int, prefix: str) -> list[types.InlineKeyboardButton]:
    """
    Smart paginator.  Returns a flat list of InlineKeyboardButton.
    prefix: callback_data prefix, e.g. "latest" / "category_14" / "search_teacher"
    """
    if total <= 1:
        return []

    def btn(page: int) -> types.InlineKeyboardButton:
        label = f"•{page}•" if page == current else str(page)
        return types.InlineKeyboardButton(label, callback_data=f"{prefix}_{page}")

    def ellipsis() -> types.InlineKeyboardButton:
        return types.InlineKeyboardButton("…", callback_data="noop")

    pages: list[int] = sorted(set(
        [1, total] +
        list(range(max(1, current - 2), min(total, current + 2) + 1))
    ))

    buttons: list[types.InlineKeyboardButton] = []
    prev = None
    for p in pages:
        if prev is not None and p - prev > 1:
            buttons.append(ellipsis())
        buttons.append(btn(p))
        prev = p
    return buttons


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: Safe Telegram ops
# ──────────────────────────────────────────────────────────────────────────────

def safe_delete(chat_id: int, msg_id: int | None) -> None:
    if msg_id is None:
        return
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


def safe_edit(chat_id: int, msg_id: int, text: str,
              reply_markup=None) -> bool:
    try:
        bot.edit_message_text(
            text, chat_id, msg_id,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        return True
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)
        return False


def main_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=False,
       is_persistent=True)
    kb.row("🔥 Latest", "📚 Categories")
    kb.row("🔎 Search")
    return kb


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_home
# ──────────────────────────────────────────────────────────────────────────────

def show_home(message: types.Message) -> None:
    bot.send_message(
        message.chat.id,
        "📖 <b>KKStories</b>\n\nUse the menu below to browse stories.",
        reply_markup=main_keyboard(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_latest
# ──────────────────────────────────────────────────────────────────────────────

@bot.message_handler(
    commands=["support"]
)
def support_command(
    message
):

    bot.send_message(
        message.chat.id,
        (
            "🤖 <b>Need support or have any issue?</b>\n\n"
            "Chat with our admin directly here 👇\n"
            "<a href='https://t.me/kambikathaadbot'>"
            "@kambikathaadbot"
            "</a>\n\n"
            "💬 Suggestions, help & support "
            "available 😊"
        ),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ──────────────────────────────────────────────────────────────────────────────
# USER-FACING ERROR MESSAGES
# ──────────────────────────────────────────────────────────────────────────────

def _user_error_message(error: Exception, context: str = "") -> str:
    """
    Convert raw exceptions into safe, user-friendly messages.
    Never exposes URLs, stack traces, or internal details.
    """
    error_str = str(error).lower()

    # WordPress / API errors
    if any(k in error_str for k in ["wordpress", "wp-json", "api error", "500", "502", "503", "504"]):
        return "⚠️ <b>Story server is temporarily unavailable.</b>\nPlease try again in a few moments. 💫"

    # Timeout / connection
    if any(k in error_str for k in ["timeout", "connection", "timed out", "unreachable", "refused"]):
        return "⚠️ <b>Connection is slow right now.</b>\nPlease check your internet and try again. 🌐"

    # Not found
    if any(k in error_str for k in ["404", "not found"]):
        return "⚠️ <b>Story not found.</b>\nIt may have been removed. 📭"

    # Database / Supabase
    if any(k in error_str for k in ["supabase", "database", "db error"]):
        return "⚠️ <b>Something went wrong on our end.</b>\nPlease try again shortly. 🔧"

    # Generic fallback
    return "⚠️ <b>Oops! Something went wrong.</b>\nPlease try again. 🙏"


def show_latest(chat_id: int, page: int = 1,
                delete_msg_id: int | None = None,
                loading_msg_id: int | None = None) -> None:
    try:
        posts, total_pages = fetch_posts(page=page, chat_id=chat_id, loading_msg_id=loading_msg_id)
    except Exception as e:
        # Always update loading message if provided
        fail_text = _user_error_message(e, "latest")
        if loading_msg_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=loading_msg_id, text=fail_text, parse_mode="HTML")
            except:
                bot.send_message(chat_id, fail_text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, fail_text, parse_mode="HTML")
        return

    kb = types.InlineKeyboardMarkup()
    for post in posts:
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()
        kb.add(types.InlineKeyboardButton(
            title,
            callback_data=f"story_{post['id']}_1_latest_{page}",
        ))

    for btn in build_pagination(page, total_pages, "latest"):
        pass  # collect in list first
    page_btns = build_pagination(page, total_pages, "latest")
    if page_btns:
        # Split into rows of 5
        row: list[types.InlineKeyboardButton] = []
        for i, btn in enumerate(page_btns):
            row.append(btn)
            if len(row) == 5:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

    if delete_msg_id:

       msg = bot.edit_message_text(
           chat_id=chat_id,
           message_id=delete_msg_id,
           text="🔥 <b>Latest Stories</b>",
           reply_markup=kb,
           parse_mode="HTML"
        )

       msg_id = delete_msg_id

    else:

        msg = bot.send_message(
            chat_id,
            "🔥 <b>Latest Stories</b>",
             reply_markup=kb,
             parse_mode="HTML"
        )

        msg_id = msg.message_id

    nav_state[chat_id] = {
        "type": "latest",
        "page": page,
        "msg_id": msg_id
    }


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_categories
# ──────────────────────────────────────────────────────────────────────────────

def show_categories(chat_id: int, delete_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)

    try:
        cats = fetch_categories()
    except Exception as e:
        print("show_categories error:", e)
        bot.send_message(chat_id, _user_error_message(e, "categories"), parse_mode="HTML")
        return

    kb = types.InlineKeyboardMarkup()
    row: list[types.InlineKeyboardButton] = []
    for cat in cats:
        row.append(types.InlineKeyboardButton(
            cat["name"],
            callback_data=f"category_{cat['id']}_1",
        ))
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)

    msg = bot.send_message(chat_id, "📚 <b>Categories</b>", reply_markup=kb)
    nav_state[chat_id] = {"type": "categories", "msg_id": msg.message_id}


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_category_posts
# ──────────────────────────────────────────────────────────────────────────────
@bot.message_handler(
    commands=["latest"]
)
def latest_command(
    message
):

    loading = bot.send_message(
        message.chat.id,
        "📚 Loading latest stories..."
    )

    try:

        state = nav_state.get(
            message.chat.id,
            {}
        )

        old_msg_id = state.get(
            "msg_id"
        )

        if old_msg_id:

            try:

                bot.delete_message(
                    message.chat.id,
                    old_msg_id
                )

            except:
                pass

        show_latest(
            message.chat.id,
            page=1,
            delete_msg_id=loading.message_id,
            loading_msg_id=loading.message_id
        )

    except Exception as e:

        print(
            "latest error:",
            e
        )

        bot.edit_message_text(
            chat_id=
            message.chat.id,
            message_id=
            loading.message_id,
            text=
            (
                "⚠️ Failed to "
                "load latest "
                "stories."
            )
        )


def show_category_posts(chat_id: int, cat_id: int, page: int = 1,
                        delete_msg_id: int | None = None,
                        cat_name: str = "",
                        loading_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)

    try:
        posts, total_pages = fetch_posts(page=page, category=cat_id, chat_id=chat_id, loading_msg_id=loading_msg_id)
    except Exception as e:
        fail_text = _user_error_message(e, "category")
        if loading_msg_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=loading_msg_id, text=fail_text, parse_mode="HTML")
            except:
                bot.send_message(chat_id, fail_text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, fail_text, parse_mode="HTML")
        return

    if not cat_name:
        cat_name = f"Category {cat_id}"

    kb = types.InlineKeyboardMarkup()
    for post in posts:
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()
        kb.add(types.InlineKeyboardButton(
            title,
            callback_data=f"story_{post['id']}_1_category_{cat_id}_{page}",
        ))

    page_btns = build_pagination(page, total_pages, f"category_{cat_id}")
    if page_btns:
        row = []
        for btn in page_btns:
            row.append(btn)
            if len(row) == 5:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

    msg = bot.send_message(
        chat_id,
        f"📚 <b>{cat_name}</b>",
        reply_markup=kb,
    )
    nav_state[chat_id] = {
        "type": "category", "page": page, "cat_id": cat_id,
        "cat_name": cat_name, "msg_id": msg.message_id,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_search_prompt
# ──────────────────────────────────────────────────────────────────────────────

def show_search_prompt(chat_id: int, delete_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)
    msg = bot.send_message(
        chat_id,
        "🔎 <b>Search</b>\n\nWhat do you want to search?",
        reply_markup=types.ForceReply(selective=True),
    )
    search_pending[chat_id] = msg.message_id


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_search_results
# ──────────────────────────────────────────────────────────────────────────────

def show_search_results(chat_id: int, query: str, page: int = 1,
                        delete_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)
    track_event(
     chat_id,
     "search",
     query
    )
    increment_user_searches(chat_id)
    loading = bot.send_message(
       chat_id,
       "🔎 Searching stories..."
    )
    try:
        posts, total_pages = fetch_posts(page=page, search=query, chat_id=chat_id, loading_msg_id=loading.message_id)
    except Exception as e:

        print(
          "search error:",
          e
        )

        # Always update the loading message — never leave it stuck
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading.message_id,
                text=(
                    "⚠️ <b>Search failed</b>\n"
                    "Site is not responding.\n"
                    "Please try again in a few seconds."
                ),
                parse_mode="HTML"
            )
        except Exception as edit_err:
            # If edit fails (message too old, etc), send new and delete loading
            print("edit fail:", edit_err)
            try:
                bot.delete_message(chat_id, loading.message_id)
            except:
                pass
            bot.send_message(
                chat_id,
                (
                    "⚠️ <b>Search failed</b>\n"
                    "Site is not responding. Please try again."
                ),
                parse_mode="HTML"
            )
        return

    if not posts:
        bot.send_message(chat_id, f"🔎 No results for <b>{query}</b>.")
        track_event(
         chat_id,
         "failed_search",
         query
        )
        return

    # Encode query for callback_data (replace spaces with underscores)
    q_key = query.replace(" ", "_")[:30]

    kb = types.InlineKeyboardMarkup()
    for post in posts:
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()
        kb.add(types.InlineKeyboardButton(
            title,
            callback_data=f"story_{post['id']}_1_search_{q_key}_{page}",
        ))

    page_btns = build_pagination(page, total_pages, f"search_{q_key}")
    if page_btns:
        row = []
        for btn in page_btns:
            row.append(btn)
            if len(row) == 5:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

    msg = bot.edit_message_text(
        chat_id=chat_id,
        message_id=loading.message_id,
        text=f"🔎 <b>{query}</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    # restore menu keyboard
    bot.send_message(
       chat_id,
       "⬇️ Menu restored",
       reply_markup=main_keyboard()
    )
    nav_state[chat_id] = {
        "type": "search", "page": page, "query": query,
        "q_key": q_key, "msg_id": msg.message_id,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_story
# ──────────────────────────────────────────────────────────────────────────────

def show_story(chat_id: int, post_id: int, page: int,
               back_ctx: str,
               existing_msg_id: int | None = None,
               list_msg_id: int | None = None,
               from_part_nav: bool = False,
               loading_msg_id: int | None = None) -> None:
    """
    Render / update the story message.

    SCROLL FIX: For page turns within same story, we DELETE the old message
    and SEND a new one — Telegram auto-scrolls to top of new message.

    SPEED FIX: Story content is cached after first fetch. Page turns serve
    from memory (no API call). Part navigation re-uses cached content.

    existing_msg_id → message to delete (for scroll-to-top effect)
    list_msg_id     → delete this before sending fresh story message
    back_ctx        → raw context string embedded in callback, e.g. "latest_3"
    from_part_nav   → True when switching between parts (preserves scroll)
    """

    # Track read (only count once per minute per user per story)
    if can_count_story_read(chat_id, post_id):
        track_event(chat_id, "open_story", str(post_id))
        increment_user_reads(chat_id)
        track_story_read(post_id, "")

    # ── FETCH OR CACHE ──────────────────────────────────────────────────
    try:
        cached = _get_cached_story(post_id)

        if cached:
            # Serve from cache — instant, no API call
            title_text = BeautifulSoup(cached["title"], "html.parser").get_text()
            pages = cached["pages"]
            author_id = cached["author_id"]
        else:
            # First fetch — hit WordPress API
            title_html, content_html, author_id = fetch_post_content(post_id)
            title_text = BeautifulSoup(title_html, "html.parser").get_text()
            # fetch_post_content already cached it, now retrieve
            cached = _get_cached_story(post_id)
            if cached:
                pages = cached["pages"]
            else:
                # Fallback: parse now (shouldn't happen)
                pages = get_story_parts(title_text, content_html)
    except Exception as e:
        fail_text = _user_error_message(e, "story")
        if loading_msg_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=loading_msg_id, text=fail_text, parse_mode="HTML")
            except:
                bot.send_message(chat_id, fail_text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, fail_text, parse_mode="HTML")
        return

    total_pages = len(pages)
    page = max(1, min(page, total_pages))
    content = pages[page - 1]

    # ── PART NAVIGATION ─────────────────────────────────────────────────
    prev_part, next_part = find_adjacent_parts(post_id, title_text, author_id)

    # ── BUILD KEYBOARD ──────────────────────────────────────────────────
    kb = types.InlineKeyboardMarkup()

    # Page pagination
    page_btns = build_pagination(page, total_pages, f"spage_{post_id}_{back_ctx}")
    if page_btns:
        row = []
        for btn in page_btns:
            row.append(btn)
            if len(row) == 5:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)

    # Part nav row
    part_row: list[types.InlineKeyboardButton] = []
    if prev_part:
        parsed = _parse_title(prev_part["title"]["rendered"])
        label = f"⏮ Part {parsed[1]}" if parsed else "⏮ Prev"
        part_row.append(types.InlineKeyboardButton(
            label,
            callback_data=f"story_{prev_part['id']}_1_{back_ctx}",
        ))
    if next_part:
        parsed = _parse_title(next_part["title"]["rendered"])
        label = f"⏭ Part {parsed[1]}" if parsed else "⏭ Next"
        part_row.append(types.InlineKeyboardButton(
            label,
            callback_data=f"story_{next_part['id']}_1_{back_ctx}",
        ))
    if part_row:
        kb.row(*part_row)
    else:
        kb.row(types.InlineKeyboardButton(
            "📚 Related Story Parts",
            callback_data=f"related_{author_id}_{post_id}_{back_ctx}"
        ))

    # Back button
    kb.row(types.InlineKeyboardButton("⬅ Back", callback_data=f"back_{back_ctx}"))

    # ── MESSAGE TEXT ────────────────────────────────────────────────────
    header = f"📖 <b>{title_text}</b>\n📄 Page {page}/{total_pages}\n\n"
    max_content = 4096 - len(header) - 20
    if len(content) > max_content:
        content = content[:max_content] + "…"
    text = header + content

    # ── RENDER STRATEGY ─────────────────────────────────────────────────
    # PAGE TURN (same story): Delete old msg + send new = auto-scroll to top ✨
    # PART NAV (different story): Same delete+send for consistency
    # FIRST OPEN: Delete list msg + send new

    if existing_msg_id:
        # Delete old message first (triggers scroll-to-top on new message)
        safe_delete(chat_id, existing_msg_id)
    else:
        safe_delete(chat_id, list_msg_id)

    # Send fresh message — user sees it at top automatically
    bot.send_message(chat_id, text, reply_markup=kb)

def show_related_story_parts(chat_id: int,
                             author_id: int,
                             current_post_id: int,
                             page: int,
                             back_ctx: str,
                             msg_id: int,
                             loading_msg_id: int | None = None):

    try:
        posts, total_pages = fetch_author_posts_paginated(
            author_id,
            page=page,
            per_page=10
        )
    except Exception as e:
        fail_text = _user_error_message(e, "related")
        if loading_msg_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=loading_msg_id, text=fail_text, parse_mode="HTML")
            except:
                bot.send_message(chat_id, fail_text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, fail_text, parse_mode="HTML")
        return

    kb = types.InlineKeyboardMarkup()

    for post in posts:
        title = BeautifulSoup(
            post["title"]["rendered"],
            "html.parser"
        ).get_text()

        marker = "📖 " if post["id"] == current_post_id else ""

        kb.add(types.InlineKeyboardButton(
            f"{marker}{title}",
            callback_data=(
                f"story_{post['id']}_1_{back_ctx}"
            ),
        ))

    # pagination
    page_btns = build_pagination(
        page,
        total_pages,
        f"relparts_{author_id}_{current_post_id}_{back_ctx}"
    )

    if page_btns:
        row = []
        for btn in page_btns:
            row.append(btn)

            if len(row) == 5:
                kb.row(*row)
                row = []

        if row:
            kb.row(*row)

    kb.row(
        types.InlineKeyboardButton(
            "⬅ Back to Story",
            callback_data=(
                f"story_{current_post_id}_1_{back_ctx}"
            )
        )
    )

    # Delete old message for scroll-to-top effect
    safe_delete(chat_id, msg_id)
    bot.send_message(
        chat_id,
        "📚 <b>Related Story Parts</b>\n\n"
        "Browse author's posts below:",
        reply_markup=kb
    )
# ──────────────────────────────────────────────────────────────────────────────
# CALLBACK ROUTING
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# ADMIN PAGINATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# BROADCAST HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _execute_broadcast(chat_id: int, msg_id: int, text: str) -> None:
    """Send broadcast to all users."""
    try:
        safe_edit(chat_id, msg_id, "⏳ Broadcasting...")

        users = supabase_get("users?select=telegram_id")
        sent = 0
        failed = 0

        for u in users:
            user_id = u.get("telegram_id")
            if not user_id:
                continue

            try:
                bot.send_message(user_id, f"📢 <b>Announcement</b>\n\n{text}", parse_mode="HTML")
                sent += 1
                time.sleep(0.05)  # Rate limit safety
            except Exception as e:
                failed += 1
                print(f"Broadcast fail {user_id}: {e}")

        safe_edit(chat_id, msg_id, f"✅ Broadcast complete!\n\n📤 Sent: {sent}\n❌ Failed: {failed}")

    except Exception as e:
        print("broadcast execution error:", e)
        safe_edit(chat_id, msg_id, "⚠️ Broadcast failed")


# ──────────────────────────────────────────────────────────────────────────────
# ADMIN USER ACTION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _show_user_inspect(chat_id: int, msg_id: int, user_id: int) -> None:
    """Show user deep-dive (equivalent to /admin_user [ID])."""
    try:
        users = supabase_get(f"users?telegram_id=eq.{user_id}&select=*")
        if not users:
            safe_edit(chat_id, msg_id, f"❌ User <code>{user_id}</code> not found.")
            return

        u = users[0]

        # Get recent events
        events = supabase_get(f"events?telegram_id=eq.{user_id}&select=*&order=id.desc&limit=15")

        text = f"""👤 <b>User Inspect</b>

🆔 <code>{user_id}</code>
👤 Name: {u.get('first_name', 'Unknown')}
📛 Username: @{u.get('username', '-')}
📖 Total Reads: {u.get('total_reads', 0)}
🔎 Total Searches: {u.get('total_searches', 0)}
📅 Last Seen: {u.get('last_seen', 'Never')[:16]}

<b>Recent Activity ({len(events)} events):</b>
"""

        for e in events[:10]:
            etype = e.get("event_type", "unknown")
            value = e.get("value", "")[:30]
            emoji = {"open_story": "📖", "search": "🔎", "failed_search": "❌"}.get(etype, "⚡")
            text += f"{emoji} {etype}: {value}\n"

        # Action buttons
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✉️ Send Message", callback_data=f"admin_msgprompt_{user_id}"),
            types.InlineKeyboardButton("⬅ Back to Users", callback_data="admin_users_1")
        )

        safe_edit(chat_id, msg_id, text[:4000], reply_markup=kb)

    except Exception as e:
        print("user inspect error:", e)
        safe_edit(chat_id, msg_id, "⚠️ Failed to load user details")


def _show_message_prompt(chat_id: int, msg_id: int, user_id: int) -> None:
    """Show message input prompt for specific user."""
    try:
        # Get user name
        users = supabase_get(f"users?telegram_id=eq.{user_id}&select=first_name")
        name = users[0].get("first_name", "User") if users else "User"

        text = (f"✉️ <b>Message to {name}</b> (<code>{user_id}</code>)\n\n"
                f"Reply to this message with your text to send.\n\n"
                f"Or click cancel to go back.")

        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("❌ Cancel", callback_data=f"admin_inspect_{user_id}"),
            types.InlineKeyboardButton("⬅ Back to Users", callback_data="admin_users_1")
        )

        safe_edit(chat_id, msg_id, text, reply_markup=kb)

        # Store pending message state
        admin_msg_pending[chat_id] = {
            "target_id": user_id,
            "prompt_msg_id": msg_id
        }

    except Exception as e:
        print("message prompt error:", e)
        safe_edit(chat_id, msg_id, "⚠️ Failed to show message prompt")


def _send_admin_message(chat_id: int, msg_id: int, user_id: int, text: str) -> None:
    """Send message to user and confirm to admin."""
    try:
        bot.send_message(
            user_id,
            f"📩 <b>Message from Admin</b>\n\n{text}",
            parse_mode="HTML"
        )

        safe_edit(
            chat_id,
            msg_id,
            f"✅ Message sent to <code>{user_id}</code>!\n\n{text[:200]}"
        )

    except Exception as e:
        print(f"send admin message error to {user_id}:", e)
        safe_edit(
            chat_id,
            msg_id,
            f"❌ Failed to send to <code>{user_id}</code>.\nUser may have blocked the bot."
        )


# Pending admin messages: {admin_chat_id: {"target_id": user_id, "prompt_msg_id": msg_id}}
admin_msg_pending: dict[int, dict] = {}

# Pending broadcast messages: {admin_chat_id: full_broadcast_text}
broadcast_pending: dict[int, str] = {}

def _show_admin_users_page(chat_id: int, msg_id: int, page: int) -> None:
    """Show admin users list page (for inline pagination)."""
    try:
        per_page = 20
        offset = (page - 1) * per_page

        all_users = supabase_get("users?select=*")
        total_users = len(all_users)
        total_pages = (total_users + per_page - 1) // per_page

        users = all_users[offset:offset + per_page]

        if not users:
            safe_edit(chat_id, msg_id, "No users on this page.")
            return

        text = f"👥 <b>Users</b> (Page {page}/{total_pages})\n\n"

        for i, u in enumerate(users, offset + 1):
            username = f"@{u['username']}" if u.get("username") else "-"
            last_seen = u.get("last_seen", "Never")[:10]
            text += f"""<b>{i}.</b> {u.get('first_name', 'Unknown')} {username}
   📖 {u.get('total_reads', 0)} reads | 🔎 {u.get('total_searches', 0)} searches | 📅 {last_seen}

"""

        kb = types.InlineKeyboardMarkup()
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton("⬅ Prev", callback_data=f"admin_users_{page-1}"))
        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton("Next ➡", callback_data=f"admin_users_{page+1}"))
        if nav_row:
            kb.row(*nav_row)

        safe_edit(chat_id, msg_id, text[:4000], reply_markup=kb)

    except Exception as e:
        print("admin users page error:", e)

def route_callback(call: types.CallbackQuery) -> None:
    """Central dispatcher for all inline button presses."""
    data    = call.data
    chat_id = call.message.chat.id
    msg_id  = call.message.message_id
    track_user(call.from_user)
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    # ── No-op ──────────────────────────────────────────────────────────────
    if data == "noop":
        return

    # ── Latest page ────────────────────────────────────────────────────────
    # callback: latest_{page}
    if re.match(r"^latest_\d+$", data):
        page = int(data.split("_")[1])
        show_latest(chat_id, page=page, delete_msg_id=msg_id)
        return

    # ── Category list page ─────────────────────────────────────────────────
    # callback: category_{cat_id}_{page}
    m = re.match(r"^category_(\d+)_(\d+)$", data)
    if m:
        cat_id, page = int(m.group(1)), int(m.group(2))
        # Retrieve cat name from state if available
        state    = nav_state.get(chat_id, {})
        cat_name = state.get("cat_name", "")
        show_category_posts(chat_id, cat_id, page,
                            delete_msg_id=msg_id, cat_name=cat_name,
                            loading_msg_id=msg_id)
        return

    # ── Search page ────────────────────────────────────────────────────────
    # callback: search_{q_key}_{page}
    m = re.match(r"^search_(.+)_(\d+)$", data)
    if m:
        q_key = m.group(1)
        page  = int(m.group(2))
        query = q_key.replace("_", " ")
        show_search_results(chat_id, query, page, delete_msg_id=msg_id)
        return

    # ── Story page turn ────────────────────────────────────────────────────
    # callback: spage_{post_id}_{back_ctx}_{page}
    m = re.match(r"^spage_(\d+)_(.+)_(\d+)$", data)
    if m:
        post_id  = int(m.group(1))
        back_ctx = m.group(2)
        page     = int(m.group(3))
        # Pass msg_id as existing_msg_id — show_story will delete it and send new
        show_story(chat_id, post_id, page, back_ctx, existing_msg_id=msg_id)
        return

    # ── Open story ─────────────────────────────────────────────────────────
    # callback: story_{post_id}_{page}_{back_ctx...}
    # back_ctx examples: latest_3 | category_14_2 | search_teacher_1
    m = re.match(r"^story_(\d+)_(\d+)_(.+)$", data)
    if m:
        post_id  = int(m.group(1))
        page     = int(m.group(2))
        back_ctx = m.group(3)
        # Delete the list message (the one the button was on)
        show_story(chat_id, post_id, page, back_ctx, list_msg_id=msg_id)
        return
   # ── Related Story Parts ─────────────────────────
    m = re.match(
     r"^related_(\d+)_(\d+)_(.+)$",
     data
     )

    if m:
        author_id = int(m.group(1))
        current_post_id = int(m.group(2))
        back_ctx = m.group(3)

        show_related_story_parts(
              chat_id=chat_id,
              author_id=author_id,
              current_post_id=current_post_id,
              page=1,
              back_ctx=back_ctx,
              msg_id=msg_id,
              loading_msg_id=msg_id
        )
        return


# ── Related pagination ─────────────────────────
    m = re.match(
    r"^relparts_(\d+)_(\d+)_(.+)_(\d+)$",
    data
    )

    if m:
        author_id = int(m.group(1))
        current_post_id = int(m.group(2))
        back_ctx = m.group(3)
        page = int(m.group(4))

        show_related_story_parts(
             chat_id=chat_id,
             author_id=author_id,
             current_post_id=current_post_id,
             page=page,
             back_ctx=back_ctx,
             msg_id=msg_id,
             loading_msg_id=msg_id
        )
        return


    # ── Admin user pagination ────────────────────────────────────────────
    m = re.match(r"^admin_users_(\\d+)$", data)
    if m:
        page = int(m.group(1))
        _show_admin_users_page(chat_id, msg_id, page)
        return

    # ── Back ───────────────────────────────────────────────────────────────
    # callback: back_{back_ctx}
    m = re.match(r"^back_(.+)$", data)
    if m:
        back_ctx = m.group(1)
        _handle_back(chat_id, msg_id, back_ctx)
        return

    # ── Admin inspect user ─────────────────────────────────────────────
    m = re.match(r"^admin_inspect_(\d+)$", data)
    if m:
        user_id = int(m.group(1))
        _show_user_inspect(chat_id, msg_id, user_id)
        return

    # ── Admin message prompt ────────────────────────────────────────────
    m = re.match(r"^admin_msgprompt_(\d+)$", data)
    if m:
        user_id = int(m.group(1))
        _show_message_prompt(chat_id, msg_id, user_id)
        return

    # ── Admin send message to user ──────────────────────────────────────
    m = re.match(r"^admin_msgsend_(\d+)_(.+)$", data)
    if m:
        user_id = int(m.group(1))
        msg_text = m.group(2).replace("_", " ")
        _send_admin_message(chat_id, msg_id, user_id, msg_text)
        return

    # ── Broadcast confirm ────────────────────────────────────────────────
    if data == "broadcast_cancel":
        safe_edit(chat_id, msg_id, "❌ Broadcast cancelled.")
        return

    if data == "broadcast_confirm":
        broadcast_text = broadcast_pending.pop(chat_id, None)
        if broadcast_text:
            _execute_broadcast(chat_id, msg_id, broadcast_text)
        else:
            safe_edit(chat_id, msg_id, "⚠️ Broadcast expired. Please send again.")
        return

    log.warning("Unhandled callback: %s", data)


def _handle_back(chat_id: int, story_msg_id: int, back_ctx: str) -> None:
    """
    Delete story message and restore the list the user came from.
    back_ctx: latest_{page} | category_{cat_id}_{page} | search_{q_key}_{page}
    """
    safe_delete(chat_id, story_msg_id)

    # latest_{page}
    m = re.match(r"^latest_(\d+)$", back_ctx)
    if m:
        show_latest(chat_id, page=int(m.group(1)))
        return

    # category_{cat_id}_{page}
    m = re.match(r"^category_(\d+)_(\d+)$", back_ctx)
    if m:
        cat_id, page = int(m.group(1)), int(m.group(2))
        show_category_posts(chat_id, cat_id, page)
        return

    # search_{q_key}_{page}
    m = re.match(r"^search_(.+)_(\d+)$", back_ctx)
    if m:
        q_key = m.group(1)
        page  = int(m.group(2))
        query = q_key.replace("_", " ")
        show_search_results(chat_id, query, page)
        return

    # fallback
    show_latest(chat_id)


# ──────────────────────────────────────────────────────────────────────────────
# TELEGRAM HANDLERS
# ──────────────────────────────────────────────────────────────────────────────
@bot.message_handler(
    commands=["start", "help"]
)
def cmd_start(message):

    print(
        "RAW START:",
        message.text
    )

    try:

        track_user(
            message.from_user
        )

        parts = (
            message.text.strip()
            .split(maxsplit=1)
        )

        print("PARTS:", parts)

        if len(parts) > 1:

            payload = parts[1]

            print(
                "PAYLOAD:",
                payload
            )

            if payload.startswith(
                "story_"
            ):

                post_id = int(
                    payload.replace(
                        "story_",
                        ""
                    )
                )

                print(
                    "OPENING STORY:",
                    post_id
                )

                loading = bot.send_message(
                    message.chat.id,
                    "📖 Loading story..."
                )

                show_story(
                    chat_id=message.chat.id,
                    post_id=post_id,
                    page=1,
                    back_ctx="deeplink",
                    loading_msg_id=loading.message_id
                )

                return

        print(
            "SHOWING MENU"
        )

        show_home(message)

    except Exception as e:

        print(
            "START ERROR:",
            e
        )

@bot.message_handler(
    func=lambda m:
    m.text == "🔥 Latest"
)
def cmd_latest(
    message:
    types.Message
) -> None:

    track_user(
        message.from_user
    )

    loading = bot.send_message(
        message.chat.id,
        "📚 Loading latest stories..."
    )

    try:

        state = nav_state.get(
            message.chat.id,
            {}
        )

        old_msg_id = state.get(
            "msg_id"
        )

        if old_msg_id:

            try:

                bot.delete_message(
                    message.chat.id,
                    old_msg_id
                )

            except:
                pass

        show_latest(
            message.chat.id,
            page=1,
            delete_msg_id=loading.message_id,
            loading_msg_id=loading.message_id
        )

    except Exception as e:

        print(
            "latest error:",
            e
        )

        bot.edit_message_text(
            chat_id=
            message.chat.id,
            message_id=
            loading.message_id,
            text=
            (
                "⚠️ Failed to "
                "load latest "
                "stories."
            )
        )

@bot.message_handler(func=lambda m: m.text == "📚 Categories")
def cmd_categories(message: types.Message) -> None:
    track_user(message.from_user)
    state = nav_state.get(message.chat.id, {})
    show_categories(message.chat.id,
                    delete_msg_id=state.get("msg_id"))


@bot.message_handler(func=lambda m: m.text == "🔎 Search")
def cmd_search(message: types.Message) -> None:
    track_user(message.from_user)
    state = nav_state.get(message.chat.id, {})
    show_search_prompt(message.chat.id,
                       delete_msg_id=state.get("msg_id"))


@bot.message_handler(func=lambda m: m.chat.id in search_pending and
                                     m.reply_to_message is not None and
                                     m.reply_to_message.message_id == search_pending.get(m.chat.id))
def handle_search_reply(message: types.Message) -> None:
    track_user(message.from_user)
    chat_id  = message.chat.id
    query    = message.text.strip()
    prompt_id = search_pending.pop(chat_id, None)

    safe_delete(chat_id, prompt_id)
    safe_delete(chat_id, message.message_id)

    if not query:
        return
    show_search_results(chat_id, query, page=1)


@bot.message_handler(func=lambda m: m.chat.id in admin_msg_pending and
                                     m.reply_to_message is not None)
def handle_admin_message_reply(message: types.Message) -> None:
    """Handle admin reply to message prompt — send to target user."""
    chat_id = message.chat.id
    pending = admin_msg_pending.pop(chat_id, None)

    if not pending:
        return

    target_id = pending["target_id"]
    msg_text = message.text.strip()

    if not msg_text:
        bot.reply_to(message, "❌ Empty message. Nothing sent.")
        return

    try:
        # Send to target user
        bot.send_message(
            target_id,
            f"📩 <b>Message from Admin</b>\n\n{msg_text}",
            parse_mode="HTML"
        )

        # Confirm to admin
        bot.reply_to(
            message,
            f"✅ Message sent to <code>{target_id}</code>!",
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"admin message send error: {e}")
        bot.reply_to(
            message,
            f"❌ Failed to send to <code>{target_id}</code>.\nUser may have blocked the bot.",
            parse_mode="HTML"
        )


@bot.callback_query_handler(
    func=lambda call: True
)
def on_callback(
    call: types.CallbackQuery
) -> None:

    print(
        "CALLBACK:",
        call.data
    )

    # Smart loading feedback: instant ops get no popup, slow ops get "Loading..."
    # Page turns (spage_) and part nav (story_) are now instant from cache
    is_instant = (
        call.data.startswith("spage_") or      # page turn
        call.data.startswith("story_") or        # part navigation  
        call.data.startswith("related_") or      # related parts
        call.data.startswith("relparts_") or     # related pagination
        call.data.startswith("back_")            # back to list
    )

    try:
        if not is_instant:
            bot.answer_callback_query(call.id, "⏳ Loading...")
        else:
            # Just acknowledge without text — faster, no popup flash
            bot.answer_callback_query(call.id)
    except:
        pass

    route_callback(call)


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK
# ──────────────────────────────────────────────────────────────────────────────
threading.Thread(
    target=scheduler_loop,
    daemon=True
).start()
WEBHOOK_PATH = f"/{BOT_TOKEN}"


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():

    if request.headers.get(
        "content-type"
    ) == "application/json":

        json_string = (
            request.get_data()
            .decode("utf-8")
        )

        update = (
            telebot.types.Update
            .de_json(json_string)
        )

        try:
            bot.process_new_updates(
                [update]
            )

            print(
                "UPDATE RECEIVED"
            )

        except Exception as e:
            print(
                "WEBHOOK ERROR:",
                e
            )
            report_webhook_error(str(e))

        return "OK", 200

    return "Bad Request", 403


@app.route("/")
def home():
    return "Bot alive ✅", 200


# ──────────────────────────────────────────────────────────────────────────────
# SET WEBHOOK ON STARTUP
# ──────────────────────────────────────────────────────────────────────────────

RENDER_URL = os.getenv(
    "RENDER_URL"
)

if RENDER_URL:
    try:
        bot.remove_webhook()

        bot.set_webhook(
            url=f"{RENDER_URL}/{BOT_TOKEN}"
        )

        print("Webhook set successfully")

    except Exception as e:
        print("Webhook error:", e)
