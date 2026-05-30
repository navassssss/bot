"""
Archive channel service.

Responsibilities:
- Copy received videos to a private archive channel
- Save the archive message ID in the database
- Use copy_message() (NOT forward) to avoid source attribution
- Handle FloodWaitError and retry

Architecture:
- The archive channel is PRIVATE and never exposed to users
- Videos are stored as Telegram-native media (not files)
- Delivery bots use copy_message() from the archive to users
- This creates a clean separation: archive stores, delivery bots distribute
"""
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

from utils.config import Config
from utils.logger import get_logger

logger = get_logger("archive")

# Retry configuration
ARCHIVE_MAX_RETRIES = 3
ARCHIVE_RETRY_DELAY = 5


class ArchiveService:
    """
    Manages the private archive channel.

    Key design decisions:
    1. copy_message() instead of forward_message():
       - forward_message() preserves source attribution (shows "Forwarded from...")
       - copy_message() creates a fresh message without attribution
       - This is critical for ban avoidance - no trace of the converter bot

    2. Private channel:
       - Archive channel is private, not discoverable
       - Only our bots and user account are members
       - Users never see the archive channel directly

    3. Immediate archiving:
       - Converter bot auto-deletes after 1 hour
       - We must archive within minutes of receiving the video
       - The converter service calls archive_service.archive_video() immediately
    """

    async def archive_video(
        self, 
        client: TelegramClient, 
        video_message: Message
    ) -> Optional[int]:
        """
        Copy a video message to the archive channel.

        Args:
            client: Active Telethon client
            video_message: The Message object containing the video

        Returns:
            The archive message ID, or None on failure
        """
        for attempt in range(1, ARCHIVE_MAX_RETRIES + 1):
            try:
                # Use copy_message to avoid "Forwarded from" attribution
                # This is CRITICAL for ban avoidance
                copied = await client.send_message(
                    Config.ARCHIVE_CHANNEL,
                    file=video_message.video,
                    caption=video_message.caption or "",
                    parse_mode=None,
                    silent=True  # Send without notification sound
                )

                logger.info("archive_video_copied", 
                           original_id=video_message.id,
                           archive_id=copied.id,
                           size=video_message.video.size)

                return copied.id

            except FloodWaitError as e:
                logger.warning("archive_flood_wait", 
                             wait_seconds=e.seconds, attempt=attempt)
                await asyncio.sleep(e.seconds)

            except Exception as e:
                logger.error("archive_copy_failed", 
                           attempt=attempt, error=str(e))
                if attempt < ARCHIVE_MAX_RETRIES:
                    import asyncio
                    await asyncio.sleep(ARCHIVE_RETRY_DELAY * attempt)

        logger.error("archive_permanently_failed", 
                    original_id=video_message.id)
        return None


# Singleton instance
archive_service = ArchiveService()
