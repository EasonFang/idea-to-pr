"""Normalise Devin session webhook payloads into one small event type.

Devin has shipped several payload shapes across v1/v2/v3. Rather than hard-code
one vendor draft, we accept the common variants and collapse them into a
``DevinEvent``. Anything unrecognised becomes UNKNOWN and is ignored with 200
(no retry storm at the webhook boundary).

The fields we actually need are few:
- which session it is (to find the originating Telegram chat),
- what happened (PR opened / finished / failed / needs attention),
- where the PR and session live (links shown in Telegram).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DevinEventKind(str, Enum):
    PR_OPENED = "pr_opened"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    MESSAGE = "message"
    UNKNOWN = "unknown"


# Terminal events are still worth relaying once, but no further actions apply.
TERMINAL = (DevinEventKind.COMPLETED, DevinEventKind.FAILED)


@dataclass
class DevinEvent:
    kind: DevinEventKind
    session_id: str = ""
    pr_url: str = ""
    session_url: str = ""
    summary: str = ""
    event_id: str = ""
    raw: dict[str, Any] | None = None


# Match event names loosely: "session.completed", "sessionCompleted",
# "pr_opened", "session.blocked", "action_required", etc.
_KIND_RULES: tuple[tuple[tuple[str, ...], DevinEventKind], ...] = (
    (("pr_opened", "pr.created", "pull_request", "draft_pr"), DevinEventKind.PR_OPENED),
    (("completed", "finished", "session.done", "exit"), DevinEventKind.COMPLETED),
    (("blocked", "needs_attention", "action_required", "requires_input", "suspended"), DevinEventKind.BLOCKED),
    (("failed", "error", "cancelled", "canceled"), DevinEventKind.FAILED),
    (("message", "comment", "update"), DevinEventKind.MESSAGE),
)


def _classify(name: str) -> DevinEventKind:
    low = name.lower().replace("-", "_")
    for needles, kind in _KIND_RULES:
        if any(n in low for n in needles):
            return kind
    return DevinEventKind.UNKNOWN


def _first(data: dict[str, Any], *paths: tuple[str, ...]) -> str:
    """Return the first non-empty value found at any dotted path."""
    for path in paths:
        node: Any = data
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, (str, int)) and str(node).strip():
            return str(node).strip()
    return ""


def normalise_event(payload: dict[str, Any]) -> DevinEvent:
    """Convert a Devin webhook payload to a DevinEvent (never raises)."""
    if not isinstance(payload, dict):
        return DevinEvent(kind=DevinEventKind.UNKNOWN)

    raw_kind = _first(
        payload,
        ("event",),
        ("event_type",),
        ("type",),
        ("data", "event"),
        ("data", "type"),
        ("action",),
    )
    kind = _classify(raw_kind)

    session_id = _first(
        payload,
        ("session_id",),
        ("data", "session_id"),
        ("data", "session", "id"),
        ("session", "id"),
        ("id",),
    )
    session_url = _first(
        payload,
        ("session_url",),
        ("url",),
        ("data", "session", "url"),
        ("data", "url"),
    )
    pr_url = _first(
        payload,
        ("pr_url",),
        ("pull_request_url",),
        ("data", "pr_url"),
        ("data", "pr", "url"),
        ("data", "pull_request", "html_url"),
        ("data", "pull_request", "url"),
    )
    summary = _first(
        payload,
        ("summary",),
        ("message",),
        ("data", "message"),
        ("data", "text"),
        ("data", "summary"),
    )
    event_id = _first(payload, ("event_id",), ("id",), ("data", "event_id"))

    # A payload with a PR link is a PR event even if the vendor called it
    # "session.completed" -- that is the exact case the reviewer asked about.
    if kind not in (DevinEventKind.PR_OPENED, DevinEventKind.UNKNOWN) and pr_url:
        kind = DevinEventKind.PR_OPENED

    return DevinEvent(
        kind=kind,
        session_id=session_id,
        pr_url=pr_url,
        session_url=session_url,
        summary=summary[:280],
        event_id=event_id,
        raw=payload,
    )
