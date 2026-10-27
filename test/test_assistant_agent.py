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
    step = dict(answer.trace[0])
    duration = step.pop("duration_ms", None)
    assert isinstance(duration, int) and duration >= 0, duration
    assert step == {
        "round": 1, "name": "get_finance", "arguments": {},
        "ok": True, "company": "605499", "as_of": "2026-06-30",
        "error": None, "sources": [FIN.id], "cached": False,
    }, step
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
    # 大盘问题也走 skill + 工具，不在提示词里放具体判据
    assert "market_view" in SYSTEM_PROMPT
    assert "get_market_overview" in SYSTEM_PROMPT
    assert "news_events" in SYSTEM_PROMPT
    assert "get_news_events" in SYSTEM_PROMPT
    assert "sector_rotation" in SYSTEM_PROMPT
    assert "get_sector_rotation" in SYSTEM_PROMPT
    assert "stock_screening" in SYSTEM_PROMPT
    assert "screen_candidates" in SYSTEM_PROMPT
    assert "strategy_risk" in SYSTEM_PROMPT
    assert "research_memory" in SYSTEM_PROMPT
    assert "事实" in SYSTEM_PROMPT and "推测" in SYSTEM_PROMPT
    assert "build_strategy_plan" in SYSTEM_PROMPT
    assert "review_eval" in SYSTEM_PROMPT
    assert "review_research_reports" in SYSTEM_PROMPT
    assert "save_daily_judgment" in SYSTEM_PROMPT
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


def test_tool_result_cache_reuses_same_call() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="第一次回答[[finance:605499:2026-06-30]]。"),
        response(tool_calls=[tool_call("c2", "get_finance", {})]),
        response(content="第二次回答[[finance:605499:2026-06-30]]。"),
    ])
    calls = []

    def fake_run_tool(name, arguments, default_company=None):
        calls.append((name, arguments, default_company))
        return ToolResult(
            ok=True, company="605499", as_of="2026-06-30", sources=(FIN,),
            data={"revenue_yi": 124.43},
        )

    original = agent_mod.run_tool
    agent_mod.run_tool = fake_run_tool
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        first = research.ask("靠什么赚钱")
        second = research.ask("再讲一次")
    finally:
        agent_mod.run_tool = original

    assert len(calls) == 1, calls          # 第二次命中缓存，没有重新取数
    assert first.trace[0]["cached"] is False
    assert second.trace[0]["cached"] is True
    tool_messages = [
        message for message in client.calls[3]["messages"]
        if message.get("role") == "tool"
    ]
    assert len(tool_messages) == 2, tool_messages
    # 旧的那份被换成 stub，只留来源；缓存命中的新结果带着完整 data
    old = json.loads(tool_messages[0]["content"])
    assert old["superseded"] is True and "data" not in old, old
    assert old["sources"][0]["id"] == FIN.id
    reused = json.loads(tool_messages[1]["content"])
    assert reused["cached"] is True
    assert reused["data"] == {"revenue_yi": 124.43}
    assert [source.id for source in second.citations] == [FIN.id]


def test_tool_cache_skips_failed_results() -> None:
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_finance", {})]),
        response(content="第一次没拿到。"),
        response(tool_calls=[tool_call("c2", "get_finance", {})]),
        response(content="第二次拿到了[[finance:605499:2026-06-30]]。"),
    ])
    calls = []

    def fake_run_tool(name, arguments, default_company=None):
        calls.append(name)
        if len(calls) == 1:
            return ToolResult(ok=False, company="605499", error="暂时没有数据")
        return ToolResult(
            ok=True, company="605499", as_of="2026-06-30", sources=(FIN,),
            data={"revenue_yi": 124.43},
        )

    original = agent_mod.run_tool
    agent_mod.run_tool = fake_run_tool
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.ask("靠什么赚钱")
        second = research.ask("再试一次")
    finally:
        agent_mod.run_tool = original

    assert len(calls) == 2, calls          # 失败结果未入缓存，第二次重新取数
    assert second.trace[0]["cached"] is False
    assert second.trace[0]["ok"] is True
    assert [source.id for source in second.citations] == [FIN.id]


