"""Devin Sessions API client + the cost guards around it.

The highest risk in the whole system is a Devin session spinning forever on a
broken dependency. Four defences live here and in the worker:

1. HARD LIMIT  - at most ``max_retries`` attempts, then stop.
2. PRE-LOCKED  - the scaffold prompt pins lockfiles (see worker.py).
3. LOOP DETECT - two consecutive failures with the same file + error signature
                 are treated as stuck and terminate early.
4. ESCAPE HATCH- /stop cancels the active session (see worker.py).

A stuck session must cost N attempts + one notification, never silent $20.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEVIN_API = "https://api.devin.ai/v1"


@dataclass
class SessionResult:
    session_id: str
    status: str
    url: str = ""


@dataclass
class GuardDecision:
    should_stop: bool
    reason: str = ""


class RetryGuard:
    """Tracks attempts and detects retry loops."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.attempts = 0
        self.history: list[tuple[str, str]] = []  # (file, error_signature)

    def record_failure(self, file: str, error_signature: str) -> GuardDecision:
        self.attempts += 1
        self.history.append((file, error_signature))

        if len(self.history) >= 2:
            last, prev = self.history[-1], self.history[-2]
            if last == prev:
                return GuardDecision(True, f"loop detected: {last[0]} / {last[1]}")

        if self.attempts >= self.max_retries:
            return GuardDecision(True, f"retry limit reached ({self.attempts})")

        return GuardDecision(False)


class DevinClient:
    def __init__(self, api_key: str, base_url: str = DEVIN_API, transport=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.transport = transport  # injectable callable(method, url, **kw)

    def _request(self, method: str, path: str, **kwargs):
        if self.transport is None:
            raise RuntimeError("no transport configured for Devin")
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        return self.transport(method, f"{self.base_url}{path}", headers=headers, **kwargs)

    def create_session(self, prompt: str) -> SessionResult:
        resp = self._request("POST", "/sessions", json={"prompt": prompt})
        data = json.loads(resp.read())
        return SessionResult(
            session_id=data.get("session_id", ""),
            status=data.get("status", "running"),
            url=data.get("url", ""),
        )

    def get_session(self, session_id: str) -> SessionResult:
        resp = self._request("GET", f"/sessions/{session_id}")
        data = json.loads(resp.read())
        return SessionResult(
            session_id=session_id,
            status=data.get("status", "unknown"),
            url=data.get("url", ""),
        )

    def send_message(self, session_id: str, message: str) -> bool:
        """Send a follow-up instruction into a running session (re-prompt)."""
        self._request(
            "POST",
            f"/sessions/{session_id}/message",
            json={"message": message},
        )
        return True

    def cancel_session(self, session_id: str) -> bool:
        try:
            self._request("POST", f"/sessions/{session_id}/cancel")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel failed: %s", exc)
            return False
