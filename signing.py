"""Constant-time signature verification for the Telegram webhook.

Telegram sends the webhook secret in the ``X-Telegram-Bot-Api-Secret-Token``
header. We compare it with ``hmac.compare_digest`` to avoid timing side
channels. When no secret is configured we allow the request through (local
development); when a secret IS configured but the header is missing or wrong
we fail closed with a 401.
"""

from __future__ import annotations

SECRET_HEADER = "x-telegram-bot-api-secret-token"


def constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings in constant time."""
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_signature(
    header_value: str | None,
    secret: str,
) -> bool:
    """Return True when the request may proceed.

    - ``secret`` empty -> no secret configured -> allow.
    - header missing -> fail closed (False).
    - header present -> constant-time compare with secret.
    """
    if not secret:
        return True
    if header_value is None:
        return False
    return constant_time_equals(header_value, secret)


def header_from_mapping(headers) -> str | None:
    """Fetch the secret header case-insensitively from any mapping."""
    for key, value in headers.items():
        if key.lower() == SECRET_HEADER:
            return value
    return None
