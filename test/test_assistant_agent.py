"""ReAct 循环：工具调用、引用、轮次上限、多轮与对照。"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import httpx2
from openai import APITimeoutError, AuthenticationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.agent as agent_mod
from assistant.models import Source, ToolResult

REQUEST = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")


def timeout_error() -> APITimeoutError:
    return APITimeoutError(request=REQUEST)


class FlakyClient:
    """前 ``failures`` 次调用抛超时，之后按脚本返回。"""

    def __init__(self, responses, failures=0):
        self.calls = []
        self.failures = failures
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise timeout_error()
        return self._responses.pop(0)

FIN = Source("finance:605499:2026-06-30", "财报", "东鹏饮料 2026-06-30",
             "2026-06-30", "2026-10-24 15:57:57")
MT = Source("finance:600519:2026-06-30", "财报", "贵州茅台 2026-06-30",
            "2026-06-30", "2026-10-24 15:57:57")


def response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


class FakeClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def test_loop_and_citation() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="能量饮料是主力[[finance:605499:2026-06-30]]。"),
    ])
    original = agent_mod.run_tool
    agent_mod.run_tool = lambda name, arguments, default_company=None: ToolResult(
        ok=True, company="605499", as_of="2026-06-30", sources=(FIN,),
        data={"revenue_yi": 124.43},
    )
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        answer = research.ask("它靠什么赚钱")
    finally:
        agent_mod.run_tool = original

    assert answer.text == "能量饮料是主力[[finance:605499:2026-06-30]]。"
    assert [source.id for source in answer.citations] == [FIN.id]
    assert answer.trace == [{
        "round": 1, "name": "get_finance", "arguments": {},
        "ok": True, "company": "605499", "as_of": "2026-06-30",
        "error": None, "sources": [FIN.id],
    }], answer.trace
    assert answer.issues == [] and answer.rounds == 2
    assert len(client.calls) == 2
    first, second = client.calls
    assert first["model"] == "deepseek-flash"
    assert first["messages"][0]["role"] == "system"
    assert "load_skill" in first["messages"][0]["content"]
    assert first["messages"][-1] == {"role": "user", "content": "它靠什么赚钱"}
    assert second["messages"][2]["role"] == "assistant"
    assert second["messages"][2]["tool_calls"][0]["id"] == "c1"
    tool_message = second["messages"][3]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert "finance:605499:2026-06-30" in tool_message["content"]


def test_unknown_citation_and_round_limit() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="营收增长[[nope]]。"),
    ])
    original = agent_mod.run_tool
    agent_mod.run_tool = lambda *args, **kwargs: ToolResult(
        ok=True, company="605499", as_of="2026-06-30", sources=(FIN,), data={}
    )
    try:
        answer = agent_mod.ResearchAgent(
            client, default_company="605499"
        ).ask("营收怎么样")
    finally:
        agent_mod.run_tool = original
    assert "[?]" in answer.text and answer.issues[0]["type"] == "unknown_citation"

    looping = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(tool_calls=[tool_call("c2", "get_finance", {})]),
    ])
    agent_mod.run_tool = lambda *args, **kwargs: ToolResult(
        ok=True, company="605499", as_of="2026-06-30", sources=(FIN,), data={}
    )
    try:
        answer = agent_mod.ResearchAgent(
            looping, default_company="605499", max_rounds=2
        ).ask("继续")
    finally:
        agent_mod.run_tool = original
    assert answer.text == ""
    assert any(issue["type"] == "rounds_exhausted" for issue in answer.issues)
    assert len(looping.calls) == 2


def test_tool_error_observation_and_multi_turn() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="没有拿到财报，无法判断。"),
        response(content="上一轮已经说过，资料不足。"),
    ])
    original = agent_mod.run_tool
    agent_mod.run_tool = lambda *args, **kwargs: ToolResult(
        ok=False, company="000001", error="没有财报数据"
    )
    try:
        research = agent_mod.ResearchAgent(client, default_company="000001")
        first = research.ask("它靠什么赚钱")
        second = research.ask("再确认一下")
    finally:
        agent_mod.run_tool = original
    assert first.text.startswith("没有拿到财报")
    tool_message = client.calls[1]["messages"][3]
    assert "没有财报数据" in tool_message["content"]
    history = client.calls[2]["messages"]
    assert {"role": "user", "content": "它靠什么赚钱"} in history
    assert {"role": "assistant", "content": "没有拿到财报，无法判断。"} in history
    assert history[-1] == {"role": "user", "content": "再确认一下"}


def test_comparison_sources() -> None:
    client = FakeClient([
        response(tool_calls=[
            tool_call("c1", "get_finance", {"company": "600519"}),
            tool_call("c2", "get_finance", {}),
        ]),
        response(content=(
            "茅台更成熟[[finance:600519:2026-06-30]]，"
            "东鹏增速更高[[finance:605499:2026-06-30]]。"
        )),
    ])
    seen = []

    def fake_run_tool(name, arguments, default_company=None):
        seen.append((name, arguments, default_company))
        code = arguments.get("company") or default_company
        source = MT if code == "600519" else FIN
        return ToolResult(ok=True, company=code, as_of="2026-06-30",
                          sources=(source,), data={})

    original = agent_mod.run_tool
    agent_mod.run_tool = fake_run_tool
    try:
        answer = agent_mod.ResearchAgent(
            client, default_company="605499"
        ).ask("跟茅台比，它靠什么赚钱")
    finally:
        agent_mod.run_tool = original
    assert seen == [
        ("get_finance", {"company": "600519"}, "605499"),
        ("get_finance", {}, "605499"),
    ], seen
    assert [source.id for source in answer.citations] == [MT.id, FIN.id]


def test_retry_once() -> None:
    client = FlakyClient([response(content="每年卖出几十亿瓶饮料。")], failures=1)
    answer = agent_mod.ResearchAgent(client, default_company="605499").ask("靠什么赚钱")
    assert answer.text == "每年卖出几十亿瓶饮料。"
    assert answer.rounds == 1
    assert len(client.calls) == 2  # 第一次超时，重试成功

    dead = FlakyClient([], failures=2)
    try:
        agent_mod.ResearchAgent(dead, default_company="605499").ask("靠什么赚钱")
    except agent_mod.AssistantError as error:
        assert "接口失败" in str(error)
        assert "DEEPSEEK_API_KEY" not in str(error)
    else:
        raise AssertionError("两次失败应抛 AssistantError")
    assert len(dead.calls) == 2


def test_non_transient_error_is_not_retried() -> None:
    response_401 = httpx2.Response(401, request=REQUEST)
    calls = {"count": 0}

    class AuthClient(FakeClient):
        def _create(self, **kwargs):
            calls["count"] += 1
            raise AuthenticationError(
                "invalid api key", response=response_401, body=None
            )

    client = AuthClient([])
    try:
        agent_mod.ResearchAgent(client, default_company="605499").ask("靠什么赚钱")
    except agent_mod.AssistantError as error:
        assert "invalid api key" in str(error)
    else:
        raise AssertionError("鉴权失败应抛 AssistantError")
    assert calls["count"] == 1, calls


def test_build_client_disables_sdk_retries() -> None:
    original = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "test-key"
    try:
        client = agent_mod.build_client()
    finally:
        if original is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = original
    try:
        assert client.max_retries == 0, client.max_retries
    finally:
        client.close()


def test_tool_exception_becomes_observation() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="工具坏了，资料不足。"),
    ])
    original = agent_mod.run_tool

    def broken(*args, **kwargs):
        raise RuntimeError("数据库被占用")

    agent_mod.run_tool = broken  # type: ignore[assignment]
    try:
        answer = agent_mod.ResearchAgent(
            client, default_company="605499"
        ).ask("靠什么赚钱")
    finally:
        agent_mod.run_tool = original  # type: ignore[assignment]

    assert answer.text == "工具坏了，资料不足。"
    tool_messages = [
        message for message in client.calls[1]["messages"] if message["role"] == "tool"
    ]
    assert len(tool_messages) == 1, tool_messages
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "数据库被占用" in tool_messages[0]["content"]
    assert answer.trace[0]["ok"] is False


def test_api_failure_rolls_back_history() -> None:
    client = FlakyClient([response(content="它卖能量饮料。")], failures=2)
    research = agent_mod.ResearchAgent(client, default_company="605499")
    try:
        research.ask("被放弃的问题")
    except agent_mod.AssistantError:
        pass
    else:
        raise AssertionError("两次失败应抛 AssistantError")

    answer = research.ask("真正的问题")
    assert answer.text == "它卖能量饮料。"
    history = [
        message.get("content")
        for message in client.calls[-1]["messages"]
        if message.get("role") == "user"
    ]
    assert history == ["真正的问题"], history


def test_prompt_has_concrete_citation_example() -> None:
    from assistant.prompts import SYSTEM_PROMPT

    assert "[[finance:605499:2026-06-30]]" in SYSTEM_PROMPT
    # "[[引用id]]" 会被模型当成字面占位符，原样写进正文
    assert "[[引用id]]" not in SYSTEM_PROMPT


def test_prompt_is_skill_based() -> None:
    from assistant.prompts import SYSTEM_PROMPT

    assert "load_skill" in SYSTEM_PROMPT
    assert "宏观" in SYSTEM_PROMPT
    assert "多个" in SYSTEM_PROMPT
    # 具体判据必须只在 skill 里，不能回到系统提示词
    for phrase in ("第一问", "第二问", "护城河", "三句话", "品牌", "可证伪"):
        assert phrase not in SYSTEM_PROMPT, phrase
    assert "只回答第一问" not in SYSTEM_PROMPT


def test_broad_question_loads_both_skills() -> None:
    client = FakeClient([
        response(tool_calls=[
            tool_call("s1", "load_skill", {"name": "business_model"}),
            tool_call("s2", "load_skill", {"name": "moat"}),
        ]),
        response(tool_calls=[tool_call("c1", "get_financial_history", {})]),
        response(content=(
            "第一问：卖能量饮料[[finance:605499:2026-06-30]]。\n"
            "第二问：渠道规模是主要优势[[finance_history:605499:2021-2025]]。"
        )),
    ])
    history = Source(
        "finance_history:605499:2021-2025", "财报·年度序列",
        "东鹏饮料 2021-2025 年度财务", "2025-12-31", "2026-10-24 15:00:00",
    )
    def fake_run_tool(name, arguments, default_company=None):
        if name == "load_skill":
            return ToolResult(ok=True, data={})
        return ToolResult(
            ok=True, company="605499", as_of="2025-12-31",
            sources=(FIN, history), data={},
        )

    original = agent_mod.run_tool
    agent_mod.run_tool = fake_run_tool
    try:
        answer = agent_mod.ResearchAgent(
            client, default_company="605499"
        ).ask("这家公司基本面如何")
    finally:
        agent_mod.run_tool = original

    names = [step["name"] for step in answer.trace]
    assert names == [
        "load_skill", "load_skill", "get_financial_history"
    ], names
    assert answer.trace[0]["arguments"] == {"name": "business_model"}
    assert answer.trace[1]["arguments"] == {"name": "moat"}
    assert "第一问" in answer.text and "第二问" in answer.text
    assert [source.id for source in answer.citations] == [
        FIN.id, history.id
    ], answer.citations


def test_use_company() -> None:
    client = FakeClient([])
    research = agent_mod.ResearchAgent(client, default_company="605499")
    original = agent_mod.run_tool
    agent_mod.run_tool = lambda *args, **kwargs: ToolResult(
        ok=True, company="600519", sources=(), data={"code": "600519"}
    )
    try:
        assert research.use_company("贵州茅台") == "600519"
        assert research.default_company == "600519"
    finally:
        agent_mod.run_tool = original

    agent_mod.run_tool = lambda *args, **kwargs: ToolResult(
        ok=False, error="没有找到「不存在」"
    )
    try:
        try:
            research.use_company("不存在")
        except agent_mod.AssistantError as error:
            assert "没有找到" in str(error)
        else:
            raise AssertionError("解析失败应抛 AssistantError")
    finally:
        agent_mod.run_tool = original


def main() -> None:
    test_loop_and_citation()
    test_unknown_citation_and_round_limit()
    test_tool_error_observation_and_multi_turn()
    test_comparison_sources()
    test_retry_once()
    test_non_transient_error_is_not_retried()
    test_build_client_disables_sdk_retries()
    test_tool_exception_becomes_observation()
    test_api_failure_rolls_back_history()
    test_prompt_has_concrete_citation_example()
    test_prompt_is_skill_based()
    test_broad_question_loads_both_skills()
    test_use_company()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
