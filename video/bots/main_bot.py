"""
Main Bot - The SAFE bot that NEVER sends videos.

Responsibilities:
- Handle /start command with deep link parameters
- Validate tokens and check video availability
- Verify user membership
- Apply rate limiting
- Generate secure redirect links to the Delivery Bot
- Provide user interface (buttons, messages)

Architecture:
- This bot is the public-facing entry point
- It NEVER sends media files - only text and inline buttons
- This separation is CRITICAL for ban avoidance
- If this bot gets limited, the delivery bot can still function
- The delivery bot is isolated and can be swapped without affecting users

Anti-Ban Strategy:
1. Never sends video/media files
2. Only sends text messages and inline keyboard buttons
3. Uses signed, expiring tokens to prevent abuse
4. Rate limits users
5. Membership verification before access
"""
import asyncio
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from utils.config import Config
from utils.logger import get_logger
from utils.tokens import token_manager, TokenExpiredError, TokenInvalidError
from database.db import db
from services.membership import membership_service

logger = get_logger("main_bot")

# Create router for handlers
main_router = Router()


class MainBot:
    """
    Main Telegram Bot - Safe, media-free entry point.

    This bot handles all user interactions except video delivery.
    It generates secure tokens that the Delivery Bot validates.
    """

    def __init__(self):
        self.bot = Bot(token=Config.MAIN_BOT_TOKEN)
        self.dp = Dispatcher()
        self.dp.include_router(main_router)
        self._running = False

    async def start(self) -> None:
        """Start the main bot."""
        logger.info("main_bot_starting")

        # Register handlers
        self._register_handlers()

        # Add source channel as required membership (optional)
        # membership_service.add_required_channel(Config.SOURCE_CHANNEL)

        self._running = True

        # Start polling in background
        asyncio.create_task(self.dp.start_polling(self.bot))
        logger.info("main_bot_started")

    async def stop(self) -> None:
        """Gracefully stop the main bot."""
        logger.info("main_bot_stopping")
        self._running = False
        await self.bot.session.close()
        logger.info("main_bot_stopped")

    def _register_handlers(self) -> None:
        """Register all message and callback handlers."""

        @main_router.message(CommandStart(deep_link=True))
        async def cmd_start_deep_link(message: Message, command: CommandStart):
            """
            Handle /start with a parameter (deep link).

            Expected format: /start <video_id>
            The video_id is validated and a secure token is generated.
            """
            user_id = message.from_user.id

            # Extract video ID from deep link
            args = command.args
            if not args:
                await message.answer(
                    "❌ Invalid link. Please use the link provided in the channel."
                )
                return

            try:
                video_id = int(args)
            except ValueError:
                await message.answer(
                    "❌ Invalid video ID. Please use a valid link from the channel."
                )
                return

            # Rate limiting check
            allowed = await db.check_rate_limit(user_id, Config.RATE_LIMIT_PER_HOUR)
            if not allowed:
                await message.answer(
                    f"⏳ Rate limit exceeded. You can request up to {Config.RATE_LIMIT_PER_HOUR} videos per hour.\n"
                    "Please try again later."
                )
                return

            # Membership check (if configured)
            is_member = await membership_service.check_membership(self.bot, user_id)
            if not is_member:
                await message.answer(
                    "🔒 Access restricted. Please join our channel to access videos."
                )
                return

            # Check video exists and is ready
            video = await db.get_video_by_id(video_id)
            if not video:
                await message.answer(
                    "❌ Video not found. It may have been removed or is no longer available."
                )
                return

            if video["status"] != "ready":
                status_messages = {
                    "pending": "⏳ This video is queued for processing. Please check back in a few minutes.",
                    "processing": "🔄 This video is currently being processed. Please wait a moment.",
                    "failed": "❌ This video failed to process. Please try another video."
                }
                await message.answer(status_messages.get(video["status"], "❌ Video unavailable."))
                return

            # Generate secure token
            token = token_manager.generate(video_id)

            # Build delivery bot deep link
            delivery_bot_username = await self._get_delivery_bot_username()
            deep_link = f"https://t.me/{delivery_bot_username}?start={token}"

            # Send response with inline button
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 Watch Video", url=deep_link)]
            ])

            await message.answer(
                "✅ <b>Video Ready!</b>\n\n"
                "Click the button below to receive your video.\n"
                "The link expires in 10 minutes for security.",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

            logger.info("main_bot_deep_link_handled", 
                       user_id=user_id, video_id=video_id)

        @main_router.message(CommandStart(deep_link=False))
        async def cmd_start_plain(message: Message):
            """Handle plain /start without parameters."""
            await message.answer(
                "👋 <b>Welcome!</b>\n\n"
                "I\'m your video access bot.\n"
                "Use the links from our channel to access videos instantly.\n\n"
                "⚡ Fast delivery\n"
                "🔒 Secure access\n"
                "📱 Works on all devices",
                parse_mode=ParseMode.HTML
            )

        @main_router.message(Command("help"))
        async def cmd_help(message: Message):
            """Handle /help command."""
            await message.answer(
                "<b>How to use:</b>\n\n"
                "1. Join our channel for video updates\n"
                "2. Click on video links in the channel\n"
                "3. I\'ll verify your access and send you a secure link\n"
                "4. Click the link to receive the video\n\n"
                f"📊 Rate limit: {Config.RATE_LIMIT_PER_HOUR} videos/hour\n"
                "⏱️ Links expire in 10 minutes",
                parse_mode=ParseMode.HTML
            )

        @main_router.message()
        async def handle_any_message(message: Message):
            """Handle any other message."""
            await message.answer(
                "Please use the links from our channel to access videos.\n"
                "Send /start to see the welcome message."
            )

    async def _get_delivery_bot_username(self) -> str:
        """Get the delivery bot username from the bot token."""
        # In production, you might want to fetch this from the database
        # or have it configurable. For now, we get it from the bot info.
        me = await self.bot.get_me()
        # The delivery bot username should be stored in config or database
        # For this example, we assume it's known or fetched dynamically
        # You can also query the delivery bot directly if needed

        # Fallback: extract from token or use config
        # In a real setup, store delivery bot usernames in delivery_bots table
        from aiogram import Bot as AioBot
        temp_bot = AioBot(token=Config.DELIVERY_BOT_TOKEN)
        try:
            delivery_me = await temp_bot.get_me()
            return delivery_me.username
        finally:
            await temp_bot.session.close()


# Singleton instance
main_bot = MainBot()
