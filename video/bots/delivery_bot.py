"""
Delivery Bot - The isolated bot that sends videos.

Responsibilities:
- Receive secure tokens via /start <token>
- Validate tokens (signature + expiry)
- Lookup archive_message_id from database
- Send video to user via copy_message()
- Increment request_count and log delivery
- Handle rate limits and errors

Architecture:
- This bot is ISOLATED from the main bot
- It ONLY receives deep links with tokens and sends videos
- It does NOT handle general chat, commands, or user interaction
- This separation means:
  1. If delivery bot gets rate-limited, main bot still works
  2. If delivery bot gets banned, we can spin up a new one instantly
  3. Main bot (public-facing) never touches media, reducing its risk
  4. We can run multiple delivery bots for load distribution

Anti-Ban Strategy:
1. Only responds to valid tokens (no open chat)
2. Uses copy_message() (no forward attribution)
3. Rate limiting per user
4. Delivery logging for audit
5. Can be replaced without affecting the main system
"""
import asyncio
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from utils.config import Config
from utils.logger import get_logger
from utils.tokens import token_manager, TokenExpiredError, TokenInvalidError
from database.db import db

logger = get_logger("delivery_bot")

# Create router for handlers
delivery_router = Router()


class DeliveryBot:
    """
    Delivery Telegram Bot - Isolated video sender.

    This bot ONLY handles /start <token> and sends videos.
    No other commands, no chat, no interaction.
    """

    def __init__(self):
        self.bot = Bot(token=Config.DELIVERY_BOT_TOKEN)
        self.dp = Dispatcher()
        self.dp.include_router(delivery_router)
        self._running = False

    async def start(self) -> None:
        """Start the delivery bot."""
        logger.info("delivery_bot_starting")

        # Register handlers
        self._register_handlers()

        self._running = True

        # Start polling in background
        asyncio.create_task(self.dp.start_polling(self.bot))
        logger.info("delivery_bot_started")

    async def stop(self) -> None:
        """Gracefully stop the delivery bot."""
        logger.info("delivery_bot_stopping")
        self._running = False
        await self.bot.session.close()
        logger.info("delivery_bot_stopped")

    def _register_handlers(self) -> None:
        """Register the token-based delivery handler."""

        @delivery_router.message(CommandStart(deep_link=True))
        async def cmd_start_token(message: Message, command: CommandStart):
            """
            Handle /start with a secure token.

            Flow:
            1. Validate token (signature + expiry)
            2. Lookup video in database
            3. Check video is ready
            4. Send video via copy_message()
            5. Log delivery
            """
            user_id = message.from_user.id
            token = command.args

            if not token:
                await message.answer("❌ Invalid access link.")
                return

            # Validate token
            try:
                video_id = token_manager.validate(token)
            except TokenExpiredError:
                await message.answer(
                    "⏰ <b>Link Expired</b>\n\n"
                    "This link has expired for security reasons.\n"
                    "Please go back to the channel and request the video again.",
                    parse_mode=ParseMode.HTML
                )
                return
            except TokenInvalidError:
                await message.answer(
                    "🔒 <b>Invalid Link</b>\n\n"
                    "This access link is invalid.\n"
                    "Please use the official links from our channel.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Lookup video
            video = await db.get_video_by_id(video_id)
            if not video:
                await message.answer(
                    "❌ <b>Video Not Found</b>\n\n"
                    "This video is no longer available.",
                    parse_mode=ParseMode.HTML
                )
                return

            if video["status"] != "ready":
                await message.answer(
                    "❌ <b>Video Unavailable</b>\n\n"
                    "This video is not ready for delivery yet.\n"
                    "Please try again later.",
                    parse_mode=ParseMode.HTML
                )
                return

            archive_message_id = video.get("archive_message_id")
            if not archive_message_id:
                await message.answer(
                    "❌ <b>Delivery Error</b>\n\n"
                    "Video archive reference is missing.\n"
                    "Please contact support.",
                    parse_mode=ParseMode.HTML
                )
                logger.error("delivery_missing_archive_id", video_id=video_id)
                return

            # Rate limiting check
            allowed = await db.check_rate_limit(user_id, Config.RATE_LIMIT_PER_HOUR)
            if not allowed:
                await message.answer(
                    f"⏳ <b>Rate Limit</b>\n\n"
                    f"You\'ve reached your limit of {Config.RATE_LIMIT_PER_HOUR} videos per hour.\n"
                    "Please try again later.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Send video via copy_message
            # This is the CRITICAL operation - copy_message avoids forward attribution
            try:
                await self.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=Config.ARCHIVE_CHANNEL,
                    message_id=archive_message_id,
                    caption="🎬 Here\'s your video! Enjoy! 🍿"
                )

                # Update stats
                await db.increment_request_count(video_id)
                await db.log_delivery(video_id, user_id, bot_id=None, success=True)

                logger.info("delivery_success", 
                           user_id=user_id, video_id=video_id, 
                           archive_message_id=archive_message_id)

            except TelegramRetryAfter as e:
                # Rate limit from Telegram
                logger.warning("delivery_rate_limited", 
                             user_id=user_id, retry_after=e.retry_after)
                await message.answer(
                    f"⏳ <b>Please Wait</b>\n\n"
                    f"Telegram rate limit active. Please wait {e.retry_after} seconds.",
                    parse_mode=ParseMode.HTML
                )
                await db.log_delivery(video_id, user_id, bot_id=None, 
                                    success=False, error_message=f"Rate limit: {e.retry_after}s")

            except TelegramAPIError as e:
                logger.error("delivery_api_error", 
                           user_id=user_id, video_id=video_id, error=str(e))
                await message.answer(
                    "❌ <b>Delivery Failed</b>\n\n"
                    "An error occurred while sending the video.\n"
                    "Please try again in a moment.",
                    parse_mode=ParseMode.HTML
                )
                await db.log_delivery(video_id, user_id, bot_id=None, 
                                    success=False, error_message=str(e))

            except Exception as e:
                logger.error("delivery_unexpected_error", 
                           user_id=user_id, video_id=video_id, error=str(e))
                await message.answer(
                    "❌ <b>Unexpected Error</b>\n\n"
                    "Something went wrong. Please try again later.",
                    parse_mode=ParseMode.HTML
                )
                await db.log_delivery(video_id, user_id, bot_id=None, 
                                    success=False, error_message=str(e))

        @delivery_router.message(CommandStart(deep_link=False))
        async def cmd_start_plain(message: Message):
            """Handle plain /start - delivery bot doesn't chat."""
            await message.answer(
                "🤖 <b>Delivery Bot</b>\n\n"
                "I only deliver videos via secure links.\n"
                "Please use the links from our main bot or channel.",
                parse_mode=ParseMode.HTML
            )

        @delivery_router.message()
        async def handle_any_message(message: Message):
            """Ignore all other messages."""
            await message.answer(
                "Please use the secure link from the main bot to receive videos."
            )


# Singleton instance
delivery_bot = DeliveryBot()
