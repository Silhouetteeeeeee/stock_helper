"""财务派生指标：纯函数实现，不依赖数据库和网络，便于单元测试。

输入是按报告期升序排列的合并报表行（字典，字段名见 ``finance_fields``），
输出同样按报告期排列的指标行。

口径约定
--------
* 报表原始数据是**报告期累计值**（一季报 = 1~3 月，半年报 = 1~6 月）。
  需要观察单季趋势时用 ``*_sq`` 字段，由累计值逐季差分得到。
* 资产负债类指标用（期初 + 期末）/ 2 的均值，期初取上一年年报；
  一季报的期初同时也是上一季度末。
* 比率类指标在报表期内累计计算，ROE / ROA 额外按天数年化，
  以免半年报的 ROE 被误读成全年水平。
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

METRIC_FIELDS: tuple[str, ...] = (
    "revenue",
    "revenue_yoy",
    "revenue_sq",
    "revenue_sq_yoy",
    "net_profit",
    "net_profit_yoy",
    "net_profit_sq",
    "net_profit_sq_yoy",
    "gross_margin",
    "net_margin",
    "rd_ratio",
    "roe",
    "roa",
    "debt_ratio",
    "current_ratio",
    "quick_ratio",
    "ar_turnover_days",
    "inventory_turnover_days",
    "ocf_to_net_profit",
    "cash_to_revenue",
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
    "net_debt",
)


def build_metrics(rows: Iterable[dict]) -> list[dict]:
    """按报告期计算全部派生指标。"""
    ordered = sorted(rows, key=lambda row: row["report_date"])
    by_date = {row["report_date"]: row for row in ordered}
    single_quarter = _single_quarter_values(ordered, by_date)

    result = []
    for row in ordered:
        result.append(_build_row(row, by_date, single_quarter))
    return result


def _build_row(
    current: dict,
    by_date: dict[str, dict],
    single_quarter: dict[tuple[str, str], float | None],
) -> dict:
    report_date = current["report_date"]
    elapsed = _elapsed_days(report_date)

    revenue = _pick(current, "revenue", "total_revenue")
    total_revenue = _pick(current, "total_revenue", "revenue")
    cost = current.get("cost_of_revenue")
    net_profit = current.get("net_profit")
    net_profit_attr = _pick(current, "net_profit_attributable", "net_profit")
    total_assets = current.get("total_assets")
    total_equity = _pick(current, "equity_parent", "total_equity")

    sq_revenue = single_quarter.get((report_date, "revenue"))
    sq_profit = single_quarter.get((report_date, "net_profit"))
    last_year = _same_quarter_last_year(report_date)
    sq_revenue_ly = single_quarter.get((last_year, "revenue"))
    sq_profit_ly = single_quarter.get((last_year, "net_profit"))

    return {
        "report_date": report_date,
        "revenue": total_revenue,
        "revenue_yoy": _growth(total_revenue, _value(by_date, last_year, "total_revenue")),
        "revenue_sq": sq_revenue,
        "revenue_sq_yoy": _growth(sq_revenue, sq_revenue_ly),
        "net_profit": net_profit,
        "net_profit_yoy": _growth(net_profit, _value(by_date, last_year, "net_profit")),
        "net_profit_sq": sq_profit,
        "net_profit_sq_yoy": _growth(sq_profit, sq_profit_ly),
        "gross_margin": _ratio(_subtract(revenue, cost), revenue, 100.0),
        "net_margin": _ratio(net_profit, total_revenue, 100.0),
        "rd_ratio": _ratio(current.get("rd_expense"), total_revenue, 100.0),
        "roe": _annualized(
            net_profit_attr,
            _average(total_equity, _value(by_date, _previous_year_end(report_date), "equity_parent", "total_equity")),
            elapsed,
        ),
        "roa": _annualized(
            net_profit,
            _average(total_assets, _value(by_date, _previous_year_end(report_date), "total_assets")),
            elapsed,
        ),
        "debt_ratio": _ratio(current.get("total_liabilities"), total_assets, 100.0),
        "current_ratio": _ratio(current.get("current_assets_total"), current.get("current_liabilities_total")),
        "quick_ratio": _ratio(
            _subtract(current.get("current_assets_total"), current.get("inventory")),
            current.get("current_liabilities_total"),
        ),
        "ar_turnover_days": _turnover_days(
            current.get("accounts_receivable"),
            _value(by_date, _previous_year_end(report_date), "accounts_receivable"),
            revenue,
            elapsed,
        ),
        "inventory_turnover_days": _turnover_days(
            current.get("inventory"),
            _value(by_date, _previous_year_end(report_date), "inventory"),
            cost,
            elapsed,
        ),
        "ocf_to_net_profit": _ratio(current.get("operating_cash_flow"), net_profit),
        "cash_to_revenue": _ratio(current.get("cash_from_sales"), total_revenue, 100.0),
        "operating_cash_flow": current.get("operating_cash_flow"),
        "capex": current.get("capex"),
        "free_cash_flow": _subtract(current.get("operating_cash_flow"), current.get("capex")),
        "net_debt": _net_debt(current),
    }


def _single_quarter_values(
    ordered: list[dict],
    by_date: dict[str, dict],
) -> dict[tuple[str, str], float | None]:
    """把累计值差分成本单季值。

    一季报本身就是单季；其余报告期用本期累计减上一报告期累计。
    缺少上一期数据时返回 None，宁可缺值也不编造。
    """
    values: dict[tuple[str, str], float | None] = {}
    for row in ordered:
        report_date = row["report_date"]
        quarter = _quarter(report_date)
        previous = by_date.get(_previous_quarter(report_date))
        for field in ("revenue", "total_revenue", "net_profit"):
            current = row.get(field)
            if current is None:
                values[(report_date, field)] = None
            elif quarter == 1:
                values[(report_date, field)] = current
            elif previous is None or previous.get(field) is None:
                values[(report_date, field)] = None
            else:
                values[(report_date, field)] = current - previous[field]
    # 收入优先用"营业收入"，缺失时退回"营业总收入"
    for row in ordered:
        report_date = row["report_date"]
        if values.get((report_date, "revenue")) is None:
            values[(report_date, "revenue")] = values.get((report_date, "total_revenue"))
    return values


# ---------------------------------------------------------------------------
# 基础运算
# ---------------------------------------------------------------------------
def _quarter(report_date: str) -> int:
    return (date.fromisoformat(report_date).month - 1) // 3 + 1


def _previous_quarter(report_date: str) -> str:
    current = date.fromisoformat(report_date)
    year, quarter = current.year, _quarter(report_date)
    if quarter == 1:
        return f"{year - 1}-12-31"
    if quarter == 2:
        return f"{year}-03-31"
    if quarter == 3:
        return f"{year}-06-30"
    return f"{year}-09-30"


def _same_quarter_last_year(report_date: str) -> str:
    current = date.fromisoformat(report_date)
    return f"{current.year - 1}-{current.month:02d}-{current.day:02d}"


def _previous_year_end(report_date: str) -> str:
    return f"{date.fromisoformat(report_date).year - 1}-12-31"


def _elapsed_days(report_date: str) -> int:
    """报告期从年初到期末经过的天数，用于周转率与年化。"""
    current = date.fromisoformat(report_date)
    return (current - date(current.year, 1, 1)).days + 1


def _pick(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _value(by_date: dict[str, dict], report_date: str, *keys: str) -> float | None:
    row = by_date.get(report_date)
    return None if row is None else _pick(row, *keys)


def _subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _average(left: float | None, right: float | None) -> float | None:
    if left is None:
        return None
    if right is None:
        return left
    return (left + right) / 2


def _ratio(numerator: float | None, denominator: float | None, scale: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * scale


def _annualized(profit: float | None, base: float | None, elapsed: int) -> float | None:
    """把报告期利润年化后除以平均资产/权益，得到可比口径的回报率。"""
    if profit is None or base is None or base == 0 or elapsed <= 0:
        return None
    return profit * (365.0 / elapsed) / base * 100.0


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _turnover_days(
    ending: float | None,
    beginning: float | None,
    flow: float | None,
    elapsed: int,
) -> float | None:
    """周转天数 = 平均余额 / 报告期收入 × 报告期天数。"""
    if ending is None or flow is None or flow == 0:
        return None
    average = _average(ending, beginning)
    if average is None:
        return None
    return average / flow * elapsed


def _net_debt(row: dict) -> float | None:
    """有息负债减去现金及交易性金融资产；任一主项缺失时不计算。"""
    cash = row.get("cash")
    if cash is None:
        return None
    debt_items = (
        row.get("short_term_borrowings"),
        row.get("current_portion_lt_debt"),
        row.get("long_term_borrowings"),
        row.get("bonds_payable"),
        row.get("lease_liabilities"),
    )
    # 银行等金融机构不披露这些有息负债科目，净负债没有可比意义
    if all(value is None for value in debt_items):
        return None
    debt = sum(value for value in debt_items if value is not None)
    liquid = cash + (row.get("trading_fin_assets") or 0.0)
    return debt - liquid
