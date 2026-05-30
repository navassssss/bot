"""
Configuration module.
Loads all settings from .env file with validation.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Config:
    """Centralized configuration with validation."""

    # --- Telegram API (Telethon) ---
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    PHONE_NUMBER: str = os.getenv("PHONE_NUMBER", "")

    # --- Bot Tokens ---
    MAIN_BOT_TOKEN: str = os.getenv("MAIN_BOT_TOKEN", "")
    DELIVERY_BOT_TOKEN: str = os.getenv("DELIVERY_BOT_TOKEN", "")

    # --- Channels ---
    SOURCE_CHANNEL: int = int(os.getenv("SOURCE_CHANNEL", "0"))
    ARCHIVE_CHANNEL: int = int(os.getenv("ARCHIVE_CHANNEL", "0"))

    # --- Converter ---
    CONVERTER_BOT: str = os.getenv("CONVERTER_BOT", "")

    # --- Security ---
    TOKEN_SECRET: str = os.getenv("TOKEN_SECRET", "")
    TOKEN_EXPIRY_MINUTES: int = int(os.getenv("TOKEN_EXPIRY_MINUTES", "10"))

    # --- Rate Limiting ---
    RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "10"))

    # --- Scraping ---
    SCRAPE_INTERVAL: int = int(os.getenv("SCRAPE_INTERVAL", "60"))

    # --- Paths ---
    SESSION_PATH: Path = PROJECT_ROOT / "sessions" / "telegram.session"
    DB_PATH: Path = PROJECT_ROOT / "database" / "app.db"

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration values."""
        required = [
            ("API_ID", cls.API_ID),
            ("API_HASH", cls.API_HASH),
            ("PHONE_NUMBER", cls.PHONE_NUMBER),
            ("MAIN_BOT_TOKEN", cls.MAIN_BOT_TOKEN),
            ("DELIVERY_BOT_TOKEN", cls.DELIVERY_BOT_TOKEN),
            ("SOURCE_CHANNEL", cls.SOURCE_CHANNEL),
            ("ARCHIVE_CHANNEL", cls.ARCHIVE_CHANNEL),
            ("CONVERTER_BOT", cls.CONVERTER_BOT),
            ("TOKEN_SECRET", cls.TOKEN_SECRET),
        ]

        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        if len(cls.TOKEN_SECRET) < 32:
            raise ValueError("TOKEN_SECRET must be at least 32 characters for security.")

        # Ensure directories exist
        cls.SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
