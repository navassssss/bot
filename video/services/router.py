"""
Router service: Orchestrates the conversion pipeline.

Responsibilities:
- Periodically check database for pending videos
- Enqueue them for conversion
- Monitor conversion progress
- Handle the scraper -> converter -> archive flow

This is the central coordinator that connects all services.
"""
import asyncio
from typing import Optional

from utils.config import Config
from utils.logger import get_logger
from database.db import db
from services.converter import converter_service

logger = get_logger("router")

# How often to check for new pending videos
PENDING_CHECK_INTERVAL = 30  # seconds


class RouterService:
    """
    Orchestrates the end-to-end pipeline:

    Scraper finds links -> Database stores them -> Router enqueues -> 
    Converter processes -> Archive stores -> Ready for delivery

    The router runs as a background task and ensures the pipeline
    keeps moving even if individual components have issues.
    """

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the router background task."""
        logger.info("router_starting")
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("router_started")

    async def stop(self) -> None:
        """Gracefully stop the router."""
        logger.info("router_stopping")
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("router_stopped")

    async def _run_loop(self) -> None:
        """Main router loop."""
        while self._running:
            try:
                await self._process_pending_videos()
            except Exception as e:
                logger.error("router_loop_error", error=str(e))

            await asyncio.sleep(PENDING_CHECK_INTERVAL)

    async def _process_pending_videos(self) -> None:
        """
        Find pending videos and enqueue them for conversion.

        Only enqueue videos that aren't already in the queue or being processed.
        The converter service handles deduplication via its queue.
        """
        pending = await db.get_pending_videos(limit=50)

        if pending:
            logger.info("router_found_pending", count=len(pending))

            for video in pending:
                if not self._running:
                    break

                # Mark as processing before enqueue to prevent double-processing
                await db.update_video_status(video["id"], "processing")
                await converter_service.enqueue(video["id"])

                # Small delay to avoid overwhelming the converter
                await asyncio.sleep(2)


# Singleton instance
router_service = RouterService()
