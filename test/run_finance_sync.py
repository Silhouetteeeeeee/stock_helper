"""真实数据跑通：同步东鹏与自选股三表，打印指标并做勾稽校验。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.finance_service as fs

CODES = ["605499", "600036", "601899", "601318", "600066"]


def check(label: str, left, right, tol: float = 0.005) -> None:
    if left is None or right is None:
        print(f"  [跳过] {label}: 数据缺失")
        return
    diff = abs(left - right)
    limit = max(abs(left), abs(right), 1.0) * tol
    status = "OK  " if diff <= limit else "FAIL"
    print(f"  [{status}] {label}: {left:,.2f} vs {right:,.2f} (差 {diff:,.2f})")
    if diff > limit:
        raise AssertionError(f"{label} 不勾稽：{left} vs {right}")


def verify(code: str, rows: list[dict]) -> None:
    print(f"-- {code} 勾稽校验 --")
    for row in rows:
        date = row["report_date"]
        if None not in (row.get("total_assets"), row.get("total_liabilities"),
                        row.get("total_equity")):
            check(
                f"{date} 资产=负债+权益",
                row.get("total_assets"),
                row["total_liabilities"] + row["total_equity"],
            )
        check(
            f"{date} 资产=负债和权益总计",
            row.get("total_assets"),
            row.get("total_liabilities_and_equity"),
        )
        # 任一构成项缺失时跳过，不能把 None 当成 0 掩盖问题
        if None not in (row.get("net_profit"), row.get("total_profit"), row.get("income_tax")):
            check(
                f"{date} 净利润=利润总额-所得税",
                row.get("net_profit"),
                row["total_profit"] - row["income_tax"],
            )
        cash_parts = [
            row.get(key) for key in ("operating_cash_flow", "investment_cash_flow",
                                     "financing_cash_flow", "fx_effect")
        ]
        if row.get("net_cash_increase") is not None and all(v is not None for v in cash_parts):
            check(
                f"{date} 现金净增加=经营+投资+筹资+汇率",
                row.get("net_cash_increase"),
                sum(cash_parts),
            )
        if None not in (row.get("ending_cash"), row.get("beginning_cash"),
                        row.get("net_cash_increase")):
            check(
                f"{date} 期末现金=期初+净增加",
                row.get("ending_cash"),
                row["beginning_cash"] + row["net_cash_increase"],
            )


def show(code: str, name: str, rows: list[dict]) -> None:
    print(f"\n== {name}（{code}）最近 6 期 ==")
    header = f"{'报告期':<12}{'营收(亿)':>10}{'营收同比':>10}{'单季同比':>10}{'毛利率':>9}{'净利率':>9}{'ROE':>8}{'负债率':>9}{'经营现金流(亿)':>15}"
    print(header)
    for row in rows[-6:]:
        def fmt(value, scale=1.0, suffix=""):
            return "--" if value is None else f"{value / scale:.2f}{suffix}"

        print(
            f"{row['report_date']:<12}"
            f"{fmt(row.get('revenue'), 1e8):>10}"
            f"{fmt(row.get('revenue_yoy'), 1.0, '%'):>10}"
            f"{fmt(row.get('revenue_sq_yoy'), 1.0, '%'):>10}"
            f"{fmt(row.get('gross_margin'), 1.0, '%'):>9}"
            f"{fmt(row.get('net_margin'), 1.0, '%'):>9}"
            f"{fmt(row.get('roe'), 1.0, '%'):>8}"
            f"{fmt(row.get('debt_ratio'), 1.0, '%'):>9}"
            f"{fmt(row.get('operating_cash_flow'), 1e8):>15}"
        )


def main() -> None:
    names = {"605499": "东鹏饮料", "600036": "招商银行", "601899": "紫金矿业",
             "601318": "中国平安", "600066": "宇通客车"}
    for code in CODES:
        print(f"同步 {names.get(code, code)} ...")
        counts = fs.sync_statements(code)
        rows = fs.get_financials(code)
        print(f"  写入 {counts}，共 {len(rows)} 期，"
              f"{rows[0]['report_date']} ~ {rows[-1]['report_date']}")
        verify(code, rows)
        show(code, names.get(code, code), rows)

    print("\n已同步：", fs.synced_codes())
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
