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
    fallback_snapshot = snap.snapshot_from_rows([
        row("2025-06-30", revenue=100 * E8),
        row("2026-06-30", revenue=100 * E8, total_profit=9 * E8,
            finance_expense=1 * E8),
    ])
    fallback = fallback_snapshot["metrics"]["safety"]
    assert fallback["interest_coverage"] == 10.0, fallback
    assert "财务费用" in fallback["interest_coverage_basis"], fallback
    # 摘要里也要标明是近似口径，不能和利息费用口径混在一起
    assert "财务费用近似" in snap.format_snapshot(fallback_snapshot)

    # 利息费用为 0 或为负（净利息收入）时算不出覆盖倍数
    for interest in (0, -1 * E8):
        zero = snap.snapshot_from_rows([
            row("2026-06-30", revenue=100 * E8, total_profit=9 * E8,
                interest_expense=interest),
        ])["metrics"]["safety"]
        assert zero["interest_coverage"] is None, zero

    # 所有债务科目都缺失时是"未知"，不能用 0 冒充"没有负债"
    bare = snap.snapshot_from_rows([
        row("2026-06-30", revenue=100 * E8, total_profit=10 * E8,
            interest_expense=1 * E8, total_equity=50 * E8),
    ])["metrics"]["safety"]
    assert bare["interest_debt"] is None, bare
    assert bare["short_term_debt"] is None, bare
    assert bare["cash_to_short_term_debt"] is None, bare

    # 净资产为 0 或负数时商誉占比没有意义
    for equity in (0, -10 * E8):
        broken = snap.snapshot_from_rows([
            row("2026-06-30", revenue=100 * E8, goodwill=10 * E8,
                total_equity=equity),
        ])["metrics"]["safety"]
        assert broken["goodwill_ratio"] is None, broken

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

    # 银行的经营现金流由存款变动驱动，连续两期偏低也不能报"现金流异常"
    bank_history = snap.snapshot_from_rows([
        row("2025-06-30", revenue=100 * E8, cost_of_revenue=None,
            ocf_to_net_profit=0.3),
        row("2026-06-30", revenue=110 * E8, cost_of_revenue=None,
            ocf_to_net_profit=0.3),
    ], name="招商银行")
    assert "weak_operating_cash_flow" not in codes(bank_history), codes(bank_history)


