"""
Main entry point for the Telegram Video Delivery System.

Orchestrates all components:
1. Database initialization
2. Telethon scraper (userbot)
3. Converter service (queue-based)
4. Router service (pipeline coordinator)
5. Main Bot (safe, public-facing)
6. Delivery Bot (isolated video sender)

Architecture:
- All components run concurrently in a single asyncio event loop
- The scraper and converter share a Telethon client
- Both bots run via aiogram polling
- The router connects scraper output to converter input
- Graceful shutdown on SIGINT/SIGTERM

Production Notes:
- Run with a process manager (systemd, supervisord, pm2)
- Monitor logs for errors and rate limits
- Keep session file persistent across restarts
- Use SQLite WAL mode for better concurrency
- Consider PostgreSQL for high-scale deployments
"""
import asyncio
import signal
import sys
from typing import List

from utils.config import Config
from utils.logger import configure_logging, get_logger
from database.db import db

from services.scraper import scraper
from services.converter import converter_service
from services.router import router_service
from services.archive import archive_service

from bots.main_bot import main_bot
from bots.delivery_bot import delivery_bot

logger = get_logger("run")

# Global flag for graceful shutdown
_shutdown_event = asyncio.Event()


async def shutdown(signal_name: str) -> None:
    """Graceful shutdown handler."""
    logger.info("shutdown_initiated", signal=signal_name)
    _shutdown_event.set()

    # Stop all services in reverse order
    await delivery_bot.stop()
    await main_bot.stop()
    await router_service.stop()
    await converter_service.stop()
    await scraper.stop()
    await db.close()

    logger.info("shutdown_complete")


def setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Setup OS signal handlers for graceful shutdown."""
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig:
            loop.add_signal_handler(sig, 
                lambda s=sig_name: asyncio.create_task(shutdown(s)))


async def main() -> None:
    """Main application entry point."""

    # Initialize logging
    configure_logging()
    logger.info("application_starting")

    # Validate configuration
    try:
        Config.validate()
        logger.info("configuration_validated")
    except ValueError as e:
        logger.error("configuration_invalid", error=str(e))
        sys.exit(1)

    # Initialize database
    await db.connect()
    logger.info("database_initialized")

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    # Start scraper (Telethon user client)
    await scraper.start()

    # Start converter service (shares scraper\'s client)
    await converter_service.start(scraper.client)

    # Start router (connects scraper to converter)
    await router_service.start()

    # Start bots
    await main_bot.start()
    await delivery_bot.start()

    logger.info("all_services_started")

    # Keep running until shutdown signal
    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    logger.info("application_exiting")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("fatal_error", error=str(e))
        sys.exit(1)
