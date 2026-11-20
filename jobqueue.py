"""Queue abstraction.

M1 runs an in-process synchronous queue so the whole chain works with zero
infrastructure. The ``Queue`` protocol is intentionally tiny so it can later
be backed by Celery/Redis or an HTTP-to-TS worker without changing callers.

Only the ingress calls ``enqueue``; all heavy lifting lives in the worker.
"""

from __future__ import annotations

import logging
from typing import Protocol

from models import Job

logger = logging.getLogger(__name__)


class JobQueue(Protocol):
    def enqueue(self, job: Job) -> None: ...


class InProcessQueue:
    """Synchronous queue for M1. Calls the handler inline.

    Fast enough for single-user use; swap for Celery before multi-user.
    """

    def __init__(self, handler=None):
        self.handler = handler

    def enqueue(self, job: Job) -> None:
        if self.handler is None:
            logger.info("job queued (no handler): chat=%s", job.chat_id)
            return
        try:
            self.handler(job)
        except Exception as exc:  # a worker crash must not break the webhook
            logger.exception("worker failed for job %s: %s", job.chat_id, exc)


class RecordingQueue:
    """Test double that just records enqueued jobs."""

    def __init__(self):
        self.jobs: list[Job] = []

    def enqueue(self, job: Job) -> None:
        self.jobs.append(job)
