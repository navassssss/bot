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
import logging
from time import sleep

import requests
import telebot
import requests
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
SUPABASE_URL = os.getenv(
    "SUPABASE_URL"
)

SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY"
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


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS: WordPress API
# ──────────────────────────────────────────────────────────────────────────────
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
        requests.post(
            f"{SUPABASE_URL}/rest/v1/users",
            headers={
                **HEADERS,
                "Prefer": "resolution=merge-duplicates"
            },
            json=data,
            timeout=10
        )

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
story_read_cache = {}
def can_count_story_read(user_id, post_id):
    key = f"{user_id}_{post_id}"

    now = time.time()

    if key in story_read_cache:
        if now - story_read_cache[key] < 60:
            return False

    story_read_cache[key] = now
    return True
    
@bot.message_handler(
    commands=["admin"]
)
def admin_panel(message):

    try:

        parts = (
            message.text.strip()
            .split(maxsplit=1)
        )

        if (
            len(parts) < 2
            or
            parts[1]
            != ADMIN_KEY
        ):

            bot.reply_to(
                message,
                "❌ Invalid key"
            )

            return

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


def _wp_get(endpoint: str, params: dict | None = None):
    """
    Wrapper for WordPress REST API requests.
    Returns (json_data, headers)
    """

    url = f"{WP_API}/{endpoint}"

    try:
        r = requests.get(
            url,
            params=params,
            timeout=20
        )

        r.raise_for_status()

        return r.json(), r.headers

    except requests.RequestException as e:
        raise RuntimeError(
            f"WordPress API error: {e}"
        )
WP_API = "https://kkstories.com/wp-json/wp/v2"       
def fetch_posts(page: int = 1, per_page: int = PER_PAGE,
                category: int | None = None,
                search: str | None = None) -> tuple[list, int]:
    """Return (posts_list, total_pages)."""
    params: dict = {"page": page, "per_page": per_page, "_fields": "id,title,author,categories"}
    if category:
        params["categories"] = category
    if search:
        params["search"] = search
    data, headers = _wp_get("posts", params)
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

def fetch_post_content(post_id: int) -> tuple[str, str, int]:
    """Return (title_html, content_html, real_author_id)."""

    data, _ = _wp_get(
        f"posts/{post_id}",
        {"_fields": "id,title,content"}
    )

    title = data["title"]["rendered"]
    content = data["content"]["rendered"]

    # Resolve fake author -> real author tag id
    author = fetch_real_author_id(post_id, content)

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
                                 per_page: int = 10):
    """
    Paginated posts by real author tag.
    """

    data, headers = _wp_get("posts", {
        "tags": author_id,
        "page": page,
        "per_page": per_page,
        "_fields": "id,title",
    })

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

def show_latest(chat_id: int, page: int = 1,
                delete_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)

    try:
        posts, total_pages = fetch_posts(page=page)
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Could not load posts: {e}")
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

    msg = bot.send_message(chat_id, "🔥 <b>Latest Stories</b>", reply_markup=kb)
    nav_state[chat_id] = {
        "type": "latest", "page": page, "msg_id": msg.message_id
    }


# ──────────────────────────────────────────────────────────────────────────────
# CORE: show_categories
# ──────────────────────────────────────────────────────────────────────────────

def show_categories(chat_id: int, delete_msg_id: int | None = None) -> None:
    safe_delete(chat_id, delete_msg_id)

    try:
        cats = fetch_categories()
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Could not load categories: {e}")
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

