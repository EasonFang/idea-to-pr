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

    def send_with_actions(
        self,
        chat_id: int,
        text: str,
        actions: list[tuple[str, str]],
    ) -> bool:
        """Send a message with inline buttons: [(label, callback_data), ...].

        callback_data is the whole control channel for the completion flow:
        approve:<sid>, reprompt:<sid>, close:<sid>. A button press arrives back
        at /webhook as a callback_query and is answered by the worker.
        """
        try:
            if not self.bot_token or self.transport is None:
                logger.debug("notifier disabled (no token/transport)")
                return False
            keyboard = [
                [{"text": label, "callback_data": data} for label, data in actions]
            ]
            url = f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage"
            self.transport(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "reply_markup": {"inline_keyboard": keyboard},
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001 - deliberately swallow
            logger.warning("sendMessage(actions) failed (ignored): %s", exc)
            return False

    def answer_callback(self, callback_id: str, text: str = "OK") -> bool:
        """Stop Telegram's loading spinner. Best-effort, never raises."""
        try:
            if not callback_id or not self.bot_token or self.transport is None:
                return False
            url = f"{TELEGRAM_API}/bot{self.bot_token}/answerCallbackQuery"
            self.transport(
                url, json={"callback_query_id": callback_id, "text": text}
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("answerCallbackQuery failed (ignored): %s", exc)
            return False
