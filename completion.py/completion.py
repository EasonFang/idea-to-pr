"""Completion flow: Devin event -> Telegram buttons -> user action.

This is the piece that turns "Devin finished" into a decision the user can
actually make without leaving Telegram:

    Devin webhook -> /devin-webhook -> normalise -> find chat via registry
        -> sendMessage with inline keyboard [Approve | Re-prompt | Close]
    button press -> /webhook (callback_query) -> queue -> Worker.handle
        -> approve / reprompt / close -> Devin API + confirmation message

Every external call is best-effort. The webhook always answers 200 so neither
Devin nor Telegram retries the event and duplicates the notification.
"""

from __future__ import annotations

import logging
from typing import Callable

from devin import DevinClient
from devin_events import DevinEvent, DevinEventKind
from notifier import Notifier
from sessions import SessionRegistry

logger = logging.getLogger(__name__)


def event_message(event: DevinEvent) -> tuple[str, list[tuple[str, str]]]:
    """Build the Telegram text + inline buttons for one Devin event."""
    sid = event.session_id
    links: list[str] = []
    if event.pr_url:
        links.append(f"Draft PR: {event.pr_url}")
    if event.session_url:
        links.append(f"Session: {event.session_url}")
    link_block = "\n".join(links)

    if event.kind == DevinEventKind.PR_OPENED:
        text = f"Devin 已给出 draft PR。{event.summary}\n{link_block}\n请选择操作："
    elif event.kind == DevinEventKind.BLOCKED:
        text = f"Devin 需要你的输入：{event.summary or '任务被阻塞'}\n{link_block}\n请选择操作："
    elif event.kind == DevinEventKind.FAILED:
        text = f"Devin 运行失败：{event.summary or '未知错误'}\n{link_block}\n可以选择重试或关闭："
    elif event.kind == DevinEventKind.COMPLETED:
        text = f"Devin 已完成。{event.summary}\n{link_block}\n请选择操作："
    else:
        text = f"Devin 更新：{event.summary or '进度更新'}\n{link_block}"

    actions: list[tuple[str, str]] = []
    if sid:
        actions = [
            ("✅ Approve", f"approve:{sid}"),
            ("🔁 Re-prompt", f"reprompt:{sid}"),
            ("⏹ Close", f"close:{sid}"),
        ]
    return text, actions


def relay_event(
    event: DevinEvent,
    registry: SessionRegistry,
    notifier: Notifier,
) -> bool:
    """Route one event to the originating chat. Returns False if nothing sent."""
    if event.kind == DevinEventKind.UNKNOWN:
        return False
    chat_id = registry.chat_for(event.session_id)
    if chat_id is None:
        logger.info("devin event for unbound session %s; ignored", event.session_id)
        return False
    if not registry.is_new_event(event.event_id):
        logger.info("duplicate devin event %s; ignored", event.event_id)
        return False
    text, actions = event_message(event)
    return notifier.send_with_actions(chat_id, text, actions)


def parse_callback(data: str) -> tuple[str, str] | None:
    """'approve:sess-1' -> ('approve', 'sess-1'). Tolerates extra colons."""
    if not data or ":" not in data:
        return None
    action, session_id = data.split(":", 1)
    if action not in ("approve", "reprompt", "close") or not session_id:
        return None
    return action, session_id


REPROMPT_TEXT = (
    "Continue from the current draft PR. Address review/CI feedback, keep the "
    "original scope, run the full test suite, and report the final PR URL."
)


def handle_callback(
    data: str,
    chat_id: int,
    registry: SessionRegistry,
    notifier: Notifier,
    devin: DevinClient | None,
    on_approve: Callable[[str], None] | None = None,
) -> bool:
    """Execute a Telegram button action. Never raises to the webhook."""
    parsed = parse_callback(data)
    if parsed is None:
        return False
    action, session_id = parsed

    # A button can only control a session that the registry says this chat owns.
    if registry.chat_for(session_id) != chat_id:
        notifier.send_message(chat_id, "该 session 不属于当前会话或已关闭。")
        return False

    if action == "approve":
        if on_approve is not None:
            try:
                on_approve(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("approve hook failed: %s", exc)
        notifier.send_message(chat_id, f"已批准 {session_id}。可以合并 draft PR 了。")
        return True

    if action == "reprompt":
        if devin is None:
            notifier.send_message(chat_id, "Devin 未配置，无法重新提示。")
            return False
        try:
            devin.send_message(session_id, REPROMPT_TEXT)
            notifier.send_message(chat_id, f"已向 Devin 追加指令：{session_id}")
            return True
        except Exception as exc:  # noqa: BLE001
            notifier.send_message(chat_id, f"重新提示失败：{exc}")
            return False

    # close
    if devin is not None:
        try:
            devin.cancel_session(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel failed: %s", exc)
    registry.unregister(session_id)
    notifier.send_message(chat_id, f"已关闭 session {session_id}。")
    return True
