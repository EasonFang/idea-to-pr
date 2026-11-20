"""Data models shared by the ingress and the worker.

The worker only ever sees plain text. Whether that text came from a typed
message or local Whisper transcription is deliberately erased here: one queue,
one pipeline, no parallel code paths to keep in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageKind(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    COMMAND = "command"
    UNHANDLED = "unhandled"


@dataclass
class IncomingMessage:
    """A normalised message extracted from a Telegram update."""

    kind: MessageKind
    chat_id: int
    text: str = ""
    voice_file_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.kind in (MessageKind.TEXT, MessageKind.VOICE, MessageKind.COMMAND)


@dataclass
class Job:
    """The unit of work placed on the queue."""

    chat_id: int
    text: str
    source: str = "text"  # always "text" downstream; informational only
    update_id: int = 0


@dataclass
class Spec:
    """The scoped spec produced before anything is delegated."""

    title: str
    summary: str
    issues: list[str] = field(default_factory=list)
    accepted: bool = False
