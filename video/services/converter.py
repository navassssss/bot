"""
Converter bot integration service.

Responsibilities:
- Send TaraBox links to a third-party converter bot
- Wait for and receive the converted video
- Handle timeouts, retries, and rate limits
- Queue-based processing (one conversion at a time)
- Immediately save video to archive channel upon receipt

Architecture:
- Uses asyncio.Queue for sequential processing
- Each conversion is a state machine: pending -> sent -> waiting -> received -> archived
- Handles FloodWaitError with exponential backoff
- Auto-retry on failure (max 3 attempts per video)
- Converter bot auto-deletes files after 1 hour, so we must archive IMMEDIATELY
"""
import asyncio
import time
from typing import Optional, Dict, Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

from utils.config import Config
from utils.logger import get_logger
from database.db import db
from services.archive import archive_service

logger = get_logger("converter")

# Configuration
MAX_RETRIES = 3
CONVERSION_TIMEOUT = 300  # 5 minutes max wait for converter response
RETRY_DELAY_BASE = 10     # Base delay for retries (seconds)


class ConverterService:
    """
    Manages interactions with the third-party converter bot.

    Flow:
    1. Take TaraBox link from queue
    2. Send link to converter bot
    3. Wait for video response
    4. Immediately archive the video
    5. Update database status

    Safety:
    - Only ONE conversion at a time (sequential queue)
    - This prevents overwhelming the converter bot and reduces ban risk
    - Each video has retry logic with exponential backoff
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._converter_entity = None

    async def start(self, client: TelegramClient) -> None:
        """Start the converter service with a shared Telethon client."""
        logger.info("converter_starting")
        self.client = client

        # Get converter bot entity
        try:
            self._converter_entity = await self.client.get_entity(Config.CONVERTER_BOT)
            logger.info("converter_bot_resolved", bot=Config.CONVERTER_BOT)
        except Exception as e:
            logger.error("converter_bot_not_found", bot=Config.CONVERTER_BOT, error=str(e))
            raise

        self._running = True
        self._task = asyncio.create_task(self._process_queue())
        logger.info("converter_started")

    async def stop(self) -> None:
        """Gracefully stop the converter service."""
        logger.info("converter_stopping")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("converter_stopped")

    async def enqueue(self, video_id: int) -> None:
        """Add a video to the conversion queue."""
        await self.queue.put(video_id)
        logger.info("converter_enqueued", video_id=video_id, queue_size=self.queue.qsize())

    async def _process_queue(self) -> None:
        """Main queue processing loop."""
        while self._running:
            try:
                video_id = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                await self._process_video(video_id)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("converter_queue_error", error=str(e))
                await asyncio.sleep(5)

    async def _process_video(self, video_id: int) -> None:
        """
        Process a single video conversion with retry logic.

        State machine:
        pending -> processing -> [ready | failed]
        """
        video = await db.get_video_by_id(video_id)
        if not video:
            logger.error("converter_video_not_found", video_id=video_id)
            return

        tarabox_link = video["tarabox_link"]

        # Mark as processing
        await db.update_video_status(video_id, "processing")
        logger.info("converter_processing", video_id=video_id, link=tarabox_link)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                success = await self._attempt_conversion(video_id, tarabox_link)
                if success:
                    return
            except FloodWaitError as e:
                wait_time = e.seconds
                logger.warning("converter_flood_wait", video_id=video_id, wait_seconds=wait_time)
                await asyncio.sleep(wait_time)
            except Exception as e:
                logger.error("converter_attempt_failed", 
                           video_id=video_id, attempt=attempt, error=str(e))
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    logger.info("converter_retrying", video_id=video_id, delay=delay)
                    await asyncio.sleep(delay)

        # All retries exhausted
        await db.update_video_status(video_id, "failed", error_message="Max retries exceeded")
        logger.error("converter_failed_permanently", video_id=video_id)

    async def _attempt_conversion(self, video_id: int, tarabox_link: str) -> bool:
        """
        Single conversion attempt.

        Steps:
        1. Send TaraBox link to converter bot
        2. Wait for response containing video
        3. Extract video message
        4. Archive the video immediately
        5. Update database

        Returns True on success, False on failure (will trigger retry).
        """
        # Send the link to converter bot
        sent_msg = await self.client.send_message(
            self._converter_entity,
            tarabox_link
        )
        logger.info("converter_link_sent", video_id=video_id, message_id=sent_msg.id)

        # Wait for converter response with timeout
        video_message = await self._wait_for_video_response(sent_msg.id)

        if not video_message:
            logger.warning("converter_no_response", video_id=video_id)
            return False

        if not video_message.video:
            logger.warning("converter_no_video_in_response", video_id=video_id)
            return False

        # CRITICAL: Archive immediately before converter deletes it
        logger.info("converter_video_received", video_id=video_id, 
                   size=video_message.video.size, duration=video_message.video.duration)

        try:
            archive_message_id = await archive_service.archive_video(
                self.client, video_message
            )

            if archive_message_id:
                await db.update_video_status(
                    video_id, "ready", 
                    archive_message_id=archive_message_id
                )
                logger.info("converter_archived", 
                           video_id=video_id, archive_message_id=archive_message_id)
                return True
            else:
                logger.error("converter_archive_failed", video_id=video_id)
                return False

        except Exception as e:
            logger.error("converter_archive_error", video_id=video_id, error=str(e))
            return False

    async def _wait_for_video_response(self, sent_message_id: int) -> Optional[Message]:
        """
        Wait for the converter bot to respond with a video.

        We listen for new messages from the converter bot that come AFTER
        our sent message. The converter bot typically replies in the same chat.

        Returns the video message or None if timeout.
        """
        start_time = time.time()

        while time.time() - start_time < CONVERSION_TIMEOUT:
            if not self._running:
                return None

            try:
                # Get recent messages from converter bot
                messages = await self.client.get_messages(
                    self._converter_entity,
                    limit=10
                )

                for msg in messages:
                    # Check if this is a response to our message
                    # Converter bots usually reply or send a new message
                    if msg.id > sent_message_id and msg.video:
                        return msg

                    # Some converters send text first, then video
                    # Check for error messages
                    if msg.id > sent_message_id and msg.text:
                        error_keywords = ["error", "failed", "invalid", "not found", 
                                        "unsupported", "expired", "deleted"]
                        if any(kw in msg.text.lower() for kw in error_keywords):
                            logger.warning("converter_error_response", 
                                         message_id=msg.id, text=msg.text[:200])
                            return None

                # Wait before checking again
                await asyncio.sleep(5)

            except FloodWaitError as e:
                logger.warning("converter_wait_flood", wait_seconds=e.seconds)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error("converter_wait_error", error=str(e))
                await asyncio.sleep(5)

        logger.warning("converter_timeout", timeout=CONVERSION_TIMEOUT)
        return None


# Singleton instance
converter_service = ConverterService()
