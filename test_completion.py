"""Tests for the event-driven completion flow.

Covers the exact path the reviewer asked about:
1. Devin event webhook -> normalise -> route to originating chat -> inline buttons
2. signature fails closed on the Devin endpoint too
3. duplicate event ids are not relayed twice (at-least-once delivery)
4. callback_query from Telegram is extracted and enqueued as a callback job
5. Approve / Re-prompt / Close each produce the right side effects
6. a button cannot control a session belonging to another chat
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app
from completion import handle_callback, parse_callback, relay_event
from devin_events import DevinEvent, DevinEventKind, normalise_event
from extract import extract_message
from jobqueue import RecordingQueue
from models import MessageKind, Spec
from sessions import SessionRegistry

SECRET = "tg-secret"
DEVIN_SECRET = "devin-secret"
HEADER = "X-Telegram-Bot-Api-Secret-Token"


# --- helpers ----------------------------------------------------------------

class ActionNotifier:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []
        self.with_actions: list[tuple[int, str, list]] = []
        self.answers: list[tuple[str, str]] = []

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))
        return True

    def progress(self, chat_id, text):
        self.messages.append((chat_id, text))
        return True

    def send_with_actions(self, chat_id, text, actions):
        self.with_actions.append((chat_id, text, actions))
        return True

    def answer_callback(self, callback_id, text="OK"):
        self.answers.append((callback_id, text))
        return True


class DevinStub:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    def send_message(self, session_id, message):
        self.messages.append((session_id, message))
        return True

    def cancel_session(self, session_id):
        self.cancelled.append(session_id)
        return True


def pr_payload(session_id="sess-1", event="session.completed", event_id="evt-1"):
    return {
        "event": event,
        "session_id": session_id,
        "event_id": event_id,
        "url": f"https://app.devin.ai/sessions/{session_id}",
        "data": {
            "pull_request": {
                "html_url": f"https://github.com/o/r/pull/9",
            }
        },
    }


def callback_update(data="approve:sess-1", chat_id=42):
    return {
        "update_id": 99,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 7},
            "message": {"chat": {"id": chat_id}},
            "data": data,
        },
    }


# --- event normalisation -----------------------------------------------------

def test_completed_event_with_pr_becomes_pr_opened():
    event = normalise_event(pr_payload())
    assert event.kind == DevinEventKind.PR_OPENED
    assert event.session_id == "sess-1"
    assert event.pr_url.endswith("/pull/9")


def test_blocked_event_classified():
    event = normalise_event({"event": "action_required", "session_id": "s"})
    assert event.kind == DevinEventKind.BLOCKED


def test_unknown_payload_is_ignored():
    assert normalise_event({"foo": "bar"}).kind == DevinEventKind.UNKNOWN


# --- /devin-webhook ----------------------------------------------------------

def test_devin_webhook_requires_signature():
    client = create_app(secret=SECRET, devin_secret=DEVIN_SECRET)
    from fastapi.testclient import TestClient as TC

    r = TC(client).post("/devin-webhook", json=pr_payload())
    assert r.status_code == 401


def test_devin_webhook_relays_buttons_to_bound_chat():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)
    client = TestClient(
        create_app(
            secret=SECRET,
            devin_secret=DEVIN_SECRET,
            notifier=notifier,
            sessions=registry,
        )
    )
    r = client.post(
        "/devin-webhook",
        json=pr_payload(),
        headers={HEADER: DEVIN_SECRET},
    )
    assert r.status_code == 200
    assert r.json()["relayed"] is True
    assert len(notifier.with_actions) == 1
    chat_id, text, actions = notifier.with_actions[0]
    assert chat_id == 42
    assert "draft PR" in text
    assert [a[0] for a in actions] == ["✅ Approve", "🔁 Re-prompt", "⏹ Close"]
    assert actions[0][1] == "approve:sess-1"


def test_duplicate_event_not_relayed_twice():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)
    event = normalise_event(pr_payload())
    assert relay_event(event, registry, notifier) is True
    assert relay_event(event, registry, notifier) is False
    assert len(notifier.with_actions) == 1


def test_unbound_session_event_is_not_relayed():
    notifier = ActionNotifier()
    event = normalise_event(pr_payload(session_id="ghost"))
    assert relay_event(event, SessionRegistry(), notifier) is False
    assert notifier.with_actions == []


# --- callback extraction + ingress ------------------------------------------

def test_callback_query_extracted():
    msg = extract_message(callback_update())
    assert msg is not None
    assert msg.kind == MessageKind.CALLBACK
    assert msg.callback_data == "approve:sess-1"
    assert msg.chat_id == 42


def test_callback_enqueued_and_spinner_answered():
    notifier = ActionNotifier()
    queue = RecordingQueue()
    client = TestClient(
        create_app(secret=SECRET, notifier=notifier, queue=queue)
    )
    r = client.post(
        "/webhook", json=callback_update(), headers={HEADER: SECRET}
    )
    assert r.status_code == 200
    assert queue.jobs[0].source == "callback"
    assert queue.jobs[0].text == "approve:sess-1"
    assert notifier.answers[0][0] == "cb-1"
    assert notifier.messages == []  # no ack message for a button press


# --- actions -----------------------------------------------------------------

@pytest.mark.parametrize("data,expected", [
    ("approve:sess-1", "approve"),
    ("reprompt:sess-1", "reprompt"),
    ("close:sess-1", "close"),
    ("bogus:sess-1", None),
    ("", None),
])
def test_parse_callback(data, expected):
    parsed = parse_callback(data)
    assert (parsed[0] if parsed else None) == expected


def test_approve_calls_hook_and_confirms():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)
    approved = []
    assert handle_callback(
        "approve:sess-1", 42, registry, notifier, None,
        on_approve=approved.append,
    ) is True
    assert approved == ["sess-1"]
    assert "已批准" in notifier.messages[-1][1]


def test_reprompt_sends_followup_to_devin():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)
    devin = DevinStub()
    assert handle_callback("reprompt:sess-1", 42, registry, notifier, devin) is True
    assert devin.messages[0][0] == "sess-1"
    assert "draft PR" in devin.messages[0][1]


def test_close_cancels_and_unregisters():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)
    devin = DevinStub()
    assert handle_callback("close:sess-1", 42, registry, notifier, devin) is True
    assert devin.cancelled == ["sess-1"]
    assert registry.chat_for("sess-1") is None


def test_button_from_wrong_chat_is_rejected():
    notifier = ActionNotifier()
    registry = SessionRegistry()
    registry.register("sess-1", 42)  # owned by chat 42
    devin = DevinStub()
    assert handle_callback("close:sess-1", 99, registry, notifier, devin) is False
    assert devin.cancelled == []  # nobody else can cancel it
