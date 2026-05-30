"""
Async database layer using aiosqlite.

Provides:
- Connection pooling (single connection per module, reused)
- Schema initialization on startup
- All CRUD operations for the video delivery system
- Rate limiting helpers
- Delivery logging
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import aiosqlite

from utils.config import Config
from utils.logger import get_logger

logger = get_logger("database")


class Database:
    """
    Async SQLite database manager.

    Uses aiosqlite for non-blocking database operations.
    All methods are async to prevent blocking the event loop.
    """

    _instance: Optional["Database"] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._db: Optional[aiosqlite.Connection] = None
        return cls._instance

    async def connect(self) -> None:
        """Initialize database connection and schema."""
        if self._db is not None:
            return

        self._db = await aiosqlite.connect(str(Config.DB_PATH))
        self._db.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrency
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        # Initialize schema
        await self._init_schema()
        logger.info("database_connected", path=str(Config.DB_PATH))

    async def _init_schema(self) -> None:
        """Execute schema.sql to create tables and indexes."""
        schema_path = Config.DB_PATH.parent / "schema.sql"
        if schema_path.exists():
            with open(schema_path, "r") as f:
                schema = f.read()
            await self._db.executescript(schema)
            await self._db.commit()

    async def close(self) -> None:
        """Close database connection gracefully."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("database_closed")

    # -------------------------------------------------------------------------
    # Video Operations
    # -------------------------------------------------------------------------

    async def insert_video(self, tarabox_link: str) -> Optional[int]:
        """
        Insert a new video record. Returns the ID if inserted, None if duplicate.
        """
        try:
            cursor = await self._db.execute(
                "INSERT OR IGNORE INTO videos (tarabox_link, status) VALUES (?, ?)",
                (tarabox_link, "pending")
            )
            await self._db.commit()
            if cursor.lastrowid:
                logger.info("video_inserted", tarabox_link=tarabox_link, id=cursor.lastrowid)
                return cursor.lastrowid
            return None
        except Exception as e:
            logger.error("video_insert_failed", tarabox_link=tarabox_link, error=str(e))
            raise

    async def get_video_by_link(self, tarabox_link: str) -> Optional[Dict[str, Any]]:
        """Get video record by TaraBox link."""
        async with self._db.execute(
            "SELECT * FROM videos WHERE tarabox_link = ?", (tarabox_link,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_video_by_id(self, video_id: int) -> Optional[Dict[str, Any]]:
        """Get video record by ID."""
        async with self._db.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_video_status(
        self, video_id: int, status: str, 
        archive_message_id: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update video status and optional fields."""
        await self._db.execute(
            """UPDATE videos 
               SET status = ?, archive_message_id = ?, error_message = ?
               WHERE id = ?""",
            (status, archive_message_id, error_message, video_id)
        )
        await self._db.commit()
        logger.info("video_status_updated", video_id=video_id, status=status)

    async def increment_request_count(self, video_id: int) -> None:
        """Increment request count and update last_requested timestamp."""
        await self._db.execute(
            """UPDATE videos 
               SET request_count = request_count + 1, last_requested = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (video_id,)
        )
        await self._db.commit()

    async def get_pending_videos(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get videos pending conversion."""
        async with self._db.execute(
            "SELECT * FROM videos WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_ready_videos(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get videos ready for delivery."""
        async with self._db.execute(
            "SELECT * FROM videos WHERE status = 'ready' ORDER BY id ASC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Scraper State
    # -------------------------------------------------------------------------

    async def get_last_message_id(self) -> int:
        """Get the last processed message ID from scraper state."""
        async with self._db.execute(
            "SELECT last_message_id FROM scraper_state WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row["last_message_id"] if row else 0

    async def set_last_message_id(self, message_id: int) -> None:
        """Update the last processed message ID."""
        await self._db.execute(
            "UPDATE scraper_state SET last_message_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (message_id,)
        )
        await self._db.commit()

    # -------------------------------------------------------------------------
    # Rate Limiting
    # -------------------------------------------------------------------------

    async def check_rate_limit(self, user_id: int, max_per_hour: int) -> bool:
        """
        Check if user is within rate limit.
        Returns True if allowed, False if exceeded.
        """
        now = datetime.utcnow()
        window_start = now - timedelta(hours=1)

        async with self._db.execute(
            "SELECT request_count, window_start FROM rate_limits WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            # First request
            await self._db.execute(
                "INSERT INTO rate_limits (user_id, request_count, window_start) VALUES (?, 1, ?)",
                (user_id, now.isoformat())
            )
            await self._db.commit()
            return True

        current_window = datetime.fromisoformat(row["window_start"])

        if current_window < window_start:
            # Window expired, reset
            await self._db.execute(
                "UPDATE rate_limits SET request_count = 1, window_start = ? WHERE user_id = ?",
                (now.isoformat(), user_id)
            )
            await self._db.commit()
            return True

        if row["request_count"] >= max_per_hour:
            return False

        # Increment count
        await self._db.execute(
            "UPDATE rate_limits SET request_count = request_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        await self._db.commit()
        return True

    # -------------------------------------------------------------------------
    # Delivery Bots
    # -------------------------------------------------------------------------

    async def register_delivery_bot(self, username: str, token: str) -> int:
        """Register a new delivery bot. Returns the bot ID."""
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO delivery_bots (bot_username, bot_token) VALUES (?, ?)",
            (username, token)
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_active_delivery_bots(self) -> List[Dict[str, Any]]:
        """Get all active delivery bots."""
        async with self._db.execute(
            "SELECT * FROM delivery_bots WHERE is_active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_bot_stats(self, bot_id: int) -> None:
        """Increment delivery count and update last_used timestamp."""
        await self._db.execute(
            """UPDATE delivery_bots 
               SET total_deliveries = total_deliveries + 1, last_used_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (bot_id,)
        )
        await self._db.commit()

    # -------------------------------------------------------------------------
    # Delivery Logs
    # -------------------------------------------------------------------------

    async def log_delivery(
        self, video_id: int, user_id: int, 
        bot_id: Optional[int], success: bool,
        error_message: Optional[str] = None
    ) -> None:
        """Log a delivery attempt for audit purposes."""
        await self._db.execute(
            """INSERT INTO delivery_logs 
               (video_id, user_id, bot_id, success, error_message)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, user_id, bot_id, success, error_message)
        )
        await self._db.commit()


# Global database instance
db = Database()
