"""复盘工具验证：存档成功/失败、复盘样本来源与空存档提示。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.tools as tools
from assistant.agent import UNCACHED_TOOLS

SNAPSHOT = {
    "trade_date": "2026-10-23",
    "as_of": "2026-10-23 15:10:00",
    "market": {"state": "震荡", "risk_level": "中"},
    "sectors": {"leaders": [{"name": "半导体", "score": 7.2}]},
    "candidates": [{"code": "sh600519", "name": "贵州茅台", "score": 7.5}],
    "strategy": None,
    "errors": {},
}

REVIEW = {
    "as_of": "2026-10-26 10:00:00",
    "horizon": 5,
    "benchmark": "沪深300",
    "reports": [
        {
            "trade_date": "2026-10-23",
            "saved_at": "2026-10-23 15:10:00",
            "market": {"state": "震荡", "status": "待观察", "hit": None},
            "sectors": [],
            "candidates": [{"code": "sh600519", "name": "贵州茅台",
                            "status": "待观察", "hit": None}],
        },
    ],
    "summary": {"reports": 1, "evaluated": 0},
    "errors": {},
    "notes": [],
    "missing": [],
}


def test_save_judgment_tool() -> None:
    calls: list = []
    original = tools.save_judgment

    def fake(strategy=None):
        calls.append(strategy)
        return {**SNAPSHOT, "saved": True,
                "store": {"trade_date": "2026-10-23",
                          "saved_at": "2026-10-23 15:10:05",
                          "replaced": False}}

    tools.save_judgment = fake
    try:
        result = tools.run_tool("save_daily_judgment", {
            "strategy": {"stance": "观望", "total_pct": 60},
        })
    finally:
        tools.save_judgment = original

    assert result.ok is True, result
    assert result.company is None
    assert calls == [{"stance": "观望", "total_pct": 60}]
    assert [source.id for source in result.sources] == ["research:2026-10-23"]
    assert "震荡" in result.sources[0].detail
    assert "新增" in result.sources[0].detail
    assert result.data["trade_date"] == "2026-10-23"
    assert "save_daily_judgment" in UNCACHED_TOOLS


def test_save_judgment_failure_and_empty() -> None:
    original = tools.save_judgment
    tools.save_judgment = lambda strategy=None: {
        **SNAPSHOT, "saved": False, "store": None,
        "errors": {"market": "大盘状态：接口超时"},
    }
    try:
        result = tools.run_tool("save_daily_judgment", {})
    finally:
        tools.save_judgment = original

    assert result.ok is False
    assert "判断存档失败" in result.error
    assert "接口超时" in result.error
    assert result.data["saved"] is False


def test_save_judgment_raises() -> None:
    original = tools.save_judgment

    def broken(strategy=None):
        raise RuntimeError("数据库不可写")

    tools.save_judgment = broken
    try:
        result = tools.run_tool("save_daily_judgment", {})
    finally:
        tools.save_judgment = original

    assert result.ok is False
    assert "判断存档失败" in result.error
    assert "数据库不可写" in result.error


def test_review_reports_tool() -> None:
    seen: list[tuple] = []
    original = tools.review_reports

    def fake(limit=20, horizon=5):
        seen.append((limit, horizon))
        return dict(REVIEW)

    tools.review_reports = fake
    try:
        result = tools.run_tool("review_research_reports", {
            "horizon": 10, "limit": 5,
        })
    finally:
        tools.review_reports = original

    assert result.ok is True, result
    assert result.company is None
    assert seen == [(5, 10)]
    ids = [source.id for source in result.sources]
    assert "review:2026-10-23" in ids
    assert any(item.startswith("review:benchmark:") for item in ids)
    assert result.data["summary"]["reports"] == 1
    assert "review_research_reports" in UNCACHED_TOOLS


def test_review_reports_clamps_and_empty() -> None:
    original = tools.review_reports
    seen: list[tuple] = []

    def fake(limit=20, horizon=5):
        seen.append((limit, horizon))
        return {**REVIEW, "reports": [],
                "notes": ["还没有保存过每日判断；先调用 save_daily_judgment 存档"]}

    tools.review_reports = fake
    try:
        result = tools.run_tool("review_research_reports", {
            "horizon": 99, "limit": -5,
        })
    finally:
        tools.review_reports = original

    assert result.ok is False
    assert "复盘失败" in result.error
    assert "还没有保存过每日判断" in result.error
    assert seen == [(1, 20)]


def test_review_reports_raises() -> None:
    original = tools.review_reports

    def broken(limit=20, horizon=5):
        raise RuntimeError("存档库损坏")

    tools.review_reports = broken
    try:
        result = tools.run_tool("review_research_reports", {})
    finally:
        tools.review_reports = original

    assert result.ok is False
    assert "复盘失败" in result.error
    assert "存档库损坏" in result.error


def test_review_schemas() -> None:
    names = [item["function"]["name"] for item in tools.schemas()]
    assert "save_daily_judgment" in names
    assert "review_research_reports" in names
    schema = next(
        item for item in tools.schemas()
        if item["function"]["name"] == "review_research_reports"
    )
    properties = schema["function"]["parameters"]["properties"]
    assert properties["horizon"]["minimum"] == 1
    assert properties["horizon"]["maximum"] == 20
    assert properties["limit"]["maximum"] == 60
    # 复盘类问法不带公司名，也要能路由到大盘会话
    assert tools.is_market_question("复盘一下最近几天的判断")
    assert tools.is_market_question("上次判断准不准")
    assert tools.is_market_question("命中率怎么样")
    assert tools.is_market_question("把今天的判断记下来")
    # 指代词说明问的是当前聊的那家公司，不该被大盘关键词带走
    assert tools.refers_to_company("它的行业轮动怎么样")
    assert not tools.refers_to_company("今天大盘怎么看")


def main() -> None:
    test_save_judgment_tool()
    test_save_judgment_failure_and_empty()
    test_save_judgment_raises()
    test_review_reports_tool()
    test_review_reports_clamps_and_empty()
    test_review_reports_raises()
    test_review_schemas()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
