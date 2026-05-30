-- =============================================================================
-- Telegram Video Delivery System - Database Schema
-- SQLite with aiosqlite
-- =============================================================================

-- =============================================================================
-- videos: Stores all video metadata and conversion status
-- =============================================================================
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tarabox_link TEXT NOT NULL UNIQUE,
    archive_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    request_count INTEGER NOT NULL DEFAULT 0,
    last_requested DATETIME,
    error_message TEXT,

    -- Index for fast lookups by link
    UNIQUE(tarabox_link)
);

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_archive_msg ON videos(archive_message_id);

-- =============================================================================
-- scraper_state: Tracks the last processed message ID for crash recovery
-- =============================================================================
CREATE TABLE IF NOT EXISTS scraper_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Initialize scraper state if empty
INSERT OR IGNORE INTO scraper_state (id, last_message_id) VALUES (1, 0);

-- =============================================================================
-- delivery_bots: Prepares for future multi-bot scaling
-- =============================================================================
CREATE TABLE IF NOT EXISTS delivery_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_username TEXT NOT NULL UNIQUE,
    bot_token TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    total_deliveries INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_delivery_bots_active ON delivery_bots(is_active);

-- =============================================================================
-- delivery_logs: Audit trail of all video deliveries
-- =============================================================================
CREATE TABLE IF NOT EXISTS delivery_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    user_id INTEGER NOT NULL,
    bot_id INTEGER REFERENCES delivery_bots(id),
    delivered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success BOOLEAN NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_delivery_logs_user ON delivery_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_delivery_logs_video ON delivery_logs(video_id);

-- =============================================================================
-- rate_limits: Per-user request tracking for rate limiting
-- =============================================================================
CREATE TABLE IF NOT EXISTS rate_limits (
    user_id INTEGER PRIMARY KEY,
    request_count INTEGER NOT NULL DEFAULT 0,
    window_start DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Triggers: Auto-update updated_at on videos table
-- =============================================================================
CREATE TRIGGER IF NOT EXISTS trg_videos_updated_at
AFTER UPDATE ON videos
FOR EACH ROW
BEGIN
    UPDATE videos SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
