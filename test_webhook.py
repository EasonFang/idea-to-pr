"""Webhook tests.

All tests run offline: no network, no real bot token. Signature is simulated
via headers, sendMessage is injected.

PRD section 7 requires three assertion classes:
1. signature: no header -> 401, wrong header -> 401, correct -> 200
2. unhandleable update -> 200 + ignored=True + NO reply sent
3. sendMessage failure -> still 200 (no retry storm)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app
from jobqueue import RecordingQueue

SECRET = "test-secret"
SIG_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class GoodNotifier:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


class BadNotifier:
    def __init__(self):
        self.calls = 0

    def send_message(self, chat_id, text):
        self.calls += 1
        raise RuntimeError("sendMessage exploded")


def text_update(text="hello", chat_id=42):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def make_client(secret=SECRET, notifier=None, queue=None):
    return TestClient(
        create_app(
            secret=secret,
            queue=queue if queue is not None else RecordingQueue(),
            notifier=notifier,
        )
    )


# --- 1. signature validation -------------------------------------------------

def test_no_signature_header_returns_401():
    client = make_client()
    r = client.post("/webhook", json=text_update())
    assert r.status_code == 401


def test_wrong_signature_returns_401():
    client = make_client()
    r = client.post("/webhook", json=text_update(), headers={SIG_HEADER: "wrong"})
    assert r.status_code == 401


def test_correct_signature_returns_200():
    client = make_client()
    r = client.post("/webhook", json=text_update(), headers={SIG_HEADER: SECRET})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_no_secret_configured_allows_request():
    client = make_client(secret="")
    r = client.post("/webhook", json=text_update())
    assert r.status_code == 200


# --- 2. unhandleable updates -------------------------------------------------

@pytest.mark.parametrize(
    "update",
    [
        {},
        {"edited_message": {"chat": {"id": 1}, "text": "x"}},
        {"channel_post": {"chat": {"id": 1}, "text": "x"}},
        {"message": {"chat": {"id": 1}, "sticker": {"emoji": "x"}}},
        {"message": {"chat": {"id": 1}, "text": ""}},
        {"message": {"chat": {"id": 1}, "photo": [{"file_id": "x"}]}},
        {"message": {"chat": {"id": 1}, "new_chat_members": [{}]}},
    ],
)
def test_unhandleable_updates_ignored_with_no_reply(update):
    notifier = GoodNotifier()
    queue = RecordingQueue()
    client = make_client(notifier=notifier, queue=queue)
    r = client.post("/webhook", json=update, headers={SIG_HEADER: SECRET})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ignored": True}
    assert notifier.sent == []  # critical: no reply to stickers etc.
    assert queue.jobs == []


# --- 3. notification failure is not fatal -----------------------------------

def test_sendmessage_failure_still_returns_200():
    bad = BadNotifier()
    queue = RecordingQueue()
    client = make_client(notifier=bad, queue=queue)
    r = client.post("/webhook", json=text_update(), headers={SIG_HEADER: SECRET})
    assert r.status_code == 200
    assert bad.calls == 1
    assert len(queue.jobs) == 1  # job still enqueued despite notify failure


def test_queue_failure_still_returns_200():
    class BadQueue:
        def enqueue(self, job):
            raise RuntimeError("queue down")

    client = make_client(notifier=GoodNotifier(), queue=BadQueue())
    r = client.post("/webhook", json=text_update(), headers={SIG_HEADER: SECRET})
    assert r.status_code == 200


# --- 4. valid messages -------------------------------------------------------

def test_text_message_enqueued_with_text_source():
    queue = RecordingQueue()
    client = make_client(notifier=GoodNotifier(), queue=queue)
    r = client.post(
        "/webhook", json=text_update("build me a todo app"), headers={SIG_HEADER: SECRET}
    )
    assert r.status_code == 200
    assert len(queue.jobs) == 1
    job = queue.jobs[0]
    assert job.text == "build me a todo app"
    assert job.source == "text"
    assert job.chat_id == 42


def test_voice_message_uses_file_id_and_voice_source():
    queue = RecordingQueue()
    update = {
        "update_id": 2,
        "message": {
            "chat": {"id": 7, "type": "private"},
            "voice": {"file_id": "FILE123", "duration": 3},
        },
    }
    client = make_client(notifier=GoodNotifier(), queue=queue)
    r = client.post("/webhook", json=update, headers={SIG_HEADER: SECRET})
    assert r.status_code == 200
    assert queue.jobs[0].text == "FILE123"
    assert queue.jobs[0].source == "voice"


def test_command_does_not_send_ack():
    notifier = GoodNotifier()
    client = make_client(notifier=notifier)
    r = client.post("/webhook", json=text_update("/stop"), headers={SIG_HEADER: SECRET})
    assert r.status_code == 200
    assert notifier.sent == []  # commands handled by worker, no ack here


def test_bad_json_is_ignored():
    client = make_client()
    r = client.post(
        "/webhook",
        content=b"not-json",
        headers={SIG_HEADER: SECRET, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["ignored"] is True


def test_health_check():
    client = make_client()
    assert client.get("/health").json() == {"status": "ok"}
