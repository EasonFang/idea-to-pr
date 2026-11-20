"""Worker pipeline.

Flow: text -> parse scoped spec -> [CONFIRM gate] -> open GitHub Issues
-> delegate to Devin -> relay progress -> draft PR.

CRITICAL PRINCIPLE: nothing is delegated until the scoped spec is confirmed.
A fuzzy prompt handed straight to an agent is this project's biggest failure
mode; the confirmation step is the guardrail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from devin import DevinClient, RetryGuard
from github_issues import GitHubIssues
from models import Job, Spec
from notifier import Notifier
from spec import parse_idea

logger = logging.getLogger(__name__)

SCAFFOLD_PREFIX = """You are implementing a small scoped feature.
Constraints (non-negotiable):
- Pin all dependencies with a lockfile (requirements.lock or package-lock.json).
- Write a failing reproduction test before changing code.
- Make the fix, run the full test suite and CI, and iterate until green.
- Open a DRAFT pull request when done. Never force-push.
- If you fail twice on the same file with the same error, stop and report.

Scoped spec:
"""


@dataclass
class WorkerDeps:
    notifier: Notifier
    issues: GitHubIssues
    devin: DevinClient | None
    transcriber=None
    spec_parser=None
    max_retries: int = 3


class Worker:
    def __init__(self, deps: WorkerDeps):
        self.deps = deps
        self.active_sessions: dict[int, str] = {}  # chat_id -> devin session id

    def handle(self, job: Job) -> None:
        text = job.text
        if job.source == "voice" and self.deps.transcriber is not None:
            try:
                text = self.deps.transcriber.transcribe_voice(job.text)
            except Exception as exc:  # noqa: BLE001
                self.deps.notifier.send_message(job.chat_id, f"转写失败：{exc}")
                return

        stripped = text.strip()
        if stripped == "/stop":
            self.stop(job.chat_id)
            return
        if stripped in ("/start", "/help"):
            self.deps.notifier.send_message(
                job.chat_id,
                "发一个想法给我，我会整理成规格；确认后开 Issue 并交给 Devin。/stop 取消。",
            )
            return

        spec = parse_idea(text, parser=self.deps.spec_parser)

        # Guardrail: send the scoped spec for confirmation before delegating.
        self.deps.notifier.send_message(
            job.chat_id,
            f"规格：{spec.title}\n{spec.summary}\n"
            f"子任务：{', '.join(spec.issues) or '（无）'}\n确认请回复 /confirm，取消回复 /stop。",
        )

        if stripped.startswith("/confirm"):
            self.confirm_and_delegate(job.chat_id, spec)

    def confirm_and_delegate(self, chat_id: int, spec: Spec) -> None:
        results = self.deps.issues.create_many(spec.issues or [spec.title])
        self.deps.notifier.progress(
            chat_id, f"已开 {sum(r.created for r in results)} 个 Issue（去重后）"
        )

        if self.deps.devin is None:
            self.deps.notifier.send_message(chat_id, "Devin 未配置，跳过委派。")
            return

        prompt = SCAFFOLD_PREFIX + spec.title + "\n" + spec.summary
        guard = RetryGuard(max_retries=self.deps.max_retries)
        try:
            session = self.deps.devin.create_session(prompt)
            self.active_sessions[chat_id] = session.session_id
            self.deps.notifier.progress(chat_id, f"Devin 已启动：{session.session_id}")

            # Poll with guards (simplified loop; real impl uses webhooks/events).
            for _ in range(self.deps.max_retries):
                current = self.deps.devin.get_session(session.session_id)
                self.deps.notifier.progress(chat_id, f"状态：{current.status}")
                if current.status in ("completed", "finished", "draft_pr_opened"):
                    self.deps.notifier.send_message(chat_id, "Devin 完成，draft PR 已就绪。")
                    self.active_sessions.pop(chat_id, None)
                    return
                if current.status in ("error", "blocked", "failed"):
                    decision = guard.record_failure("scaffold", current.status)
                    if decision.should_stop:
                        self.deps.notifier.send_message(
                            chat_id, f"已停止：{decision.reason}。请查看错误尾部。"
                        )
                        self.stop(chat_id)
                        return
        except Exception as exc:  # noqa: BLE001
            self.deps.notifier.send_message(chat_id, f"Devin 调用失败：{exc}")
        finally:
            self.active_sessions.pop(chat_id, None)

    def stop(self, chat_id: int) -> None:
        sid = self.active_sessions.pop(chat_id, None)
        if sid and self.deps.devin is not None:
            self.deps.devin.cancel_session(sid)
            self.deps.notifier.send_message(chat_id, "已取消当前 Devin session。")
        else:
            self.deps.notifier.send_message(chat_id, "当前没有运行中的 session。")
