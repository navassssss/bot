"""
Membership verification service.

Responsibilities:
- Check if a user is a member of required channels/groups
- Verify user hasn't been banned
- Cache membership checks to reduce API calls
- Support multiple membership requirements

Architecture:
- Uses aiogram Bot to check chat member status
- Caches results for 5 minutes to avoid repeated API calls
- Returns bool: True = member, False = not member
- Can be extended to support paid membership, subscription checks, etc.
"""
import asyncio
import time
from typing import Dict, Optional, Set

from aiogram import Bot
from aiogram.types import ChatMemberStatus

from utils.config import Config
from utils.logger import get_logger

logger = get_logger("membership")

# Cache membership checks for 5 minutes
MEMBERSHIP_CACHE_TTL = 300  # seconds


class MembershipService:
    """
    Verifies user membership in required channels.

    Current implementation checks if user is in the source channel.
    Can be extended to check multiple channels, subscription status, etc.

    Cache strategy:
    - In-memory dict with TTL
    - Reduces API calls and improves response time
    - Cache is per-process (sufficient for single-instance deployment)
    """

    def __init__(self):
        self._cache: Dict[int, Dict] = {}  # user_id -> {status, timestamp}
        self._required_channels: Set[int] = set()

    def add_required_channel(self, channel_id: int) -> None:
        """Add a channel that users must be members of."""
        self._required_channels.add(channel_id)
        logger.info("membership_channel_added", channel_id=channel_id)

    async def check_membership(self, bot: Bot, user_id: int) -> bool:
        """
        Check if user is a member of all required channels.

        Returns True if all checks pass, False otherwise.
        """
        # Check cache first
        cached = self._get_cached(user_id)
        if cached is not None:
            return cached

        # No required channels = always allowed
        if not self._required_channels:
            return True

        # Check each required channel
        for channel_id in self._required_channels:
            try:
                member = await bot.get_chat_member(channel_id, user_id)

                # Valid statuses: member, administrator, creator
                valid_statuses = {
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                }

                if member.status not in valid_statuses:
                    self._set_cached(user_id, False)
                    logger.info("membership_check_failed", 
                              user_id=user_id, channel_id=channel_id, 
                              status=member.status)
                    return False

            except Exception as e:
                logger.error("membership_check_error", 
                           user_id=user_id, channel_id=channel_id, error=str(e))
                # On error, be permissive (fail open) to avoid blocking legitimate users
                # Change to False for fail-closed behavior
                return True

        # All checks passed
        self._set_cached(user_id, True)
        logger.info("membership_check_passed", user_id=user_id)
        return True

    def _get_cached(self, user_id: int) -> Optional[bool]:
        """Get cached membership status if not expired."""
        if user_id not in self._cache:
            return None

        entry = self._cache[user_id]
        if time.time() - entry["timestamp"] > MEMBERSHIP_CACHE_TTL:
            del self._cache[user_id]
            return None

        return entry["status"]

    def _set_cached(self, user_id: int, status: bool) -> None:
        """Cache membership check result."""
        self._cache[user_id] = {
            "status": status,
            "timestamp": time.time()
        }

    def invalidate_cache(self, user_id: int) -> None:
        """Invalidate cache for a specific user."""
        self._cache.pop(user_id, None)


# Singleton instance
membership_service = MembershipService()