def test_realtime_tools_bypass_cache() -> None:
    """估值/公告/业绩预告随行情和披露变化，长会话里不能一直用旧结果。"""
    valuation = Source(
        "valuation:605499:2026-10-24", "估值", "东鹏饮料 估值", "2026-10-24", ""
    )
    client = FakeClient([
        response(tool_calls=[tool_call("c1", "get_valuation", {})]),
        response(content="第一次[[valuation:605499:2026-10-24]]。"),
        response(tool_calls=[tool_call("c2", "get_valuation", {})]),
        response(content="第二次[[valuation:605499:2026-10-24]]。"),
    ])
    calls = []

    def fake_run_tool(name, arguments, default_company=None):
        calls.append(name)
        return ToolResult(
            ok=True, company="605499", as_of="2026-10-24",
            sources=(valuation,), data={"pe": 25.0},
        )

    original = agent_mod.run_tool
    agent_mod.run_tool = fake_run_tool
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.ask("现在贵不贵")
        second = research.ask("再看一眼")
    finally:
        agent_mod.run_tool = original

    assert calls == ["get_valuation", "get_valuation"], calls
    assert second.trace[0]["cached"] is False


def test_restore_seeds_tool_cache() -> None:
    """重开对话：回放出来的取数结果直接进缓存，不必重新取数。"""
    history = [
        {"role": "user", "content": "靠什么赚钱"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "get_finance", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": json.dumps({
                "ok": True,
                "sources": [FIN.to_dict()],
                "company": "605499",
                "as_of": "2026-06-30",
                "data": {"revenue_yi": 124.43},
            }, ensure_ascii=False),
        },
        {"role": "assistant", "content": "饮料是主力[[finance:605499:2026-06-30]]。"},
    ]
    client = FakeClient([
        response(tool_calls=[tool_call("c2", "get_finance", {})]),
        response(content="还是饮料[[finance:605499:2026-06-30]]。"),
    ])

    def fail_run_tool(*args, **kwargs):
        raise AssertionError("命中回放缓存后不应重新取数")

    original = agent_mod.run_tool
    agent_mod.run_tool = fail_run_tool
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.restore_history(history)
        answer = research.ask("再讲讲")
    finally:
        agent_mod.run_tool = original

    assert answer.trace[0]["cached"] is True
    assert [source.id for source in answer.citations] == [FIN.id]
    tool_messages = [
        message for message in client.calls[1]["messages"]
        if message.get("role") == "tool"
    ]
    # 回放出来的那份被换成 stub，缓存命中的新结果重新拿到完整数据
    old = json.loads(tool_messages[0]["content"])
    assert old["superseded"] is True and "data" not in old, old
    reused = json.loads(tool_messages[-1]["content"])
    assert reused["cached"] is True
    assert reused["data"] == {"revenue_yi": 124.43}


def test_estimate_tokens() -> None:
    assert agent_mod.estimate_tokens("") == 0
    assert agent_mod.estimate_tokens("财报数据") > agent_mod.estimate_tokens("abcd")
    assert agent_mod.estimate_tokens("x" * 1000) == 301


