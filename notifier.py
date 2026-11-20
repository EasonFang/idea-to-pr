"""Notification layer. Sending a Telegram message is always best-effort.

A failure here must never bubble up to the webhook, otherwise Telegram sees a
5xx and retries the whole update, producing duplicate jobs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class Notifier:
    def __init__(self, bot_token: str = "", transport=None):
        self.bot_token = bot_token
        self.transport = transport  # injectable in tests; callable(url, json=...)

    def send_message(self, chat_id: int, text: str) -> bool:
        """Best-effort send. Returns True on success, False on any failure.

        Never raises.
        """
        try:
            if not self.bot_token or self.transport is None:
                logger.debug("notifier disabled (no token/transport)")
                return False
            url = f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage"
            self.transport(url, json={"chat_id": chat_id, "text": text})
            return True
        except Exception as exc:  # noqa: BLE001 - deliberately swallow
            logger.warning("sendMessage failed (ignored): %s", exc)
            return False

    def progress(self, chat_id: int, message: str) -> bool:
        """Relay a progress event back into the chat."""
        return self.send_message(chat_id, f"• {message}")
