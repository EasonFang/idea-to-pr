"""Parse a free-form idea into a scoped MVP spec + issue list.

Uses a cheap/fast model via an injectable ``parser`` callable so the expensive
budget is reserved for the actual coding session. Falls back to a deterministic
heuristic when no model is available (tests / offline).
"""

from __future__ import annotations

import logging

from models import Spec

logger = logging.getLogger(__name__)


def heuristic_spec(text: str) -> Spec:
    """Deterministic, offline spec: first line = title, rest = bullets."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0][:80] if lines else text.strip()[:80] or "Untitled idea"
    bullets = [l.lstrip("-*0123456789. ").strip() for l in lines[1:]]
    bullets = [b for b in bullets if b][:10]
    summary = " ".join(lines[1:3])[:280] if len(lines) > 1 else title
    return Spec(title=title, summary=summary, issues=bullets)


def parse_idea(text: str, parser=None) -> Spec:
    """Produce a Spec. ``parser`` is an optional callable(text)->Spec."""
    if parser is not None:
        try:
            spec = parser(text)
            if isinstance(spec, Spec):
                return spec
        except Exception as exc:  # noqa: BLE001
            logger.warning("spec parser failed, using heuristic: %s", exc)
    return heuristic_spec(text)