def test_context_usage() -> None:
    research = agent_mod.ResearchAgent(FakeClient([]), default_company="605499")
    usage = research.context_usage()
    assert usage["tokens"] > 0
    assert usage["budget_tokens"] == agent_mod.CONTEXT_SOFT_TOKENS
    assert usage["hard_tokens"] == agent_mod.CONTEXT_HARD_TOKENS
    assert usage["level"] == "normal"
    assert 0 <= usage["ratio"] <= 1
    assert 0 < usage["percent"] < 80
    # 环的比例和百分比同源，不会一个超 100%、一个被截断
    assert abs(usage["percent"] - usage["tokens"] / usage["budget_tokens"] * 100) < 0.2
    # 流式草稿的 token 直接加在占用上，负数或 None 不能把占用拉低
    assert research.context_usage(1200)["tokens"] == usage["tokens"] + 1200
    assert research.context_usage(-500)["tokens"] == usage["tokens"]
    assert research.context_usage(None)["tokens"] == usage["tokens"]

    original_soft = agent_mod.CONTEXT_SOFT_TOKENS
    try:
        agent_mod.CONTEXT_SOFT_TOKENS = int(usage["tokens"] / 0.85)
        warn = research.context_usage()
        assert warn["level"] == "warn"
        assert 80 <= warn["percent"] < 100

        agent_mod.CONTEXT_SOFT_TOKENS = max(1, usage["tokens"] // 2)
        over = research.context_usage()
        assert over["level"] == "over"
        assert over["ratio"] == 1.0
        assert over["percent"] > 100
        # 分母为 0 的极端配置也不能除零
        agent_mod.CONTEXT_SOFT_TOKENS = 0
        assert research.context_usage()["level"] == "over"
    finally:
        agent_mod.CONTEXT_SOFT_TOKENS = original_soft


def _history_turn(index: int, data: dict | None = None) -> list[dict]:
    """一轮问答：user + tool_call + tool 结果 + 最终回答。"""
    call_id = f"c{index}"
    return [
        {"role": "user", "content": f"问题{index}"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "get_finance", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps({
                "ok": True,
                "sources": [FIN.to_dict()],
                "company": "605499",
                "as_of": "2026-06-30",
                "data": data if data is not None else {"payload": "x" * 5000},
            }, ensure_ascii=False),
        },
        {"role": "assistant", "content": f"回答{index}"},
    ]


def test_over_soft_deletes_old_tool_groups() -> None:
    """超过软阈值：整组删掉历史工具链，保留用户问题和最终回答。"""
    history = []
    for index in range(3):
        history.extend(_history_turn(index))

    client = FakeClient([response(content="新回答")])
    original_soft = agent_mod.CONTEXT_SOFT_TOKENS
    original_hard = agent_mod.CONTEXT_HARD_TOKENS
    agent_mod.CONTEXT_SOFT_TOKENS = 1
    agent_mod.CONTEXT_HARD_TOKENS = 10 ** 9
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.restore_history(history)
        research.ask("新问题")
    finally:
        agent_mod.CONTEXT_SOFT_TOKENS = original_soft
        agent_mod.CONTEXT_HARD_TOKENS = original_hard

    sent = client.calls[0]["messages"]
    call_ids = [
        call["id"]
        for message in sent
        for call in message.get("tool_calls") or []
    ]
    assert call_ids == [], call_ids                    # 工具链整组删光
    contents = [message.get("content") for message in sent]
    # 用户问题和最终回答一个不少
    assert "问题0" in contents and "回答0" in contents
    assert "问题1" in contents and "回答1" in contents
    assert "问题2" in contents and "回答2" in contents
    assert not [m for m in sent if m.get("role") == "tool"]


def test_over_hard_stubs_older_rounds_in_current_question() -> None:
    """单轮自己撑爆：stub 当前问题里较早的取数，最新一组和 skill 不动。"""
    client = FakeClient([
        response(tool_calls=[
            tool_call("s1", "load_skill", {"skill": "finance"}),
            tool_call("c1", "get_finance", {}),
        ]),
        response(tool_calls=[tool_call("c2", "get_segments", {})]),
        response(tool_calls=[tool_call("c3", "get_dividends", {})]),
        response(content="汇总回答"),
    ])

    def fake_run_tool(name, arguments, default_company=None):
        return ToolResult(
            ok=True, company="605499", as_of="2026-06-30", sources=(FIN,),
            data={"payload": name + "x" * 2000},
        )

    original = agent_mod.run_tool
    original_hard = agent_mod.CONTEXT_HARD_TOKENS
    agent_mod.run_tool = fake_run_tool
    agent_mod.CONTEXT_HARD_TOKENS = 10
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        answer = research.ask("基本面怎么样")
    finally:
        agent_mod.run_tool = original
        agent_mod.CONTEXT_HARD_TOKENS = original_hard

    assert answer.text == "汇总回答"
    sent = client.calls[-1]["messages"]
    tools = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in sent if message.get("role") == "tool"
    }
    # load_skill 是判据，轮内不裁；较早的取数结果换成 stub；最新一组保留
    assert "data" in tools["s1"] and tools["s1"].get("omitted") is not True
    assert tools["c1"]["omitted"] is True and "data" not in tools["c1"]
    assert tools["c2"]["omitted"] is True and "data" not in tools["c2"]
    assert "data" in tools["c3"]


