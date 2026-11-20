"""Message extraction from Telegram updates.

Anything we cannot act on (stickers, channel posts, edits, empty messages,
service messages) must be acknowledged with ``ignored=True`` and NO reply.
Replying to every sticker is the fastest way to get the bot blocked.
"""

from __future__ import annotations

from typing import Any

from models import IncomingMessage, MessageKind


def extract_message(update: dict[str, Any]) -> IncomingMessage | None:
    """Return a normalised message, or None if the update is unhandleable."""
    if not isinstance(update, dict):
        return None

    # Inline-keyboard button press. Telegram sends these as callback_query,
    # NOT as message -- a bot that only reads .message silently drops them.
    cb = update.get("callback_query")
    if isinstance(cb, dict):
        cb_msg = cb.get("message") or {}
        chat_id = (cb_msg.get("chat") or {}).get("id")
        if chat_id is None:
            chat_id = (cb.get("from") or {}).get("id")
        if chat_id is None:
            return None
        return IncomingMessage(
            kind=MessageKind.CALLBACK,
            chat_id=chat_id,
            text=str(cb.get("data") or "").strip(),
            callback_id=str(cb.get("id") or ""),
            callback_data=str(cb.get("data") or "").strip(),
            raw=update,
        )

    # Only plain private/group messages are handled.
    if "edited_message" in update or "channel_post" in update:
        return None
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None

    # Service messages (joins, photo albums without caption, polls, etc.)
    if any(
        k in msg
        for k in (
            "new_chat_members",
            "left_chat_member",
            "new_chat_title",
            "new_chat_photo",
            "delete_chat_photo",
            "group_chat_created",
            "poll",
            "sticker",
            "animation",
            "video",
            "photo",
            "contact",
            "location",
            "venue",
            "invoice",
        )
    ):
        return None

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    text = msg.get("text") or msg.get("caption") or ""
    voice = msg.get("voice")

    if isinstance(voice, dict) and voice.get("file_id"):
        caption = str(msg.get("caption") or "").strip()
        return IncomingMessage(
            kind=MessageKind.VOICE,
            chat_id=chat_id,
            text=caption,
            voice_file_id=voice["file_id"],
            raw=update,
        )

    text = str(text).strip()
    if not text:
        return None

    if text.startswith("/"):
        return IncomingMessage(
            kind=MessageKind.COMMAND, chat_id=chat_id, text=text, raw=update
        )

    return IncomingMessage(
        kind=MessageKind.TEXT, chat_id=chat_id, text=text, raw=update
    )
