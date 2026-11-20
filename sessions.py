"""Session registry: links a Devin session to the Telegram chat that owns it.

The Devin webhook arrives asynchronously and carries NO chat id. This registry
is the missing piece that lets an incoming session event find the right user.
It also records seen event ids so Devin at-least-once delivery cannot produce
duplicate Telegram messages.

In-memory here (single process, like the in-process queue). The interface is
deliberately tiny so a Redis/SQLite implementation can be dropped in later
without touching the webhook or worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class SessionRegistry:
    by_session: dict[str, int] = field(default_factory=dict)  # session_id -> chat_id
    by_chat: dict[int, str] = field(default_factory=dict)  # chat_id -> session_id
    seen_events: set[str] = field(default_factory=set)

    def register(self, session_id: str, chat_id: int) -> None:
        if not session_id:
            return
        old = self.by_chat.get(chat_id)
        if old and old in self.by_session:
            self.by_session.pop(old, None)
        self.by_session[session_id] = chat_id
        self.by_chat[chat_id] = session_id

    def chat_for(self, session_id: str) -> int | None:
        return self.by_session.get(session_id)

    def session_for(self, chat_id: int) -> str | None:
        return self.by_chat.get(chat_id)

    def unregister(self, session_id: str) -> None:
        chat_id = self.by_session.pop(session_id, None)
        if chat_id is not None and self.by_chat.get(chat_id) == session_id:
            self.by_chat.pop(chat_id, None)

    def is_new_event(self, event_id: str) -> bool:
        """Return True the first time an event id is seen (idempotent relay)."""
        if not event_id:
            return True  # no id to dedup on -> relay once, best effort
        if event_id in self.seen_events:
            return False
        self.seen_events.add(event_id)
        return True

    def active_sessions(self) -> Iterable[str]:
        return tuple(self.by_session)
