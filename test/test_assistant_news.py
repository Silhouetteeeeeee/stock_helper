"""新闻事件工具验证：不需要公司、来源登记、失败是观察结果。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

NEWS = {
    "as_of": "2026-10-26 13:52:00",
    "events": [{
        "title": "国务院发布半导体产业规划",
        "category": "政策",
        "source": "东方财富",
        "source_id": "news:em:20261026100000",
        "published_at": "2026-10-26 10:00:00",
        "industries": [{"name": "半导体", "keywords": ["半导体"]}],
        "confidence": "高",
        "uncertainty": "政策细则未定",
        "impact_path": "政策 → 半导体/芯片需求",
    }],
    "overseas": [{
        "name": "标普500", "code": ".INX", "market": "美股",
        "date": "2026-10-23", "close": 7623.68, "chg_pct": -0.52,
    }],
    "feeds": [{
        "name": "东方财富", "detail": "财经快讯",
        "source_id": "news:em:20261026100000", "count": 1,
        "as_of": "2026-10-26",
    }],
    "counts": {"政策": 1},
    "errors": {},
    "notes": [],
}


def test_news_tool_does_not_need_company() -> None:
    calls: list[int] = []
    original = tools.build_news_events

    def fake(limit=12):
        calls.append(limit)
        return NEWS

    tools.build_news_events = fake
    try:
        # 即使会话默认公司是东鹏，新闻工具也不做公司解析
        result = tools.run_tool(
            "get_news_events", {"limit": 5}, default_company="605499"
        )
    finally:
        tools.build_news_events = original

    assert calls == [5], calls
    assert result.ok is True, result
    assert result.company is None
    assert result.as_of == "2026-10-26 13:52:00"
    assert result.data == NEWS
    assert [source.id for source in result.sources] == ["news:em:20261026100000"]
    assert result.sources[0].label == "财经快讯"
    assert result.sources[0].detail == "东方财富"
    assert "get_news_events" in UNCACHED_TOOLS


def test_news_tool_clamps_limit() -> None:
    calls: list[int] = []
    original = tools.build_news_events

    def fake(limit=12):
        calls.append(limit)
        return NEWS

    tools.build_news_events = fake
    try:
        tools.run_tool("get_news_events", {"limit": 999})
        tools.run_tool("get_news_events", {"limit": "bad"})
    finally:
        tools.build_news_events = original

    assert calls == [20, 12], calls


def test_news_tool_failure_is_observation() -> None:
    def broken(limit=12):
        raise RuntimeError("接口超时")

    original = tools.build_news_events
    tools.build_news_events = broken
    try:
        result = tools.run_tool("get_news_events", {})
    finally:
        tools.build_news_events = original

    assert result.ok is False
    assert "新闻事件获取失败" in result.error
    assert "接口超时" in result.error


def test_news_tool_without_data() -> None:
    empty = {
        "as_of": None,
        "events": [],
        "overseas": [],
        "feeds": [],
        "counts": {},
        "errors": {"cls": "连不上", "em": "连不上"},
    }
    original = tools.build_news_events
    tools.build_news_events = lambda limit=12: empty
    try:
        result = tools.run_tool("get_news_events", {})
    finally:
        tools.build_news_events = original

    assert result.ok is False
    assert "cls：连不上" in result.error
    assert "em：连不上" in result.error


def test_news_intent_and_schema() -> None:
    assert tools.is_market_question("今天有什么政策新闻")
    assert tools.is_market_question("美联储降息对哪些行业有影响")
    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "get_news_events"
    )
    assert schema["function"]["parameters"]["properties"]["limit"]["maximum"] == 20


def main() -> None:
    test_news_tool_does_not_need_company()
    test_news_tool_clamps_limit()
    test_news_tool_failure_is_observation()
    test_news_tool_without_data()
    test_news_intent_and_schema()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
