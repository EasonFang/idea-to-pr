"""Tests for issue dedup, retry guard, and the confirm-before-delegate rule.

These cover the PRD's "后续需补" items: Issue dedup idempotency and Devin
retry-limit / loop simulation.
"""

from __future__ import annotations

import pytest

from devin import RetryGuard
from github_issues import GitHubIssues, IssueStore, title_hash
from models import Job, Spec
from notifier import Notifier
from worker import Worker, WorkerDeps


class DummyNotifier:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text):
        self.messages.append(text)
        return True

    def progress(self, chat_id, text):
        self.messages.append(text)
        return True


# --- dedup -------------------------------------------------------------------

def test_title_hash_normalises_case_and_whitespace():
    assert title_hash("  Build Todo App  ") == title_hash("build   todo app")


def test_duplicate_issue_not_created_twice():
    store = IssueStore(":memory:")
    issues = GitHubIssues("token", "owner/repo", store)  # no transport -> dry run
    first = issues.create("Build Todo App")
    second = issues.create("build   todo app")
    assert first.created is True
    assert second.created is False
    assert second.number == first.number
    store.close()


# --- retry guard -------------------------------------------------------------

def test_retry_limit_stops_at_cap():
    guard = RetryGuard(max_retries=3)
    assert guard.record_failure("a.py", "e1").should_stop is False
    assert guard.record_failure("b.py", "e2").should_stop is False
    decision = guard.record_failure("c.py", "e3")
    assert decision.should_stop is True
    assert "retry limit" in decision.reason


def test_loop_detection_stops_early():
    guard = RetryGuard(max_retries=10)
    guard.record_failure("broken.py", "ModuleNotFound")
    decision = guard.record_failure("broken.py", "ModuleNotFound")
    assert decision.should_stop is True
    assert "loop detected" in decision.reason


def test_distinct_failures_do_not_trigger_loop():
    guard = RetryGuard(max_retries=5)
    assert guard.record_failure("a.py", "e1").should_stop is False
    assert guard.record_failure("b.py", "e2").should_stop is False


# --- confirm gate ------------------------------------------------------------

def test_no_confirmation_does_not_delegate():
    """Sending an idea alone must NOT open issues or start Devin."""
    notifier = DummyNotifier()
    store = IssueStore(":memory:")
    issues = GitHubIssues("t", "o/r", store)

    class DevinSpy:
        def __init__(self):
            self.calls = 0

        def create_session(self, prompt):
            self.calls += 1
            raise AssertionError("must not delegate before confirmation")

    spy = DevinSpy()
    worker = Worker(
        WorkerDeps(notifier=notifier, issues=issues, devin=spy, max_retries=3)
    )
    worker.handle(Job(chat_id=1, text="Build a todo app\n- add items\n- persist"))

    assert spy.calls == 0
    assert any("规格" in m for m in notifier.messages)
    store.close()


def test_stop_without_session_is_safe():
    notifier = DummyNotifier()
    store = IssueStore(":memory:")
    worker = Worker(
        WorkerDeps(
            notifier=notifier,
            issues=GitHubIssues("t", "o/r", store),
            devin=None,
        )
    )
    worker.handle(Job(chat_id=1, text="/stop"))
    assert any("没有运行" in m for m in notifier.messages)
    store.close()