def test_over_hard_summarizes_oldest_turns() -> None:
    """删完工具链仍超硬阈值：摘要最早一轮，并拼进 system prompt。"""
    history = []
    for index in range(3):
        history.extend(_history_turn(index))

    client = FakeClient([
        response(content="旧轮次摘要：东鹏饮料财报数据。"),
        response(content="新回答"),
    ])
    original_soft = agent_mod.CONTEXT_SOFT_TOKENS
    original_hard = agent_mod.CONTEXT_HARD_TOKENS
    agent_mod.CONTEXT_SOFT_TOKENS = 1
    agent_mod.CONTEXT_HARD_TOKENS = 1
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.restore_history(history)
        research.ask("新问题")
    finally:
        agent_mod.CONTEXT_SOFT_TOKENS = original_soft
        agent_mod.CONTEXT_HARD_TOKENS = original_hard

    assert research._summary == "旧轮次摘要：东鹏饮料财报数据。"
    summary_call = client.calls[0]
    assert "tools" not in summary_call
    assert summary_call["messages"][0]["content"] == agent_mod.SUMMARY_PROMPT
    assert "用户问：问题0" in summary_call["messages"][1]["content"]
    assert "回答0" in summary_call["messages"][1]["content"]

    sent = client.calls[1]["messages"]
    assert "旧轮次摘要" in sent[0]["content"]
    contents = [message.get("content") for message in sent]
    assert "问题0" not in contents and "回答0" not in contents
    # 最近两轮受保护
    assert "问题1" in contents and "问题2" in contents
    assert "新问题" in contents


class SummaryFailClient(FakeClient):
    """摘要调用抛错，其余调用正常。"""

    def _create(self, **kwargs):
        if "tools" not in kwargs:
            self.calls.append(kwargs)
            raise RuntimeError("摘要接口挂了")
        return super()._create(**kwargs)


def test_summary_failure_falls_back_to_omitting_turns() -> None:
    history = []
    for index in range(3):
        history.extend(_history_turn(index))

    client = SummaryFailClient([response(content="新回答")])
    original_soft = agent_mod.CONTEXT_SOFT_TOKENS
    original_hard = agent_mod.CONTEXT_HARD_TOKENS
    agent_mod.CONTEXT_SOFT_TOKENS = 1
    agent_mod.CONTEXT_HARD_TOKENS = 1
    try:
        research = agent_mod.ResearchAgent(client, default_company="605499")
        research.restore_history(history)
        answer = research.ask("新问题")
    finally:
        agent_mod.CONTEXT_SOFT_TOKENS = original_soft
        agent_mod.CONTEXT_HARD_TOKENS = original_hard

    assert answer.text == "新回答"
    assert answer.issues[0]["type"] == "context_summary_failed"
    contents = [message.get("content") for message in client.calls[-1]["messages"]]
    assert "问题0" not in contents and "回答0" not in contents
    assert "问题2" in contents


def test_memory_brief_injected() -> None:
    class FakeMemory:
        def __init__(self):
            self.calls = []

        def brief(self, code):
            self.calls.append(code)
            return "研究档案：605499 东鹏饮料"

    memory = FakeMemory()
    agent = agent_mod.ResearchAgent(FakeClient([]), default_company="605499", memory=memory)
    text = agent._system_text()
    assert "研究档案：605499 东鹏饮料" in text
    assert memory.calls == ["605499"]

    market = agent_mod.ResearchAgent(FakeClient([]), default_company="MARKET", memory=memory)
    market._system_text()
    assert memory.calls == ["605499"]  # 大盘不读档案


def test_memory_tools_uncached() -> None:
    for name in (
        "list_research_memory", "read_research_memory",
        "append_research_record", "update_research_record",
        "record_conclusion_change",
    ):
        assert name in agent_mod.UNCACHED_TOOLS


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
    test_memory_brief_injected()
    test_memory_tools_uncached()
    test_broad_question_loads_both_skills()
    test_use_company()
    test_tool_result_cache_reuses_same_call()
    test_tool_cache_skips_failed_results()
    test_realtime_tools_bypass_cache()
    test_restore_seeds_tool_cache()
    test_estimate_tokens()
    test_context_usage()
    test_over_soft_deletes_old_tool_groups()
    test_over_hard_stubs_older_rounds_in_current_question()
    test_over_hard_summarizes_oldest_turns()
    test_summary_failure_falls_back_to_omitting_turns()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
