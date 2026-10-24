"""一页速览验证：指标行、标题、降级、提示合并与纯文本渲染。

全部用构造好的快照/估值字典，不联网、不碰 Qt。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.briefing as br
import stock.valuation as val
from stock.finance_snapshot import FinanceError

CODE = "605499"
NAME = "东鹏饮料"
E8 = 1e8


def make_snapshot(
    gross_margin: float | None = 48.4,
    gross_change: float | None = 3.2,
    roe: float | None = 40.0,
    ocf: float | None = 24.9 * E8,
    ocf_ratio: float | None = 0.87,
    flags: list[dict] | None = None,
) -> dict:
    return {
        "available": True,
        "code": CODE,
        "name": NAME,
        "latest": {"report_date": "2026-06-30"},
        "metrics": {
            "revenue": {"value": 124.4 * E8, "yoy": 15.9},
            "net_profit": {"value": 28.7 * E8, "yoy": 20.7},
            "gross_margin": {"value": gross_margin, "change_pp": gross_change},
            "roe": {"value": roe},
            "cash": {"operating_cash_flow": ocf, "ocf_to_net_profit": ocf_ratio},
        },
        "flags": flags or [],
        "sources": {"财报": "测试源"},
    }


def make_valuation(
    pe: float | None = 15.63,
    pb: float | None = 3.94,
    pe_pct: float | None = 0.0,
    pb_pct: float | None = 34.0,
    median_pe: float | None = 24.0,
    rank: int = 4,
    total: int = 21,
    flags: list[dict] | None = None,
) -> dict:
    return {
        "available": True,
        "code": CODE,
        "name": NAME,
        "as_of": "2026-10-23",
        "valuation": {
            "pe_ttm": {"value": pe, "years": 5, "samples": 1200, "percentile": pe_pct},
            "pb_mrq": {"value": pb, "years": 5, "samples": 1200, "percentile": pb_pct},
        },
        "industry": {
            "board": "饮料乳品",
            "count": 28,
            "median_pe": median_pe,
            "median_pb": 2.1,
            "rank_pe": rank,
            "rank_total": total,
            "peers": [],
        },
        "flags": flags or [],
        "sources": {"估值": "测试源"},
    }


def make_segments(flags: list[dict] | None = None, available: bool = True) -> dict:
    items = [
        {"name": "能量饮料", "income": 89.37 * E8, "share": 71.8, "gross_margin": 54.7,
         "yoy": 6.9, "share_delta": -6.1, "is_other": False},
        {"name": "电解质饮料", "income": 16.72 * E8, "share": 13.4, "gross_margin": 40.6,
         "yoy": 12.2, "share_delta": 0.1, "is_other": False},
        {"name": "茶饮料", "income": 10.58 * E8, "share": 8.5, "gross_margin": 23.7,
         "yoy": None, "share_delta": None, "is_other": False},
        {"name": "其他(补充)", "income": 0.17 * E8, "share": 0.1, "gross_margin": 33.4,
         "yoy": 187.5, "share_delta": 0.1, "is_other": True},
    ]
    return {
        "available": available,
        "code": CODE,
        "name": NAME,
        "as_of": "2026-06-30",
        "kind": "2",
        "kind_label": "产品",
        "total_income": 124.43 * E8,
        "previous_date": "2025-06-30",
        "count": len(items),
        "items": items,
        "flags": flags or [],
        "stale": False,
        "sources": {"主营": "测试源"},
        "summary": "主营构成测试",
    }


def text_of(briefing: dict) -> str:
    return "\n".join(briefing["lines"])


def main() -> None:
    # -- 财报与估值都齐：五行指标，顺序固定 ----------------------------------
    snapshot = make_snapshot()
    valuation = make_valuation()
    briefing = br.briefing_from(snapshot, valuation)

    assert briefing["available"] is True, briefing
    assert briefing["code"] == CODE and briefing["name"] == NAME, briefing
    assert briefing["headline"] == "财报 2026-06-30 · 估值 2026-10-23", briefing["headline"]
    assert briefing["lines"] == [
        "营收 124.4亿 +15.9% · 净利 28.7亿 +20.7%",
        "毛利率 48.4%（+3.2pp） · ROE 40.0%",
        "经营现金流 24.9亿 · 为净利的 87%",
        "PE 15.63 · PB 3.94（近 5 年 0%/34% 分位）",
        "饮料乳品 28 家 · PE 中值 24.0 · 第 4/21",
    ], briefing["lines"]
    assert len(briefing["lines"]) <= br.MAX_LINES

    rendered = briefing["summary"]
    assert rendered.startswith("东鹏饮料(605499) · 财报 2026-06-30"), rendered
    assert "PE 15.63" in rendered, rendered

    # -- 银行没有毛利率：不能留下"毛利率 —"这种占位 --------------------------
    bank = br.briefing_from(make_snapshot(gross_margin=None, gross_change=None, roe=11.8), None)
    assert bank["lines"][1] == "ROE 11.8%", bank["lines"]
    assert "—" not in text_of(bank), bank["lines"]

    # 现金流也缺失时整行省略
    bare = br.briefing_from(
        make_snapshot(gross_margin=None, roe=None, ocf=None, ocf_ratio=None), None
    )
    assert bare["lines"] == ["营收 124.4亿 +15.9% · 净利 28.7亿 +20.7%"], bare["lines"]

    # -- 亏损公司：PE 写"亏损"，只有 PB 有分位 -----------------------------
    loss = br.briefing_from(
        make_snapshot(), make_valuation(pe=-5.0, pe_pct=None, pb_pct=34.0)
    )
    assert loss["lines"][3] == "PE 亏损 · PB 3.94（近 5 年 PB 34% 分位）", loss["lines"]
    both_missing = br.briefing_from(
        None, make_valuation(pe_pct=None, pb_pct=None)
    )
    assert both_missing["lines"][0].startswith("PE 15.63 · PB 3.94")
    assert "分位" not in both_missing["lines"][0], both_missing["lines"]

    # 没有同业数据时不出行业行
    no_peers = br.briefing_from(None, make_valuation(median_pe=None))
    assert len(no_peers["lines"]) == 1, no_peers["lines"]

    # -- 只有财报 / 只有估值 ------------------------------------------------
    only_finance = br.briefing_from(snapshot, None)
    assert only_finance["available"] is True
    assert only_finance["headline"] == "财报 2026-06-30", only_finance["headline"]
    assert not any(line.startswith("PE") for line in only_finance["lines"])

    only_valuation = br.briefing_from(None, valuation)
    assert only_valuation["name"] == NAME and only_valuation["code"] == CODE
    assert only_valuation["headline"] == "估值 2026-10-23", only_valuation["headline"]
    assert len(only_valuation["lines"]) == 2, only_valuation["lines"]

    # -- 主营构成：插在营收之后，明细进纯文本 ------------------------------
    segments = make_segments()
    with_segments = br.briefing_from(make_snapshot(), make_valuation(), segments)
    assert with_segments["lines"][1] == "主营 能量饮料 72% · 电解质饮料 13%", with_segments["lines"]
    assert len(with_segments["lines"]) == 6, with_segments["lines"]
    assert len(with_segments["lines"]) <= br.MAX_LINES
    assert with_segments["segments"] is segments, with_segments
    assert with_segments["sources"]["主营"] == "测试源", with_segments["sources"]

    rendered_segments = with_segments["summary"]
    assert "主营构成 2026-06-30（按产品）：" in rendered_segments, rendered_segments
    assert "能量饮料 89.4亿，占比 71.8%（同比 +6.9%），毛利率 54.7%" in rendered_segments
    # 汇总项不上标题行
    assert "其他(补充)" not in with_segments["lines"][1], with_segments["lines"][1]

    # 不可用时不占行，也不写进结构
    no_seg = br.briefing_from(make_snapshot(), None, make_segments(available=False))
    assert not any(line.startswith("主营 ") for line in no_seg["lines"]), no_seg["lines"]
    assert no_seg["segments"] is None, no_seg

    # 分部提示排在财报提示之后、估值提示之前
    ordered = br.briefing_from(
        make_snapshot(flags=[{"level": "info", "message": "现金流转弱"}]),
        make_valuation(flags=[{"level": "warning", "message": "PE 高位"}]),
        make_segments(flags=[{"level": "warning", "message": "主业收入下滑"}]),
    )
    assert [alert["text"] for alert in ordered["alerts"]] == [
        "现金流转弱",
        "主业收入下滑",
        "PE 高位",
    ], ordered["alerts"]
    assert "[异常] 主业收入下滑" in ordered["summary"], ordered["summary"]

    # 分部接口失败：只多一条提示，其他照常
    seg_error = br.briefing_from(
        make_snapshot(), None, None, valuation_error="估值超时", segments_error="主营超时"
    )
    assert [alert["text"] for alert in seg_error["alerts"]] == [
        "主营构成暂不可用",
        "估值数据暂不可用",
    ], seg_error["alerts"]
    assert "主营 能量饮料" not in text_of(seg_error), text_of(seg_error)

    # -- 两份摘要的提示合并，纯文本里带级别 ---------------------------------
    snapshot["flags"] = [
        {"level": "warning", "code": "revenue_flat", "message": "单季营收连续两期放缓"},
        {"level": "info", "code": "cash_weak", "message": "现金流偏弱"},
    ]
    valuation["flags"] = [{"level": "warning", "code": "pe_high", "message": "PE 处于高位"}]
    merged = br.briefing_from(snapshot, valuation)
    assert [alert["text"] for alert in merged["alerts"]] == [
        "单季营收连续两期放缓",
        "现金流偏弱",
        "PE 处于高位",
    ], merged["alerts"]
    assert "[异常] 单季营收连续两期放缓" in merged["summary"], merged["summary"]
    assert "[关注] 现金流偏弱" in merged["summary"], merged["summary"]
    assert "提示：" in merged["summary"], merged["summary"]

    # -- 什么都没有 ---------------------------------------------------------
    empty = br.briefing_from(None, None)
    assert empty["available"] is False, empty
    assert empty["lines"] == [] and empty["alerts"] == []
    assert "暂无财报、主营与估值数据" in empty["summary"], empty
    assert "暂无财报、主营与估值数据" in br.format_briefing(empty)

    # -- 估值接口挂了：财报照常展示，只多一条提示 --------------------------
    snapshot["flags"] = []
    degraded = br.briefing_from(snapshot, None, valuation_error="估值接口超时")
    assert degraded["available"] is True
    assert degraded["alerts"] == [{"level": "info", "text": "估值数据暂不可用"}], degraded
    assert "财报 2026-06-30" in degraded["headline"]

    # -- build_briefing：取数失败时降级，空代码报错 -------------------------
    calls = {"snapshot": 0}

    def fake_snapshot(code, **kwargs):
        calls["snapshot"] += 1
        return make_snapshot()

    def broken_valuation(*args, **kwargs):
        raise val.ValuationError("估值接口超时")

    def fake_segments(code, **kwargs):
        calls["segments"] = calls.get("segments", 0) + 1
        return make_segments()

    br.build_snapshot = fake_snapshot  # type: ignore[assignment]
    br.build_valuation = broken_valuation  # type: ignore[assignment]
    br.build_segments = fake_segments  # type: ignore[assignment]

    result = br.build_briefing(CODE)
    assert calls["snapshot"] == 1, calls
    assert calls["segments"] == 1, calls
    assert result["available"] is True
    assert "PE 15.63" not in text_of(result), text_of(result)
    assert result["alerts"][-1]["text"] == "估值数据暂不可用", result["alerts"]
    assert result["lines"][1] == "主营 能量饮料 72% · 电解质饮料 13%", result["lines"]

    # 主营接口挂了同样只降级自己
    def broken_segments(*args, **kwargs):
        raise br.SegmentError("主营接口超时")

    br.build_segments = broken_segments  # type: ignore[assignment]
    degraded_segments = br.build_briefing(CODE)
    assert degraded_segments["alerts"][0]["text"] == "主营构成暂不可用", degraded_segments
    assert not any(line.startswith("主营 ") for line in degraded_segments["lines"])

    try:
        br.build_briefing("")
    except FinanceError:
        pass
    else:
        raise AssertionError("空代码应该报 FinanceError")

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
