"""基本面快照验证：指标汇总、趋势截取、异常提示、文本渲染。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.stock import Stock

import stock.finance_snapshot as snap

E8 = 1e8


def row(report_date: str, **fields) -> dict:
    base = {
        "code": "605499",
        "report_date": report_date,
        "ann_date": None,
        "audited": None,
        "fetched_at": "2026-10-24 15:00:00",
        # 非金融企业的标志（金融机构没有营业成本），银行类用例显式传 None
        "cost_of_revenue": 1.0,
    }
    base.update(fields)
    return base


def codes(snapshot: dict) -> list[str]:
    return [flag["code"] for flag in snapshot["flags"]]


# 7 期完整数据：应收暴涨、毛利率下滑、现金流连续两期偏弱、
# 资产负债率跳升、货币资金盖不住短期借款、单季增速连续放缓
HISTORY = [
    row("2024-12-31", revenue=30 * E8, revenue_yoy=10, revenue_sq=8 * E8,
        revenue_sq_yoy=10, net_profit=3 * E8, net_profit_yoy=5,
        net_profit_sq=0.8 * E8, net_profit_sq_yoy=8, gross_margin=45,
        net_margin=10, ocf_to_net_profit=1.2, debt_ratio=32),
    row("2025-03-31", revenue=10 * E8, revenue_yoy=20, revenue_sq=10 * E8,
        revenue_sq_yoy=20, net_profit=1 * E8, net_profit_yoy=22,
        net_profit_sq=1 * E8, net_profit_sq_yoy=22, gross_margin=46,
        ocf_to_net_profit=0.9),
    row("2025-06-30", revenue=20 * E8, revenue_yoy=25, revenue_sq=10 * E8,
        revenue_sq_yoy=30, net_profit=2 * E8, net_profit_yoy=28,
        net_profit_sq=1 * E8, net_profit_sq_yoy=32, gross_margin=48,
        net_margin=10, debt_ratio=30, accounts_receivable=4 * E8,
        inventory=5.5 * E8, ocf_to_net_profit=0.8),
    row("2025-09-30", revenue=31 * E8, revenue_yoy=30, revenue_sq=11 * E8,
        revenue_sq_yoy=40, net_profit=3.1 * E8, net_profit_yoy=32,
        net_profit_sq=1.1 * E8, net_profit_sq_yoy=40, gross_margin=47,
        ocf_to_net_profit=0.7),
    row("2025-12-31", revenue=43 * E8, revenue_yoy=35, revenue_sq=12 * E8,
        revenue_sq_yoy=35, net_profit=4.3 * E8, net_profit_yoy=38,
        net_profit_sq=1.2 * E8, net_profit_sq_yoy=35, gross_margin=46,
        ocf_to_net_profit=0.6, debt_ratio=35),
    row("2026-03-31", revenue=13 * E8, revenue_yoy=28, revenue_sq=13 * E8,
        revenue_sq_yoy=28, net_profit=1.3 * E8, net_profit_yoy=30,
        net_profit_sq=1.3 * E8, net_profit_sq_yoy=28, gross_margin=42,
        ocf_to_net_profit=0.2),
    row("2026-06-30", revenue=26 * E8, revenue_yoy=30, revenue_sq=13 * E8,
        revenue_sq_yoy=25, net_profit=2.6 * E8, net_profit_yoy=30,
        net_profit_sq=1.3 * E8, net_profit_sq_yoy=25, gross_margin=40,
        net_margin=10, roe=25, roa=12, debt_ratio=45, current_ratio=1.8,
        operating_cash_flow=1.5 * E8, capex=0.5 * E8, free_cash_flow=1.0 * E8,
        ocf_to_net_profit=0.35, net_debt=1.5 * E8, cash=2 * E8,
        short_term_borrowings=3 * E8, accounts_receivable=8 * E8,
        inventory=6 * E8, ann_date="2026-08-26"),
]


def two_periods(latest_fields: dict) -> list[dict]:
    """两期数据：只改最新一期的关键字段，用于单独验证某条规则。"""
    base = row("2025-06-30", revenue=10 * E8, net_profit=1 * E8, gross_margin=30)
    latest = row("2026-06-30", revenue=10.5 * E8, net_profit=1 * E8, gross_margin=30)
    latest.update(latest_fields)
    return [base, latest]


def test_safety_metrics() -> None:
    """财务安全：有息负债、利息保障、商誉占比、应收/存货同比。"""
    latest = row(
        "2026-06-30", revenue=100 * E8, revenue_yoy=20,
        total_profit=12 * E8, interest_expense=2 * E8,
        short_term_borrowings=5 * E8, current_portion_lt_debt=1 * E8,
        long_term_borrowings=8 * E8, bonds_payable=2 * E8,
        lease_liabilities=0.5 * E8, cash=6 * E8,
        goodwill=10 * E8, total_equity=50 * E8,
        accounts_receivable=15 * E8, inventory=9 * E8,
    )
    last_year = row("2025-06-30", accounts_receivable=10 * E8, inventory=10 * E8)

    safety = snap.snapshot_from_rows(
        [last_year, latest], name="测试股份"
    )["metrics"]["safety"]
    assert safety["interest_debt"] == 16.5 * E8, safety
    assert safety["short_term_debt"] == 6 * E8, safety
    assert safety["interest_coverage"] == 7.0, safety   # (12+2)/2
    assert safety["interest_coverage_basis"] == "利息费用", safety
    assert safety["goodwill"] == 10 * E8, safety
    assert safety["goodwill_ratio"] == 20.0, safety
    assert safety["accounts_receivable_yoy"] == 50.0, safety
    assert safety["inventory_yoy"] == -10.0, safety
    assert safety["cash_to_short_term_debt"] == 1.0, safety

    text = snap.format_snapshot(snap.snapshot_from_rows([last_year, latest]))
    assert "有息负债 16.5亿" in text, text
    assert "利息保障 7.0倍" in text, text
    assert "商誉 10.0亿（占净资产 20.0%）" in text, text

    # 没有利息费用科目时用财务费用近似，并标明口径
    fallback = snap.snapshot_from_rows([
        row("2025-06-30", revenue=100 * E8),
        row("2026-06-30", revenue=100 * E8, total_profit=9 * E8,
            finance_expense=1 * E8),
    ])["metrics"]["safety"]
    assert fallback["interest_coverage"] == 10.0, fallback
    assert "财务费用" in fallback["interest_coverage_basis"], fallback

    # 利息费用为 0 或为负（净利息收入）时算不出覆盖倍数
    for interest in (0, -1 * E8):
        zero = snap.snapshot_from_rows([
            row("2026-06-30", revenue=100 * E8, total_profit=9 * E8,
                interest_expense=interest),
        ])["metrics"]["safety"]
        assert zero["interest_coverage"] is None, zero

    # 银行没有可比的有息负债与利息保障口径，整组留空
    bank = snap.snapshot_from_rows([
        row("2026-06-30", revenue=100 * E8, cost_of_revenue=None,
            total_profit=40 * E8, interest_expense=30 * E8,
            short_term_borrowings=500 * E8, goodwill=10 * E8),
    ])["metrics"]["safety"]
    assert all(value is None for value in bank.values()), bank
    assert "有息负债" not in snap.format_snapshot(snap.snapshot_from_rows([
        row("2026-06-30", revenue=100 * E8, cost_of_revenue=None),
    ]))


def main() -> None:
    test_safety_metrics()

    snapshot = snap.snapshot_from_rows(HISTORY, name="东鹏饮料")
    assert snapshot["available"] is True
    assert snapshot["code"] == "605499"
    assert snapshot["periods"] == 7
    assert snapshot["latest"]["report_date"] == "2026-06-30"
    assert snapshot["latest"]["ann_date"] == "2026-08-26"

    metrics = snapshot["metrics"]
    assert metrics["revenue"]["value"] == 26 * E8
    assert metrics["revenue"]["yoy"] == 30
    assert metrics["revenue"]["single_quarter"] == 13 * E8
    assert metrics["gross_margin"]["value"] == 40
    assert metrics["gross_margin"]["change_pp"] == -8          # 40 vs 去年同期 48
    assert metrics["debt_ratio"]["change_pp"] == 15            # 45 vs 去年同期 30
    assert metrics["roe"]["value"] == 25
    assert metrics["cash"]["ocf_to_net_profit"] == 0.35

    # 趋势默认取最近 8 期，数据只有 7 期时全给
    assert len(snapshot["trend"]) == 7
    assert snapshot["trend"][-1]["report_date"] == "2026-06-30"
    assert len(snap.snapshot_from_rows(HISTORY, periods=3)["trend"]) == 3

    found = codes(snapshot)
    for expected in (
        "receivables_outpace_revenue",     # 应收 +100% vs 营收 +30%
        "gross_margin_drop",               # 毛利率 -8pp
        "weak_operating_cash_flow",        # 连续两期低于 50%
        "debt_ratio_rise",                 # 资产负债率 +15pp
        "cash_short_of_short_term_debt",   # 2亿现金 < 3亿短期借款
        "revenue_momentum",                # 单季增速连续 3 个季度放缓
    ):
        assert expected in found, (expected, found)

    # 不该误报的：存货只涨 9%，净利润增速没超过营收
    for unexpected in ("inventory_outpace_revenue", "profit_outpacing_revenue",
                       "revenue_decline", "net_cash_position"):
        assert unexpected not in found, (unexpected, found)

    momentum = next(flag for flag in snapshot["flags"] if flag["code"] == "revenue_momentum")
    assert momentum["detail"]["values"] == [40, 35, 28, 25], momentum
    assert "连续 3 个季度放缓" in momentum["message"], momentum["message"]

    # -- 文本渲染 -----------------------------------------------------------
    text = snap.format_snapshot(snapshot)
    assert "东鹏饮料(605499)" in text, text
    assert "最新报告期 2026-06-30（公告 2026-08-26）" in text, text
    assert "营收 26.0亿" in text, text
    assert "毛利率 40.0%（-8.0pp）" in text, text
    assert "近 7 个季度单季营收（亿）" in text, text
    assert "[异常] 应收账款同比 +100.0%" in text, text
    assert "数据来源：新浪财经" in text, text

    # -- 单条规则 -----------------------------------------------------------
    falling = snap.snapshot_from_rows(two_periods({
        "revenue_yoy": 5, "net_profit_yoy": -20,
    }))
    assert "profit_falling_revenue_rising" in codes(falling), codes(falling)

    outpacing = snap.snapshot_from_rows(two_periods({
        "revenue_yoy": 10, "net_profit_yoy": 40,
    }))
    assert "profit_outpacing_revenue" in codes(outpacing), codes(outpacing)

    declining = snap.snapshot_from_rows(two_periods({"revenue_yoy": -15}))
    assert "revenue_decline" in codes(declining), codes(declining)

    net_cash = snap.snapshot_from_rows(two_periods({"revenue_yoy": 8, "net_debt": -1 * E8}))
    assert "net_cash_position" in codes(net_cash), codes(net_cash)
    assert "净现金状态" in snap.format_snapshot(net_cash)

    # -- 银行类报表：缺毛利率、应收、现金科目也不能崩 -------------------------
    bank = snap.snapshot_from_rows([
        row("2025-12-31", revenue=100 * E8, net_profit=40 * E8, revenue_yoy=5,
            net_profit_yoy=6, debt_ratio=92, roe=15, net_debt=-120 * E8,
            cost_of_revenue=None),
    ], name="招商银行")
    assert codes(bank) == [], codes(bank)
    bank_text = snap.format_snapshot(bank)
    assert "毛利率" not in bank_text, bank_text
    assert "净负债" not in bank_text, "金融机构不该展示净负债"
    assert "为净利润的" not in bank_text, "银行的经营现金流与净利润不可比"
    assert "资产负债率 92.0%" in bank_text, bank_text

    # -- 空数据 -------------------------------------------------------------
    empty = snap.snapshot_from_rows([], name="测试股份")
    assert empty["available"] is False
    assert empty["summary"] == "测试股份：暂无财报数据"

    # -- build_snapshot 走缓存层 --------------------------------------------
    snap.ensure_financials = lambda code, **kwargs: HISTORY  # type: ignore[assignment]
    snap.get_stock = lambda code: Stock(code=code, name="东鹏饮料")  # type: ignore[assignment]
    built = snap.build_snapshot("SH605499")
    assert built["name"] == "东鹏饮料"
    assert built["metrics"]["revenue"]["value"] == 26 * E8
    assert built["summary"] == snap.format_snapshot(built)

    try:
        snap.build_snapshot("")
    except snap.FinanceError:
        pass
    else:
        raise AssertionError("空代码没有报错")

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
