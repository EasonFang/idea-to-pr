"""FastAPI ingress: HMAC check, fast ack, queue decoupling.

Design rules enforced here:
- Signature check fails closed (401) when a secret is configured but missing.
- Unhandleable updates return 200 + ignored=True and send NO reply.
- The queue enqueue is the only "work"; notification is best-effort and can
  never turn a 200 into a 5xx (which would trigger Telegram retries).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request

from extract import extract_message
from models import Job, MessageKind
from signing import header_from_mapping, verify_signature

logger = logging.getLogger(__name__)


def create_app(
    *,
    secret: str = "",
    queue=None,
    notifier=None,
    ack_text: str = "收到，正在整理规格…",
) -> FastAPI:
    app = FastAPI(title="idea-to-pr ingress")
    app.state.secret = secret
    app.state.queue = queue
    app.state.notifier = notifier
    app.state.ack_text = ack_text

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request):
        # 1. Signature verification (fail closed).
        header_value = header_from_mapping(request.headers)
        if not verify_signature(header_value, app.state.secret):
            logger.warning("rejected unsigned webhook")
            return _json({"detail": "invalid signature"}, status_code=401)

        # 2. Parse update.
        try:
            update: dict[str, Any] = await request.json()
        except Exception:
            return _json({"ok": True, "ignored": True}, status_code=200)

        msg = extract_message(update)
        if msg is None or not msg.is_actionable:
            return _json({"ok": True, "ignored": True}, status_code=200)

        # 3. Enqueue the heavy work (the ONLY required action).
        job = Job(
            chat_id=msg.chat_id,
            text=msg.voice_file_id if msg.kind == MessageKind.VOICE else msg.text,
            source="voice" if msg.kind == MessageKind.VOICE else "text",
            update_id=update.get("update_id", 0),
        )
        if app.state.queue is not None:
            try:
                app.state.queue.enqueue(job)
            except Exception as exc:  # noqa: BLE001 - queue must not 5xx
                logger.exception("enqueue failed (ignored): %s", exc)

        # 4. Best-effort acknowledgement. Never let this fail the request,
        #    otherwise Telegram retries the update (retry storm).
        if app.state.notifier is not None and msg.kind != MessageKind.COMMAND:
            try:
                app.state.notifier.send_message(msg.chat_id, app.state.ack_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ack failed (ignored): %s", exc)

        return _json({"ok": True, "ignored": False, "queued": True}, status_code=200)

    return app


def _json(content: dict, status_code: int = 200):
    from fastapi.responses import JSONResponse

    return JSONResponse(content=content, status_code=status_code)


app = create_app()
