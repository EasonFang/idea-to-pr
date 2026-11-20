"""Configuration loaded from environment variables.

Nothing here is required for the tests to run: every value has a safe default
so the app can be imported in CI without a real bot token or network access.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Attributes:
        webhook_secret: Shared secret expected in the Telegram secret-token
            header. When set, a missing/invalid signature fails closed (401).
        bot_token: Telegram bot token, used for best-effort replies and voice
            download.
        github_token: Token used by the GitHub Issues client.
        github_repo: "owner/name" of the target repository.
        devin_api_key: Devin Sessions API key.
        devin_api_base: Devin API base URL.
        whisper_model: faster-whisper model size (tiny/small/medium/large-v3).
        max_retries: Hard cap on Devin session retries (cost guard).
        db_path: SQLite database path; use ":memory:" for tests.
        ack_text: Text sent back to the user on an accepted message.
    """

    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repo: str = os.getenv("GITHUB_REPO", "")
    devin_api_key: str = os.getenv("DEVIN_API_KEY", "")
    devin_api_base: str = os.getenv("DEVIN_API_BASE", "https://api.devin.ai/v1")
    whisper_model: str = os.getenv("WHISPER_MODEL", "small")
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    db_path: str = os.getenv("DB_PATH", "idea-to-pr.db")
    ack_text: str = "收到，正在整理规格…"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()


def load_settings() -> Settings:
    return Settings.from_env()
