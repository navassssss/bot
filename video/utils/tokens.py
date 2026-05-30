"""
Secure token generation and validation.

Tokens are signed, expiring URLs that prevent:
- ID enumeration attacks
- Replay attacks (via expiry)
- Tampering (via HMAC signature)

Token format (base64url encoded):
    video_id|expiry_timestamp|signature

The signature is HMAC-SHA256 over "video_id|expiry_timestamp".
"""
import base64
import hashlib
import hmac
import struct
import time
from typing import Optional

from utils.config import Config
from utils.logger import get_logger

logger = get_logger("tokens")


class TokenError(Exception):
    """Base exception for token operations."""
    pass


class TokenExpiredError(TokenError):
    """Raised when a token has expired."""
    pass


class TokenInvalidError(TokenError):
    """Raised when a token signature is invalid or format is corrupted."""
    pass


class SecureTokenManager:
    """
    Manages signed, expiring tokens for deep-link video access.

    Architecture note:
    - Tokens are short-lived (default 10 minutes)
    - They encode the video_id so we never expose raw database IDs
    - HMAC-SHA256 prevents tampering
    - Base64url encoding makes them URL-safe for Telegram deep links
    """

    def __init__(self, secret: str, expiry_minutes: int = 10):
        self._secret = secret.encode("utf-8")
        self._expiry_seconds = expiry_minutes * 60

    def generate(self, video_id: int) -> str:
        """
        Generate a signed, expiring token for a video.

        Args:
            video_id: The internal database video ID.

        Returns:
            URL-safe base64 string representing the token.
        """
        expiry = int(time.time()) + self._expiry_seconds

        # Build payload: video_id|expiry
        payload = f"{video_id}|{expiry}"

        # Sign with HMAC-SHA256
        signature = hmac.new(
            self._secret,
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()[:16]  # 16 hex chars = 64 bits of security (sufficient)

        # Combine and encode
        token_raw = f"{payload}|{signature}"
        token_b64 = base64.urlsafe_b64encode(token_raw.encode("utf-8")).decode("utf-8").rstrip("=")

        logger.debug("token_generated", video_id=video_id, expiry=expiry)
        return token_b64

    def validate(self, token: str) -> int:
        """
        Validate a token and return the video_id.

        Args:
            token: The URL-safe base64 token string.

        Returns:
            The video_id if valid.

        Raises:
            TokenExpiredError: If the token has expired.
            TokenInvalidError: If the signature is invalid or format is wrong.
        """
        try:
            # Decode base64url
            padding = 4 - (len(token) % 4)
            if padding != 4:
                token += "=" * padding
            token_raw = base64.urlsafe_b64decode(token).decode("utf-8")

            # Parse components
            parts = token_raw.split("|")
            if len(parts) != 3:
                raise TokenInvalidError("Invalid token format")

            video_id_str, expiry_str, provided_signature = parts
            video_id = int(video_id_str)
            expiry = int(expiry_str)

            # Check expiry
            if time.time() > expiry:
                raise TokenExpiredError("Token has expired")

            # Verify signature
            payload = f"{video_id}|{expiry}"
            expected_signature = hmac.new(
                self._secret,
                payload.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()[:16]

            if not hmac.compare_digest(provided_signature, expected_signature):
                raise TokenInvalidError("Invalid token signature")

            logger.debug("token_validated", video_id=video_id)
            return video_id

        except (ValueError, base64.binascii.Error) as e:
            raise TokenInvalidError(f"Token decode error: {e}")


# Singleton instance
token_manager = SecureTokenManager(
    secret=Config.TOKEN_SECRET,
    expiry_minutes=Config.TOKEN_EXPIRY_MINUTES
)
