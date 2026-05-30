"""
Telethon-based scraper for the private source channel.

Responsibilities:
- Connect as a Telegram user (not bot) to read private channels
- Extract TaraBox links from messages (text, captions, hidden URLs)
- Process both historical and new messages
- Save progress to database for crash recovery
- Avoid duplicates via UNIQUE constraint on tarabox_link

Architecture:
- Runs as a background task in the main asyncio loop
- Uses regex to find TaraBox links
- Processes messages in batches to avoid memory issues
- Gracefully handles FloodWaitError and connection drops
"""
import asyncio
import re
from typing import List, Optional, Set

from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telethon.tl.types import Message, MessageMediaWebPage

from utils.config import Config
from utils.logger import get_logger
from database.db import db

logger = get_logger("scraper")

# Regex pattern for TaraBox links
# Matches: tarabox.com, tarabox.io, tarabox.net, etc.
TARABOX_PATTERN = re.compile(
    r"https?://(?:www\.)?tarabox\.[a-z]{2,6}/[a-zA-Z0-9_-]+",
    re.IGNORECASE
)

# Also match tara.box variants
TARABOX_PATTERN_ALT = re.compile(
    r"https?://(?:www\.)?tara\.box/[a-zA-Z0-9_-]+",
    re.IGNORECASE
)


class ChannelScraper:
    """
    Scrapes a private Telegram channel for TaraBox links.

    Uses Telethon user client because:
    1. Bot API cannot read messages from private channels the bot isn't in
    2. We need access to message history
    3. We need to see hidden URLs in message entities
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Initialize and start the scraper."""
        logger.info("scraper_starting")

        self.client = TelegramClient(
            str(Config.SESSION_PATH),
            Config.API_ID,
            Config.API_HASH
        )

        # Connect and authenticate
        await self.client.connect()

        if not await self.client.is_user_authorized():
            logger.info("scraper_authorization_required")
            await self.client.start(phone=Config.PHONE_NUMBER)
            logger.info("scraper_authorized")

        self._running = True
        self._task = asyncio.create_task(self._scrape_loop())
        logger.info("scraper_started")

    async def stop(self) -> None:
        """Gracefully stop the scraper."""
        logger.info("scraper_stopping")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self.client:
            await self.client.disconnect()

        logger.info("scraper_stopped")

    async def _scrape_loop(self) -> None:
        """Main scraping loop with crash recovery."""
        while self._running:
            try:
                await self._process_messages()
            except FloodWaitError as e:
                wait_time = e.seconds
                logger.warning("scraper_flood_wait", wait_seconds=wait_time)
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error("scraper_loop_error", error=str(e))
                await asyncio.sleep(30)  # Brief pause before retry

            # Wait before next check
            await asyncio.sleep(Config.SCRAPE_INTERVAL)

    async def _process_messages(self) -> None:
        """
        Process messages from the source channel.

        Strategy:
        1. Get last processed message ID from database
        2. Fetch messages newer than that ID
        3. On first run (last_id=0), fetch historical messages too
        4. Extract TaraBox links and save to database
        5. Update last_message_id
        """
        last_id = await db.get_last_message_id()

        logger.info("scraper_processing", last_message_id=last_id)

        # Get entity for the source channel
        entity = await self.client.get_entity(Config.SOURCE_CHANNEL)

        new_links_found = 0
        max_message_id = last_id

        if last_id == 0:
            # First run: process ALL historical messages
            logger.info("scraper_historical_sync")
            async for message in self.client.iter_messages(
                entity,
                reverse=True,  # Oldest first
                limit=None     # All messages
            ):
                if not self._running:
                    break

                links = self._extract_links(message)
                for link in links:
                    video_id = await db.insert_video(link)
                    if video_id:
                        new_links_found += 1

                if message.id > max_message_id:
                    max_message_id = message.id

                # Save progress every 100 messages to allow crash recovery
                if message.id % 100 == 0:
                    await db.set_last_message_id(max_message_id)
                    logger.info("scraper_progress", message_id=max_message_id, new_links=new_links_found)
        else:
            # Normal run: only new messages
            async for message in self.client.iter_messages(
                entity,
                min_id=last_id,
                limit=100  # Process in batches
            ):
                if not self._running:
                    break

                links = self._extract_links(message)
                for link in links:
                    video_id = await db.insert_video(link)
                    if video_id:
                        new_links_found += 1

                if message.id > max_message_id:
                    max_message_id = message.id

        # Update last processed message ID
        if max_message_id > last_id:
            await db.set_last_message_id(max_message_id)

        if new_links_found > 0:
            logger.info("scraper_batch_complete", new_links=new_links_found, max_id=max_message_id)

    def _extract_links(self, message: Message) -> List[str]:
        """
        Extract TaraBox links from a message.

        Checks:
        1. Message text/caption
        2. Hidden URLs in message entities (buttons, links)
        3. Web page preview URLs
        """
        links: Set[str] = set()

        # Check message text and caption
        text = message.text or message.caption or ""
        if text:
            links.update(TARABOX_PATTERN.findall(text))
            links.update(TARABOX_PATTERN_ALT.findall(text))

        # Check entities (hidden URLs, buttons, etc.)
        if message.entities:
            for entity in message.entities:
                if hasattr(entity, "url") and entity.url:
                    url = entity.url
                    if TARABOX_PATTERN.search(url) or TARABOX_PATTERN_ALT.search(url):
                        links.add(url)

        # Check web page preview
        if message.media and isinstance(message.media, MessageMediaWebPage):
            if message.media.webpage and message.media.webpage.url:
                url = message.media.webpage.url
                if TARABOX_PATTERN.search(url) or TARABOX_PATTERN_ALT.search(url):
                    links.add(url)

        return list(links)


# Singleton instance
scraper = ChannelScraper()
