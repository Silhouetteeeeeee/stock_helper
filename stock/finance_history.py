"""年度财务序列：把合并财报压成 5–10 年的年报一行一年。

竞争优势不能只看最近一期：毛利率、净利率、费用率、ROE 要在多年里
稳得住，才谈得上"能持续"。这里只保留年报（``-12-31``），季报不混入，
也不拿三季报顶替缺失的年报。

* ``build_history(code)``       走 ``ensure_financials`` 缓存后生成
* ``history_from_rows(rows)``   纯函数，便于测试

金额换算成亿元保留两位；百分比是 1 表示 1%，保留一位；
缺失值保留 ``None``，不填 0。
"""

from __future__ import annotations

from .db_service import get_stock, normalize_stock_code
from .finance_service import DEFAULT_CACHE_DAYS, FinanceError, ensure_financials

SOURCE_LABEL = "新浪财经 stock_financial_report_sina"


def build_history(
    code: str,
    years: int = 10,
    max_age_days: int = DEFAULT_CACHE_DAYS,
    force: bool = False,
) -> dict:
    """按需取数并生成年度序列；返回里补上 ``code`` 与 ``name``。"""
    code = normalize_stock_code(code)
    if not code:
        raise FinanceError("请输入股票代码")

    rows = ensure_financials(code, max_age_days=max_age_days, force=force)
    stock = get_stock(code)
    data = history_from_rows(rows, years=years)
    data["code"] = code
    data["name"] = stock.name if stock is not None else None
    return data


def history_from_rows(rows: list[dict], years: int = 10) -> dict:
    """由合并后的财报行生成年度序列（纯函数，不碰数据库和网络）。"""
    window = _window(years)
    annual = sorted(
        (row for row in rows if _is_annual(row)),
        key=lambda row: row["report_date"],
    )
    if not annual:
        return {
            "available": False,
            "as_of": None,
            "fetched_at": None,
            "span": None,
            "years": [],
            "notes": ["没有年报数据"],
        }

    selected = annual[-window:]
    first_year = selected[0]["report_date"][:4]
    last_year = selected[-1]["report_date"][:4]
    notes: list[str] = []
    if len(selected) < window:
        notes.append(f"只有 {len(selected)} 年年报（计划回看 {window} 年）")
    missing = _missing_years(selected)
    if missing:
        notes.append("缺少年报：" + "、".join(missing))

    stamps = [row.get("fetched_at") for row in selected if row.get("fetched_at")]
    return {
        "available": True,
        "as_of": selected[-1]["report_date"],
        "fetched_at": max(stamps) if stamps else None,
        "span": f"{first_year}-{last_year}",
        "years": [_year_row(row) for row in selected],
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _is_annual(row: dict) -> bool:
    report_date = row.get("report_date")
    return isinstance(report_date, str) and report_date.endswith("-12-31")


def _window(years) -> int:
    try:
        return min(max(int(years), 1), 20)
    except (TypeError, ValueError):
        return 10


def _missing_years(rows: list[dict]) -> list[str]:
    present = {row["report_date"][:4] for row in rows}
    start, end = int(rows[0]["report_date"][:4]), int(rows[-1]["report_date"][:4])
    return [str(year) for year in range(start, end + 1) if str(year) not in present]


def _year_row(row: dict) -> dict:
    revenue = row.get("revenue")
    # 金融机构没有"营业成本"，毛利率与费用率口径都不可比，整组留空
    financial = row.get("cost_of_revenue") is None and row.get("gross_margin") is None
    return {
        "period": row["report_date"],
        "revenue_yi": _yi(revenue),
        "revenue_yoy": _round(row.get("revenue_yoy"), 1),
        "net_profit_yi": _yi(row.get("net_profit")),
        "net_profit_yoy": _round(row.get("net_profit_yoy"), 1),
        "basic_eps": _round(row.get("basic_eps"), 3),
        "gross_margin": None if financial else _round(row.get("gross_margin"), 1),
        "net_margin": _round(row.get("net_margin"), 1),
        "roe": _round(row.get("roe"), 1),
        "roa": _round(row.get("roa"), 1),
        "selling_ratio": None if financial else _ratio(row.get("selling_expense"), revenue),
        "admin_ratio": None if financial else _ratio(row.get("admin_expense"), revenue),
        "rd_ratio": None if financial else _ratio(row.get("rd_expense"), revenue),
        "ocf_to_net_profit": None if financial else _round(row.get("ocf_to_net_profit"), 3),
        "operating_cash_flow_yi": _yi(row.get("operating_cash_flow")),
        "capex_yi": _yi(row.get("capex")),
        "free_cash_flow_yi": _yi(row.get("free_cash_flow")),
        "equity_parent_yi": _yi(row.get("equity_parent")),
        "net_debt_yi": _yi(row.get("net_debt")),
        "interest_debt_yi": None if financial else _yi(_interest_debt(row)),
        "fixed_assets_yi": _yi(row.get("fixed_assets")),
        "construction_in_progress_yi": _yi(row.get("construction_in_progress")),
        "debt_ratio": _round(row.get("debt_ratio"), 1),
    }


def _interest_debt(row: dict) -> float | None:
    """有息负债合计；科目全缺时返回 None，不用 0 冒充没有负债。"""
    values = (
        row.get("short_term_borrowings"),
        row.get("current_portion_lt_debt"),
        row.get("long_term_borrowings"),
        row.get("bonds_payable"),
        row.get("lease_liabilities"),
    )
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _yi(value) -> float | None:
    """元 -> 亿元，保留两位。"""
    return None if value is None else round(value / 1e8, 2)


def _ratio(part, revenue) -> float | None:
    # 费用科目为负多是冲回或数据源符号问题，负收入也没有可比口径；
    # 这两种情况宁可留空，也不能输出负费用率冒充真实比率
    if part is None or part < 0 or revenue is None or revenue <= 0:
        return None
    return round(part / revenue * 100.0, 1)


def _round(value, digits: int) -> float | None:
    return None if value is None else round(value, digits)
