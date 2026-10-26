"""双栏会话的栏位数据：打开、替换、关闭与去重。

页面只负责把这里算出的 ``panes`` 状态画出来；顶部会话条回传的
``{left, right}`` 也先经过 ``sanitize_assignment``，不认识的会话 id
会被丢掉，避免旧组件值或脏消息把页面带到空会话。

左右两栏各自保存自己的公司、助手与消息；``right`` 为 ``None`` 表示
页面还是默认的一整栏。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

PANE_SIDES = ("left", "right")


def new_pane() -> dict[str, Any]:
    """一栏的初始状态；新建对话、打开历史都从这里开始。"""
    return {
        "code": None,
        "name": "",
        "conversation_id": None,
        "messages": [],
        "agent": None,
        "agent_code": None,
        "agent_error": "",
        "notice": "",
        "pending": None,
    }


def pane_ids(panes: dict[str, Any]) -> dict[str, int | None]:
    """两栏当前打开的会话 id，交给顶部会话条标记「左 / 右」。"""

    def conversation_id(pane: Any) -> int | None:
        if not isinstance(pane, dict):
            return None
        value = pane.get("conversation_id")
        return int(value) if value else None

    return {
        "left": conversation_id(panes.get("left")),
        "right": conversation_id(panes.get("right")),
    }


def sanitize_assignment(
    incoming: Any, known_ids: Iterable[int]
) -> dict[str, int | None]:
    """整理组件回传的栏位：未知 id 丢弃；同一会话不能同时占两栏。"""
    known = {int(value) for value in known_ids}
    result: dict[str, int | None] = {"left": None, "right": None}
    if not isinstance(incoming, dict):
        return result
    for side in PANE_SIDES:
        try:
            conversation_id = int(incoming.get(side))
        except (TypeError, ValueError):
            conversation_id = None
        if conversation_id in known:
            result[side] = conversation_id
    # 同一段对话同时出现在两栏会让两边的回答互相覆盖，右栏视为没打开
    if result["right"] is not None and result["right"] == result["left"]:
        result["right"] = None
    return result


def pane_from_conversation(conversation, messages) -> dict[str, Any]:
    """把一条历史会话装进新的栏位状态。"""
    pane = new_pane()
    pane["code"] = conversation.primary_code or None
    pane["name"] = conversation.primary_name or conversation.primary_code or ""
    pane["conversation_id"] = int(conversation.id)
    pane["messages"] = [dict(message) for message in messages]
    pane["notice"] = f"已打开对话：{conversation.title}"
    return pane


def open_in_left(
    panes: dict[str, Any], conversation, messages_for: Callable[[int], list[dict]]
) -> dict[str, Any]:
    """点击会话条：在左栏打开；如果右栏正好是同一段，收掉右栏。"""
    right = panes.get("right")
    if right and right.get("conversation_id") == conversation.id:
        right = None
    return {
        "left": pane_from_conversation(
            conversation, messages_for(int(conversation.id))
        ),
        "right": right,
    }


def assign_panes(
    panes: dict[str, Any],
    incoming: Any,
    conversations: dict[int, Any],
    messages_for: Callable[[int], list[dict]],
) -> dict[str, Any]:
    """按会话条回传的栏位打开/关闭两侧，返回新的 ``panes``。

    ``left`` 回传 ``None`` 表示组件那边左栏不是已保存的会话（新建、还没
    落库），保持页面当前的左栏不动；右栏 ``None`` 就是关闭右栏。
    """
    ids = sanitize_assignment(incoming, conversations)
    left_id, right_id = ids["left"], ids["right"]

    left = panes.get("left") or new_pane()
    if left_id is not None and left_id != left.get("conversation_id"):
        left = pane_from_conversation(
            conversations[left_id], messages_for(left_id)
        )

    right = panes.get("right")
    current_right_id = right.get("conversation_id") if right else None
    if right_id is None:
        right = None
    elif right_id != current_right_id:
        right = pane_from_conversation(
            conversations[right_id], messages_for(right_id)
        )
    return {"left": left, "right": right}