def test_detail_fields() -> None:
    """报表明细：每股收益、权益、投入资本与营运资金科目。"""
    latest = row(
        "2026-06-30", revenue=100 * E8,
        basic_eps=2.15, diluted_eps=2.12,
        equity_parent=80 * E8, minority_interest=2 * E8,
        total_equity=82 * E8, total_assets=200 * E8,
        total_liabilities=118 * E8,
        fixed_assets=30 * E8, construction_in_progress=10 * E8,
        intangible_assets=5 * E8, long_term_equity_invest=3 * E8,
        notes_payable=2 * E8, accounts_payable=12 * E8,
        advances_from_customers=1 * E8, contract_liabilities=4 * E8,
        notes_receivable=6 * E8, prepayments=2 * E8,
        other_receivables=1 * E8, development_expenditure=1.5 * E8,
        selling_expense=10 * E8, admin_expense=4 * E8,
        finance_expense=1 * E8, operating_profit=20 * E8,
        total_profit=21 * E8, income_tax=4 * E8,
        net_profit_attributable=16 * E8, minority_profit=1 * E8,
        cash_from_sales=95 * E8, operating_cash_inflow=100 * E8,
        operating_cash_outflow=80 * E8, investment_cash_flow=-10 * E8,
        financing_cash_flow=5 * E8, ending_cash=20 * E8,
        paid_in_capital=10 * E8, capital_reserve=30 * E8,
        investment_income=-0.5 * E8, fair_value_change=0.1 * E8,
        asset_impairment=-0.3 * E8, credit_impairment=-0.2 * E8,
        treasury_stock=2 * E8, rd_expense=4 * E8, capex=8 * E8,
    )
    last_year = row(
        "2025-06-30", fixed_assets=20 * E8, construction_in_progress=5 * E8,
        notes_receivable=3 * E8, prepayments=2.5 * E8,
        other_receivables=0.5 * E8, accounts_payable=8 * E8,
        contract_liabilities=2 * E8, development_expenditure=1 * E8,
    )
    detail = snap.snapshot_from_rows(
        [last_year, latest], name="测试股份"
    )["metrics"]["detail"]

    assert detail["basic_eps"] == 2.15, detail
    assert detail["diluted_eps"] == 2.12, detail
    assert detail["equity_parent"] == 80 * E8, detail
    assert detail["minority_interest"] == 2 * E8, detail
    assert detail["total_equity"] == 82 * E8, detail
    assert detail["total_assets"] == 200 * E8, detail
    assert detail["total_liabilities"] == 118 * E8, detail
    assert detail["fixed_assets"] == 30 * E8, detail
    assert detail["fixed_assets_yoy"] == 50.0, detail
    assert detail["construction_in_progress"] == 10 * E8, detail
    assert detail["construction_in_progress_yoy"] == 100.0, detail
    assert detail["intangible_assets"] == 5 * E8, detail
    assert detail["long_term_equity_invest"] == 3 * E8, detail
    assert detail["notes_payable"] == 2 * E8, detail
    assert detail["accounts_payable"] == 12 * E8, detail
    assert detail["advances_from_customers"] == 1 * E8, detail
    assert detail["contract_liabilities"] == 4 * E8, detail
    # 投资收益、公允价值变动和减值损失保留数据源符号
    assert detail["investment_income"] == -0.5 * E8, detail
    assert detail["fair_value_change"] == 0.1 * E8, detail
    assert detail["asset_impairment"] == -0.3 * E8, detail
    assert detail["credit_impairment"] == -0.2 * E8, detail
    assert detail["treasury_stock"] == 2 * E8, detail
    assert detail["rd_expense"] == 4 * E8, detail
    assert detail["capex"] == 8 * E8, detail

    # 营运资金科目同比：应收/预付/其他应收、应付/合同负债/开发支出
    assert detail["notes_receivable_yoy"] == 100.0, detail
    assert detail["prepayments_yoy"] == -20.0, detail
    assert detail["other_receivables_yoy"] == 100.0, detail
    assert detail["accounts_payable_yoy"] == 50.0, detail
    assert detail["contract_liabilities_yoy"] == 100.0, detail
    assert detail["development_expenditure_yoy"] == 50.0, detail

    # 三张表的完整科目按报表分组，保留元单位
    balance = detail["balance"]
    assert balance["notes_receivable"] == 6 * E8, balance
    assert balance["paid_in_capital"] == 10 * E8, balance
    assert balance["short_term_borrowings"] is None, balance
    income = detail["income"]
    assert income["operating_profit"] == 20 * E8, income
    assert income["net_profit_attributable"] == 16 * E8, income
    assert "basic_eps" not in income, income  # 每股收益不是金额，留在顶层
    cashflow = detail["cashflow"]
    assert cashflow["cash_from_sales"] == 95 * E8, cashflow
    assert cashflow["ending_cash"] == 20 * E8, cashflow

    # 上年同期缺失时同比留空，不能拿 0 当基数
    partial = snap.snapshot_from_rows([
        row("2026-06-30", revenue=100 * E8, fixed_assets=30 * E8),
    ])["metrics"]["detail"]
    assert partial["fixed_assets_yoy"] is None, partial
    assert partial["construction_in_progress_yoy"] is None, partial
    assert partial["basic_eps"] is None, partial
    assert partial["balance"]["notes_receivable"] is None, partial
    assert partial["income"]["operating_profit"] is None, partial
    assert partial["cashflow"]["cash_from_sales"] is None, partial

    # 金融机构也有每股收益、权益和资产，明细不能像安全指标那样整组清空
    bank = snap.snapshot_from_rows([
        row("2026-06-30", revenue=100 * E8, cost_of_revenue=None,
            basic_eps=5.0, equity_parent=500 * E8, total_assets=5000 * E8),
    ])["metrics"]["detail"]
    assert bank["basic_eps"] == 5.0, bank
    assert bank["equity_parent"] == 500 * E8, bank
    assert bank["total_assets"] == 5000 * E8, bank


def main() -> None:
    test_safety_metrics()
    test_detail_fields()

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
