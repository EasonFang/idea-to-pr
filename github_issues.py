"""GitHub Issues creation with idempotent, hash-based de-duplication.

Dedup key = sha256 of the normalised title (lowercase, whitespace collapsed).
Repeated ideas therefore never open duplicate issues.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def normalise_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def title_hash(title: str) -> str:
    return hashlib.sha256(normalise_title(title).encode("utf-8")).hexdigest()


@dataclass
class IssueResult:
    number: int
    url: str
    created: bool  # False means a duplicate was reused


class IssueStore:
    """SQLite-backed record of titles we have already opened."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS issues ("
            "hash TEXT PRIMARY KEY, title TEXT, number INTEGER, url TEXT)"
        )
        self.conn.commit()

    def exists(self, h: str) -> IssueResult | None:
        row = self.conn.execute(
            "SELECT title, number, url FROM issues WHERE hash=?", (h,)
        ).fetchone()
        if row is None:
            return None
        return IssueResult(number=row[1], url=row[2], created=False)

    def record(self, h: str, title: str, number: int, url: str) -> IssueResult:
        self.conn.execute(
            "INSERT OR REPLACE INTO issues (hash, title, number, url) VALUES (?,?,?,?)",
            (h, title, number, url),
        )
        self.conn.commit()
        return IssueResult(number=number, url=url, created=True)

    def close(self):
        self.conn.close()


class GitHubIssues:
    def __init__(self, token: str, repo: str, store: IssueStore, transport=None):
        self.token = token
        self.repo = repo  # owner/name
        self.store = store
        self.transport = transport  # injectable callable(method, url, **kw)

    def create(self, title: str, body: str = "") -> IssueResult:
        """Create an issue, or return the existing one when de-duped."""
        h = title_hash(title)
        existing = self.store.exists(h)
        if existing is not None:
            logger.info("duplicate issue skipped: %s", title)
            return existing

        # Without a real transport (tests / dry run) we still record the dedup
        # entry so idempotency can be asserted without network.
        if not self.transport or not self.token or not self.repo:
            logger.info("no transport/token: dry-run issue %s", title)
            return self.store.record(h, title, number=0, url="")

        url = f"{GITHUB_API}/repos/{self.repo}/issues"
        resp = self.transport(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": body},
        )
        data = json.loads(resp.read())
        return self.store.record(h, title, number=data["number"], url=data["html_url"])

    def create_many(self, titles: list[str]) -> list[IssueResult]:
        return [self.create(t) for t in titles]
