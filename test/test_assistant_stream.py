"""流式回答：片段事件、工具轮次丢弃草稿与历史回滚。"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.agent as agent_mod
from assistant.agent import ResearchAgent
from assistant.models import Source, ToolResult

FIN = Source(
    "finance:605499:2026-06-30",
    "财报",
    "东鹏饮料 2026-06-30",
    "2026-06-30",
    "2026-10-24 15:57:57",
)
ANSWER = "能量饮料是主力[[finance:605499:2026-06-30]]。"


def chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def call_delta(index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class StreamClient:
    """每一轮是一串流式 chunk；create 收到 stream=True 才返回迭代器。"""

    def __init__(self, rounds):
        self.calls = []
        self._rounds = list(rounds)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._rounds.pop(0))


class ExplodingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        def explode():
            raise RuntimeError("连接中断")
            yield  # pragma: no cover

        return explode()


def _tool_call_round():
    return [
        chunk(tool_calls=[call_delta(0, id="c1", name="get_finance", arguments="")]),
        chunk(tool_calls=[call_delta(0, arguments='{"code"')]),
        chunk(tool_calls=[call_delta(0, arguments=': "605499"}')]),
    ]


def _answer_round():
    return [
        chunk(content="能量饮料是"),
        chunk(content="主力[[finance:605499:2026-06-30]]"),
        chunk(content="。"),
    ]


def _patch_tool():
    original = agent_mod.run_tool
    agent_mod.run_tool = lambda name, arguments, default_company=None: ToolResult(
        ok=True,
        company="605499",
        as_of="2026-06-30",
        sources=(FIN,),
        data={"revenue_yi": 124.43},
    )
    return original


def test_stream_text_and_tool_events() -> None:
    client = StreamClient([_tool_call_round(), _answer_round()])
    original = _patch_tool()
    try:
        agent = ResearchAgent(client, default_company="605499")
        events: list[dict] = []
        answer = agent.ask_stream("靠什么赚钱", events.append)
    finally:
        agent_mod.run_tool = original

    assert client.calls[0]["stream"] is True

    kinds = [event["type"] for event in events]
    assert kinds.count("discard") == 1, kinds
    assert kinds.count("tool") == 1, kinds
    assert "".join(
        event["delta"] for event in events if event["type"] == "text"
    ) == ANSWER

    tool_event = next(event for event in events if event["type"] == "tool")
    assert tool_event["name"] == "get_finance"
    assert tool_event["company"] == "605499"
    assert tool_event["ok"] is True
    # 每次工具调用都带耗时，流式事件和最终 trace 一致
    assert isinstance(tool_event["duration_ms"], int), tool_event
    assert tool_event["duration_ms"] >= 0
    assert answer.trace[0]["duration_ms"] == tool_event["duration_ms"]

    assert answer.text == ANSWER
    assert [citation.id for citation in answer.citations] == [FIN.id]
    assert answer.rounds == 2

    # 流式拼出来的工具调用要能回放
    assistant_entry = next(
        message for message in agent._history if message.get("tool_calls")
    )
    assert assistant_entry["tool_calls"][0]["function"] == {
        "name": "get_finance",
        "arguments": '{"code": "605499"}',
    }


def test_stream_discards_draft_before_tool_round() -> None:
    """模型先说了句话再调工具：discard 要排在草稿之后、正式答案之前。"""
    client = StreamClient([
        [chunk(content="我先查一下财报。"), chunk(tool_calls=[
            call_delta(0, id="c1", name="get_finance", arguments="{}")
        ])],
        _answer_round(),
    ])
    original = _patch_tool()
    try:
        agent = ResearchAgent(client, default_company="605499")
        events: list[dict] = []
        agent.ask_stream("靠什么赚钱", events.append)
    finally:
        agent_mod.run_tool = original

    discard_at = next(
        index for index, event in enumerate(events) if event["type"] == "discard"
    )
    draft_at = next(
        index for index, event in enumerate(events)
        if event["type"] == "text" and "我先查一下" in event["delta"]
    )
    answer_at = next(
        index for index, event in enumerate(events)
        if event["type"] == "text" and "能量饮料" in event["delta"]
    )
    assert draft_at < discard_at < answer_at, events


def test_stream_error_rolls_back_history() -> None:
    agent = ResearchAgent(ExplodingClient(), default_company="605499")
    try:
        agent.ask_stream("靠什么赚钱", lambda event: None)
    except RuntimeError as error:
        assert "连接中断" in str(error)
    else:  # pragma: no cover
        raise AssertionError("应当抛出异常")
    assert agent.history_size() == 0


def main() -> None:
    test_stream_text_and_tool_events()
    test_stream_discards_draft_before_tool_round()
    test_stream_error_rolls_back_history()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
