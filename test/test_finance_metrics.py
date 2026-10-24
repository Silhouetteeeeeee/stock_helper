"""派生指标口径验证：单季差分、同比、年化 ROE、周转天数。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock.finance_metrics import build_metrics


def row(report_date: str, **kwargs) -> dict:
    base = {"report_date": report_date}
    base.update(kwargs)
    return base


def close(actual, expected, tol=0.01) -> None:
    assert actual is not None, "值为 None"
    assert abs(actual - expected) < tol, (actual, expected)


def main() -> None:
    rows = [
        row("2024-03-31", revenue=100.0, total_revenue=100.0, cost_of_revenue=60.0,
            net_profit=10.0, net_profit_attributable=10.0),
        row("2024-06-30", revenue=220.0, total_revenue=220.0, cost_of_revenue=130.0,
            net_profit=22.0, net_profit_attributable=22.0),
        row("2024-09-30", revenue=360.0, total_revenue=360.0, cost_of_revenue=210.0,
            net_profit=36.0, net_profit_attributable=36.0),
        row("2024-12-31", revenue=520.0, total_revenue=520.0, cost_of_revenue=300.0,
            net_profit=52.0, net_profit_attributable=52.0,
            total_assets=1000.0, total_liabilities=500.0,
            equity_parent=500.0, total_equity=500.0,
            accounts_receivable=60.0, inventory=180.0),
        row("2025-03-31", revenue=120.0, total_revenue=120.0, cost_of_revenue=65.0,
            net_profit=12.0, net_profit_attributable=12.0),
        row("2025-06-30", revenue=270.0, total_revenue=270.0, cost_of_revenue=150.0,
            net_profit=27.0, net_profit_attributable=27.0,
            total_assets=1100.0, total_liabilities=540.0,
            equity_parent=560.0, total_equity=560.0,
            accounts_receivable=80.0, inventory=200.0,
            current_assets_total=600.0, current_liabilities_total=300.0,
            operating_cash_flow=30.0, capex=20.0, cash_from_sales=300.0,
            rd_expense=10.0, cash=80.0,
            short_term_borrowings=50.0, current_portion_lt_debt=10.0,
            long_term_borrowings=40.0, bonds_payable=0.0, lease_liabilities=0.0),
    ]

    metrics = {item["report_date"]: item for item in build_metrics(rows)}
    latest = metrics["2025-06-30"]

    # 单季差分：2025Q2 = 270 - 120；2024Q2 = 220 - 100
    close(latest["revenue_sq"], 150.0)
    close(latest["revenue_sq_yoy"], (150 - 120) / 120 * 100)
    close(latest["net_profit_sq"], 15.0)
    close(latest["net_profit_sq_yoy"], (15 - 12) / 12 * 100)

    # 累计同比：2025H1 270 vs 2024H1 220
    close(latest["revenue_yoy"], (270 - 220) / 220 * 100)

    # 盈利能力
    close(latest["gross_margin"], (270 - 150) / 270 * 100)
    close(latest["net_margin"], 27 / 270 * 100)
    close(latest["rd_ratio"], 10 / 270 * 100)

    # 年化：半年利润 27，平均权益 (560+500)/2，天数 181
    close(latest["roe"], 27 * 365 / 181 / 530 * 100)
    close(latest["roa"], 27 * 365 / 181 / 1050 * 100)

    # 偿债与周转
    close(latest["debt_ratio"], 49.0909, 0.01)
    close(latest["current_ratio"], 2.0)
    close(latest["quick_ratio"], 400 / 300)

    # 周转天数 = 平均余额 / 报告期收入 × 181 天
    close(latest["ar_turnover_days"], 70 / 270 * 181)
    close(latest["inventory_turnover_days"], 190 / 150 * 181)

    # 现金流
    close(latest["ocf_to_net_profit"], 30 / 27)
    close(latest["cash_to_revenue"], 300 / 270 * 100)
    close(latest["free_cash_flow"], 10.0)
    close(latest["net_debt"], 50 + 10 + 40 - 80)

    # 缺少上一期数据时必须是 None，不能凭空差分
    first = metrics["2024-03-31"]
    assert first["revenue_sq"] == 100.0
    assert first["revenue_sq_yoy"] is None, first["revenue_sq_yoy"]

    # 只有一季报、没有上一期时 Q2 单季不可计算
    lonely = build_metrics([row("2025-06-30", revenue=50.0, total_revenue=50.0)])
    assert lonely[0]["revenue_sq"] is None, lonely[0]["revenue_sq"]

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
