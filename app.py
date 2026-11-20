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

from completion import relay_event
from devin_events import normalise_event
from extract import extract_message
from models import Job, MessageKind
from sessions import SessionRegistry
from signing import header_from_mapping, verify_signature

logger = logging.getLogger(__name__)


def create_app(
    *,
    secret: str = "",
    queue=None,
    notifier=None,
    ack_text: str = "收到，正在整理规格…",
    devin_secret: str = "",
    sessions: SessionRegistry | None = None,
) -> FastAPI:
    app = FastAPI(title="idea-to-pr ingress")
    app.state.secret = secret
    app.state.queue = queue
    app.state.notifier = notifier
    app.state.ack_text = ack_text
    app.state.devin_secret = devin_secret
    app.state.sessions = sessions or SessionRegistry()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/devin-webhook")
    async def devin_webhook(request: Request):
        """Devin session events -> Telegram inline buttons.

        Fast 200 always: a slow/failing Telegram send must never make Devin
        retry the event and duplicate the notification.
        """
        # Separate secret from the Telegram one; fail closed when configured.
        header_value = header_from_mapping(request.headers)
        if not verify_signature(header_value, app.state.devin_secret):
            return _json({"detail": "invalid signature"}, status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return _json({"ok": True, "ignored": True}, status_code=200)
        try:
            event = normalise_event(payload)
            sent = relay_event(event, app.state.sessions, app.state.notifier)
        except Exception as exc:  # noqa: BLE001 - never 5xx on an event
            logger.exception("devin event relay failed (ignored): %s", exc)
            sent = False
        return _json({"ok": True, "relayed": bool(sent)}, status_code=200)

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
        is_callback = msg.kind == MessageKind.CALLBACK
        job = Job(
            chat_id=msg.chat_id,
            text=msg.callback_data if is_callback else (
                msg.voice_file_id if msg.kind == MessageKind.VOICE else msg.text
            ),
            source="callback" if is_callback else (
                "voice" if msg.kind == MessageKind.VOICE else "text"
            ),
            update_id=update.get("update_id", 0),
        )
        if app.state.queue is not None:
            try:
                app.state.queue.enqueue(job)
            except Exception as exc:  # noqa: BLE001 - queue must not 5xx
                logger.exception("enqueue failed (ignored): %s", exc)

        # 4. Answer the inline button immediately so Telegram stops spinning.
        #    The actual action runs in the worker via the queued callback job.
        if is_callback and app.state.notifier is not None:
            try:
                app.state.notifier.answer_callback(msg.callback_id, "处理中…")
            except Exception as exc:  # noqa: BLE001
                logger.warning("answer callback failed (ignored): %s", exc)

        # 5. Best-effort acknowledgement. Never let this fail the request,
        #    otherwise Telegram retries the update (retry storm). No ack for
        #    commands or button presses (the action result is the feedback).
        if (
            app.state.notifier is not None
            and msg.kind not in (MessageKind.COMMAND, MessageKind.CALLBACK)
        ):
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