def show_category_posts(chat_id: int, cat_id: int, page: int = 1,
                        delete_msg_id: int | None = None,
                        cat_name: str = "") -> None:
    safe_delete(chat_id, delete_msg_id)

    try:
        posts, total_pages = fetch_posts(page=page, category=cat_id)
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Could not load category: {e}")
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
    try:
        posts, total_pages = fetch_posts(page=page, search=query)
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Search failed: {e}")
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

    msg = bot.send_message(
        chat_id,
        f"🔎 <b>{query}</b>",
        reply_markup=kb,
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
               list_msg_id: int | None = None) -> None:
    """
    Render / update the story message.
    existing_msg_id → edit this message (navigation within story)
    list_msg_id     → delete this before sending fresh story message
    back_ctx        → raw context string embedded in callback, e.g. "latest_3"
    """
    if can_count_story_read(chat_id, post_id):
               track_event(
                 chat_id,
                 "open_story",
                 str(post_id)
                )
               increment_user_reads(chat_id)
    try:
        title_html, content_html, author_id = fetch_post_content(post_id)
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Could not load story: {e}")
        return

    title_text = BeautifulSoup(title_html, "html.parser").get_text()
    pages      = get_story_parts(title_text, content_html)
    total_pages = len(pages)
    page        = max(1, min(page, total_pages))
    content     = pages[page - 1]

    # Part navigation
    print("TITLE:", title_text)
    print("PARSED:", _parse_title(title_text))
    prev_part, next_part = find_adjacent_parts(post_id, title_text, author_id)

    # Build keyboard
    kb = types.InlineKeyboardMarkup()

    # Story page pagination
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
        if parsed:
            series = _strip_author_tag(_html_to_text(prev_part["title"]["rendered"]))
            # Build a clean label: strip the number suffix to get just the series name
            label = f"⏮ Part {parsed[1]}"
        else:
            label = "⏮ Prev"
        part_row.append(types.InlineKeyboardButton(
            label,
            callback_data=f"story_{prev_part['id']}_1_{back_ctx}",
        ))
    if next_part:
        parsed = _parse_title(next_part["title"]["rendered"])
        if parsed:
            label = f"⏭ Part {parsed[1]}"
        else:
            label = "⏭ Next"
        part_row.append(types.InlineKeyboardButton(
            label,
            callback_data=f"story_{next_part['id']}_1_{back_ctx}",
        ))
    if part_row:
     kb.row(*part_row)
    else:
     kb.row(
        types.InlineKeyboardButton(
            "📚 Related Story Parts",
            callback_data=(
                f"related_{author_id}_{post_id}_{back_ctx}"
            )
        )
    )

    # Back button
    kb.row(types.InlineKeyboardButton("⬅ Back", callback_data=f"back_{back_ctx}"))

    # Message text — trim to Telegram 4096 limit
    header = f"📖 <b>{title_text}</b>\n📄 Page {page}/{total_pages}\n\n"
    max_content = 4096 - len(header) - 20
    if len(content) > max_content:
        content = content[:max_content] + "…"
    text = header + content

    if existing_msg_id:
        safe_edit(chat_id, existing_msg_id, text, reply_markup=kb)
    else:
        safe_delete(chat_id, list_msg_id)
        bot.send_message(chat_id, text, reply_markup=kb)

def show_related_story_parts(chat_id: int,
                             author_id: int,
                             current_post_id: int,
                             page: int,
                             back_ctx: str,
                             msg_id: int):

    try:
        posts, total_pages = fetch_author_posts_paginated(
            author_id,
            page=page,
            per_page=10
        )
    except Exception as e:
        bot.send_message(
            chat_id,
            f"⚠️ Could not load author posts: {e}"
        )
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

    safe_edit(
        chat_id,
        msg_id,
        "📚 <b>Related Story Parts</b>\n\n"
        "Part buttons unavailable.\n"
        "Browse author's posts below:",
        reply_markup=kb
    )
# ──────────────────────────────────────────────────────────────────────────────
# CALLBACK ROUTING
# ──────────────────────────────────────────────────────────────────────────────

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
                            delete_msg_id=msg_id, cat_name=cat_name)
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
              msg_id=msg_id
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
             msg_id=msg_id
        )
        return


    # ── Back ───────────────────────────────────────────────────────────────
    # callback: back_{back_ctx}
    m = re.match(r"^back_(.+)$", data)
    if m:
        back_ctx = m.group(1)
        _handle_back(chat_id, msg_id, back_ctx)
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

@bot.message_handler(commands=["start", "help"])
def cmd_start(message):

    print("START HANDLER RUNNING")

    try:
        track_user(message.from_user)

        print("TRACK USER OK")

        show_home(message)

        print("SHOW HOME OK")

    except Exception as e:
        print("START ERROR:", e)


@bot.message_handler(func=lambda m: m.text == "🔥 Latest")
def cmd_latest(message: types.Message) -> None:
    track_user(message.from_user)
    # Delete old list message if present
    state = nav_state.get(message.chat.id, {})
    show_latest(message.chat.id, page=1,
                delete_msg_id=state.get("msg_id"))


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

    route_callback(call)


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK
# ──────────────────────────────────────────────────────────────────────────────

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
