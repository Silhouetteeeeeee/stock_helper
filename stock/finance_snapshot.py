"""基本面快照：把宽表财报压缩成分析用的摘要与异常提示。

``finance_service`` 存下来的是 29 期 × 上百个字段的宽表，人和模型都读不动。
这里把它压成三段：最近一期的关键指标、近 N 个季度的单季趋势、一组规则化
的异常提示。每个数字都带报告期，数据来源和抓取时间一并带出。

* ``build_snapshot(code)``       按需取数（走 ``ensure_financials`` 的缓存）后生成快照
* ``snapshot_from_rows(rows)``   纯函数，直接由合并后的财报行生成，便于测试
* ``format_snapshot(snapshot)``  渲染成一段可读文本

口径：金额单位是元，百分比是 1 表示 1%（与 ``finance_metrics`` 一致），
只在渲染时换算成亿；缺失值一律保留为 None，不填 0。

``build_snapshot`` 会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable

from .db_service import get_stock, normalize_stock_code
from .finance_service import DEFAULT_CACHE_DAYS, FinanceError, ensure_financials

DEFAULT_TREND_PERIODS = 8
SOURCE_LABEL = "新浪财经 stock_financial_report_sina"

# --- 异常判定阈值（都是"超过多少才算"，避免正常波动被当成异常） -----------
RECEIVABLE_GAP_PP = 20.0      # 应收账款增速比营收增速快这么多百分点
INVENTORY_GAP_PP = 20.0       # 存货增速比营收增速快这么多百分点
OCF_RATIO_WARN = 0.5          # 经营现金流 / 净利润 低于此值（0.5 = 净利润的一半）
GROSS_MARGIN_DROP_PP = 5.0    # 毛利率同比下滑这么多百分点
DEBT_RATIO_RISE_PP = 10.0     # 资产负债率同比上升这么多百分点
REVENUE_DECLINE_PCT = -10.0   # 营收同比下滑超过这个比例
PROFIT_OUTPACE_PP = 20.0      # 净利润增速比营收增速快这么多百分点

LEVEL_LABELS = {"warning": "异常", "info": "关注"}


def build_snapshot(
    code: str,
    periods: int = DEFAULT_TREND_PERIODS,
    max_age_days: int = DEFAULT_CACHE_DAYS,
    force: bool = False,
) -> dict:
    """按需取数并生成基本面快照。

    数据没同步过、或者已经过期时会先抓一次（同 ``ensure_financials``），
    所以第一次调用会慢几秒，之后都走本地缓存。
    """
    code = normalize_stock_code(code)
    if not code:
        raise FinanceError("请输入股票代码")

    rows = ensure_financials(code, max_age_days=max_age_days, force=force)
    stock = get_stock(code)
    return snapshot_from_rows(
        rows,
        periods=periods,
        name=stock.name if stock is not None else None,
    )


def snapshot_from_rows(
    rows: list[dict],
    periods: int = DEFAULT_TREND_PERIODS,
    name: str | None = None,
) -> dict:
    """由合并后的财报行生成快照（纯函数，不碰数据库和网络）。"""
    ordered = sorted(rows, key=lambda row: row["report_date"])
    if not ordered:
        return {
            "available": False,
            "code": None,
            "name": name,
            "periods": 0,
            "latest": None,
            "metrics": {},
            "trend": [],
            "flags": [],
            "sources": {"statement": SOURCE_LABEL},
            "summary": f"{name or '该股票'}：暂无财报数据",
        }

    latest = ordered[-1]
    last_year = _by_date(ordered, _same_quarter_last_year(latest["report_date"]))
    trend = ordered[-max(1, periods):]

    snapshot = {
        "available": True,
        "code": latest.get("code"),
        "name": name,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "periods": len(ordered),
        "latest": {
            "report_date": latest["report_date"],
            "ann_date": latest.get("ann_date"),
            "audited": latest.get("audited"),
            "fetched_at": latest.get("fetched_at"),
        },
        "metrics": _metrics(latest, last_year),
        "trend": [_trend_row(row) for row in trend],
        "flags": [],
        "sources": {"statement": SOURCE_LABEL},
    }
    snapshot["flags"] = _flags(ordered, latest, last_year, snapshot["trend"])
    snapshot["summary"] = format_snapshot(snapshot)
    return snapshot


def format_snapshot(snapshot: dict) -> str:
    """把快照渲染成一段可读文本，供界面或模型直接使用。"""
    if not snapshot.get("available"):
        return f"{snapshot.get('name') or '该股票'}：暂无财报数据"

    latest = snapshot["latest"]
    metrics = snapshot["metrics"]
    title = f"{snapshot['name']}({snapshot['code']})" if snapshot.get("name") else snapshot["code"]
    header = f"{title} · 最新报告期 {latest['report_date']}"
    if latest.get("ann_date"):
        header += f"（公告 {latest['ann_date']}）"
    lines = [header]

    revenue, profit = metrics["revenue"], metrics["net_profit"]
    lines.append(
        f"营收 {_yi(revenue['value'])}，同比 {_pct(revenue['yoy'])}；"
        f"单季 {_yi(revenue['single_quarter'])}，同比 {_pct(revenue['single_quarter_yoy'])}"
    )
    lines.append(
        f"净利润 {_yi(profit['value'])}，同比 {_pct(profit['yoy'])}；"
        f"单季 {_yi(profit['single_quarter'])}，同比 {_pct(profit['single_quarter_yoy'])}"
    )

    profitability = _join(
        _part("毛利率", metrics["gross_margin"]["value"], "%",
              _pp(metrics["gross_margin"]["change_pp"])),
        _part("净利率", metrics["net_margin"]["value"], "%",
              _pp(metrics["net_margin"]["change_pp"])),
        _part("ROE", metrics["roe"]["value"], "%"),
        _part("ROA", metrics["roa"]["value"], "%"),
    )
    if profitability:
        lines.append(profitability)

    cash = metrics["cash"]
    cash_flow = _join(
        _part("经营现金流", cash["operating_cash_flow"], yi=True),
        _percent_part("为净利润的", cash["ocf_to_net_profit"]),
        _part("自由现金流", cash["free_cash_flow"], yi=True),
        _signed_part("净负债", cash["net_debt"]),
    )
    if cash_flow:
        lines.append(cash_flow)

    balance = _join(
        _part("资产负债率", metrics["debt_ratio"]["value"], "%",
              _pp(metrics["debt_ratio"]["change_pp"])),
        _ratio_part("流动比率", metrics["current_ratio"]["value"]),
        _part("货币资金", cash["cash_balance"], yi=True),
    )
    if balance:
        lines.append(balance)

    safety = metrics.get("safety") or {}
    coverage_basis = safety.get("interest_coverage_basis")
    # 用财务费用近似时要标出来，不能和利息费用口径混为一谈
    coverage_note = "财务费用近似" if coverage_basis and "财务费用" in coverage_basis else None
    safety_line = _join(
        _part("有息负债", safety.get("interest_debt"), yi=True),
        _part("短期有息负债", safety.get("short_term_debt"), yi=True),
        _times_part("利息保障", safety.get("interest_coverage"), coverage_note),
        _goodwill_part(safety.get("goodwill"), safety.get("goodwill_ratio")),
        _part("应收账款", safety.get("accounts_receivable"), yi=True,
              extra=_pct(safety.get("accounts_receivable_yoy"))),
        _part("存货", safety.get("inventory"), yi=True,
              extra=_pct(safety.get("inventory_yoy"))),
    )
    if safety_line:
        lines.append(safety_line)

    trend = snapshot["trend"]
    if trend:
        lines.append("")
        lines.append(
            f"近 {len(trend)} 个季度单季营收（亿）："
            + " / ".join(_yi_short(row["revenue_sq"]) for row in trend)
        )
        lines.append(
            f"近 {len(trend)} 个季度单季净利（亿）："
            + " / ".join(_yi_short(row["net_profit_sq"]) for row in trend)
        )

    if snapshot["flags"]:
        lines.append("")
        lines.append("提示：")
        for flag in snapshot["flags"]:
            lines.append(f"- [{LEVEL_LABELS.get(flag['level'], flag['level'])}] {flag['message']}")

    fetched = latest.get("fetched_at")
    lines.append("")
    lines.append(
        f"数据来源：{snapshot['sources']['statement']}"
        + (f"，抓取于 {fetched}" if fetched else "")
        + f"，共 {snapshot['periods']} 期"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 指标汇总
# ---------------------------------------------------------------------------
def _metrics(latest: dict, last_year: dict | None) -> dict:
    return {
        "revenue": {
            "value": latest.get("revenue"),
            "yoy": latest.get("revenue_yoy"),
            "single_quarter": latest.get("revenue_sq"),
            "single_quarter_yoy": latest.get("revenue_sq_yoy"),
        },
        "net_profit": {
            "value": latest.get("net_profit"),
            "attributable": latest.get("net_profit_attributable"),
            "yoy": latest.get("net_profit_yoy"),
            "single_quarter": latest.get("net_profit_sq"),
            "single_quarter_yoy": latest.get("net_profit_sq_yoy"),
        },
        "gross_margin": _level_metric(latest, last_year, "gross_margin"),
        "net_margin": _level_metric(latest, last_year, "net_margin"),
        "debt_ratio": _level_metric(latest, last_year, "debt_ratio"),
        "roe": {"value": latest.get("roe")},
        "roa": {"value": latest.get("roa")},
        "current_ratio": {"value": latest.get("current_ratio")},
        "quick_ratio": {"value": latest.get("quick_ratio")},
        "rd_ratio": {"value": latest.get("rd_ratio")},
        "cash": {
            "operating_cash_flow": latest.get("operating_cash_flow"),
            "free_cash_flow": latest.get("free_cash_flow"),
            # 银行的经营现金流主要由存款变动决定，和净利润没有可比性
            "ocf_to_net_profit": (
                None if _is_financial(latest) else latest.get("ocf_to_net_profit")
            ),
            "net_debt": _net_debt(latest),
            "cash_balance": latest.get("cash"),
            "short_term_borrowings": latest.get("short_term_borrowings"),
        },
        "safety": _safety(latest, last_year),
        "detail": _detail(latest, last_year),
    }


def _is_financial(row: dict) -> bool:
    """银行、保险等金融机构没有营业成本科目，部分指标口径不可比。"""
    return row.get("cost_of_revenue") is None


def _net_debt(row: dict) -> float | None:
    """金融机构的净负债没有可比意义：它们没有有息负债科目，
    现金是准备金而不是可动用资金，算出来的数字只会误导。"""
    return None if _is_financial(row) else row.get("net_debt")


def _safety(latest: dict, last_year: dict | None) -> dict:
    """财务安全：有息负债、利息保障、商誉与营运资金占用。

    金额单位仍是元；比率是 1 表示 1%，和其余指标一致。金融机构没有
    可比的"有息负债/利息保障"口径（存款不是有息负债，利息支出也不是
    费用），整组留空，避免用错口径得出"很安全"的假象。
    """
    safety = {
        "interest_debt": _sum(
            latest.get("short_term_borrowings"),
            latest.get("current_portion_lt_debt"),
            latest.get("long_term_borrowings"),
            latest.get("bonds_payable"),
            latest.get("lease_liabilities"),
        ),
        "short_term_debt": _sum(
            latest.get("short_term_borrowings"),
            latest.get("current_portion_lt_debt"),
        ),
        "interest_coverage": None,
        "interest_coverage_basis": None,
        "goodwill": latest.get("goodwill"),
        "goodwill_ratio": None,
        "accounts_receivable": latest.get("accounts_receivable"),
        "accounts_receivable_yoy": _growth(
            latest.get("accounts_receivable"), _value(last_year, "accounts_receivable")
        ),
        "inventory": latest.get("inventory"),
        "inventory_yoy": _growth(
            latest.get("inventory"), _value(last_year, "inventory")
        ),
        "cash_to_short_term_debt": None,
    }
    if _is_financial(latest):
        return {key: None for key in safety}

    safety["interest_coverage"], safety["interest_coverage_basis"] = _interest_coverage(latest)

    goodwill, equity = safety["goodwill"], latest.get("total_equity")
    if goodwill is not None and equity is not None and equity > 0:
        safety["goodwill_ratio"] = round(goodwill / equity * 100.0, 1)

    cash, short_term = latest.get("cash"), safety["short_term_debt"]
    if cash is not None and short_term:
        safety["cash_to_short_term_debt"] = round(cash / short_term, 2)
    return safety


def _detail(latest: dict, last_year: dict | None) -> dict:
    """报表里对研究有用、但不属于核心比率的明细科目。

    数值保持数据源原始符号：投资收益、公允价值变动、减值损失可能是
    负数。下游换算成亿元时不要把符号抹掉。
    """
    fields = (
        "basic_eps",
        "diluted_eps",
        "equity_parent",
        "minority_interest",
        "total_equity",
        "total_assets",
        "total_liabilities",
        "fixed_assets",
        "construction_in_progress",
        "intangible_assets",
        "long_term_equity_invest",
        "notes_payable",
        "accounts_payable",
        "advances_from_customers",
        "contract_liabilities",
        "investment_income",
        "fair_value_change",
        "asset_impairment",
        "credit_impairment",
        "treasury_stock",
        "rd_expense",
        "capex",
    )
    detail = {field: latest.get(field) for field in fields}
    detail["fixed_assets_yoy"] = _growth(
        latest.get("fixed_assets"), _value(last_year, "fixed_assets")
    )
    detail["construction_in_progress_yoy"] = _growth(
        latest.get("construction_in_progress"),
        _value(last_year, "construction_in_progress"),
    )
    return detail


def _interest_coverage(row: dict) -> tuple[float | None, str | None]:
    """利息保障倍数：``(利润总额 + 利息费用) / 利息费用``。

    数据源优先用"利息费用"；没有该科目时退回财务费用近似。财务费用
    为负说明利息收入大于支出，算不出有意义的覆盖倍数，留空。
    """
    profit = row.get("total_profit")
    interest = row.get("interest_expense")
    basis = "利息费用"
    if interest is None:
        interest = row.get("finance_expense")
        basis = "财务费用（近似）"
    if profit is None or interest is None or interest <= 0:
        return None, None
    return round((profit + interest) / interest, 1), basis


def _sum(*values) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _level_metric(latest: dict, last_year: dict | None, field: str) -> dict:
    """比率类指标：本期值 + 同比变动（百分点）。"""
    value = latest.get(field)
    previous = _value(last_year, field)
    change = None if value is None or previous is None else value - previous
    return {"value": value, "change_pp": change}


def _trend_row(row: dict) -> dict:
    return {
        "report_date": row["report_date"],
        "revenue_sq": row.get("revenue_sq"),
        "revenue_sq_yoy": row.get("revenue_sq_yoy"),
        "net_profit_sq": row.get("net_profit_sq"),
        "net_profit_sq_yoy": row.get("net_profit_sq_yoy"),
        "gross_margin": row.get("gross_margin"),
        "net_margin": row.get("net_margin"),
    }


# ---------------------------------------------------------------------------
# 异常提示
# ---------------------------------------------------------------------------
def _flags(
    ordered: list[dict],
    latest: dict,
    last_year: dict | None,
    trend: list[dict],
) -> list[dict]:
    flags: list[dict] = []
    revenue_yoy = latest.get("revenue_yoy")
    profit_yoy = latest.get("net_profit_yoy")

    # 应收账款 / 存货跑得比收入快，是放宽信用或压货的常见信号
    for field, label, gap, code in (
        ("accounts_receivable", "应收账款", RECEIVABLE_GAP_PP, "receivables_outpace_revenue"),
        ("inventory", "存货", INVENTORY_GAP_PP, "inventory_outpace_revenue"),
    ):
        growth = _growth(latest.get(field), _value(last_year, field))
        if growth is None or revenue_yoy is None:
            continue
        if growth > 0 and growth - revenue_yoy >= gap:
            flags.append({
                "level": "warning",
                "code": code,
                "message": f"{label}同比 {growth:+.1f}%，明显快于营收 {revenue_yoy:+.1f}%",
                "detail": {
                    "field": field,
                    "value": latest.get(field),
                    "yoy": growth,
                    "revenue_yoy": revenue_yoy,
                },
            })

    margin = latest.get("gross_margin")
    margin_last_year = _value(last_year, "gross_margin")
    if (
        margin is not None
        and margin_last_year is not None
        and margin - margin_last_year <= -GROSS_MARGIN_DROP_PP
    ):
        flags.append({
            "level": "warning",
            "code": "gross_margin_drop",
            "message": f"毛利率 {margin:.1f}%，同比下降 {margin_last_year - margin:.1f}pp",
            "detail": {"value": margin, "previous": margin_last_year},
        })

    if revenue_yoy is not None and profit_yoy is not None:
        if revenue_yoy > 0 and profit_yoy < 0:
            flags.append({
                "level": "warning",
                "code": "profit_falling_revenue_rising",
                "message": f"营收同比 {revenue_yoy:+.1f}%，净利润却同比 {profit_yoy:+.1f}%",
                "detail": {"revenue_yoy": revenue_yoy, "net_profit_yoy": profit_yoy},
            })
        elif revenue_yoy > 0 and profit_yoy - revenue_yoy >= PROFIT_OUTPACE_PP:
            flags.append({
                "level": "info",
                "code": "profit_outpacing_revenue",
                "message": f"净利润同比 {profit_yoy:+.1f}%，显著快于营收 {revenue_yoy:+.1f}%",
                "detail": {"revenue_yoy": revenue_yoy, "net_profit_yoy": profit_yoy},
            })

    if revenue_yoy is not None and revenue_yoy <= REVENUE_DECLINE_PCT:
        flags.append({
            "level": "warning",
            "code": "revenue_decline",
            "message": f"营收同比 {revenue_yoy:+.1f}%，处于下滑状态",
            "detail": {"revenue_yoy": revenue_yoy},
        })

    # 单期现金流可能受季节性影响，连续两期才提示。
    # 银行的经营现金流由存款/贷款变动驱动，和净利润没有可比性，不报这条。
    if not _is_financial(latest):
        streak = _trailing_streak(
            ordered, "ocf_to_net_profit", lambda v: v < OCF_RATIO_WARN
        )
        if streak >= 2:
            ratio = latest.get("ocf_to_net_profit")
            flags.append({
                "level": "warning",
                "code": "weak_operating_cash_flow",
                "message": (
                    f"经营现金流连续 {streak} 期低于净利润的 {OCF_RATIO_WARN * 100:.0f}%"
                    f"（最近一期只有 {ratio * 100:.0f}%）"
                ),
                "detail": {"periods": streak, "ratio": ratio},
            })

    debt = latest.get("debt_ratio")
    debt_last_year = _value(last_year, "debt_ratio")
    if debt is not None and debt_last_year is not None and debt - debt_last_year >= DEBT_RATIO_RISE_PP:
        flags.append({
            "level": "warning",
            "code": "debt_ratio_rise",
            "message": f"资产负债率 {debt:.1f}%，同比上升 {debt - debt_last_year:.1f}pp",
            "detail": {"value": debt, "previous": debt_last_year},
        })

    cash, borrowings = latest.get("cash"), latest.get("short_term_borrowings")
    if cash is not None and borrowings is not None and borrowings > 0 and cash < borrowings:
        flags.append({
            "level": "warning",
            "code": "cash_short_of_short_term_debt",
            "message": f"货币资金 {_yi(cash)}，不足以覆盖短期借款 {_yi(borrowings)}",
            "detail": {"cash": cash, "short_term_borrowings": borrowings},
        })

    net_debt = _net_debt(latest)
    if net_debt is not None and net_debt < 0:
        flags.append({
            "level": "info",
            "code": "net_cash_position",
            "message": f"净现金状态，净负债 {_yi(net_debt)}",
            "detail": {"net_debt": net_debt},
        })

    momentum = _momentum(trend)
    if momentum is not None:
        flags.append(momentum)

    return flags


def _momentum(trend: list[dict]) -> dict | None:
    """单季营收增速连续加快 / 放缓，标注拐点。"""
    values = [
        row["revenue_sq_yoy"]
        for row in trend
        if row.get("revenue_sq_yoy") is not None
    ]
    if len(values) < 3:
        return None

    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    if diffs[-1] == 0:
        return None
    rising = diffs[-1] > 0

    streak = 0
    for diff in reversed(diffs):
        if diff == 0 or (diff > 0) != rising:
            break
        streak += 1
    if streak < 2:
        return None

    window = values[-(streak + 1):]
    return {
        "level": "info",
        "code": "revenue_momentum",
        "message": (
            f"单季营收同比增速连续 {streak} 个季度"
            + ("加快" if rising else "放缓")
            + "（" + " → ".join(f"{value:+.1f}%" for value in window) + "）"
        ),
        "detail": {"quarters": streak, "values": window},
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _part(label: str, value, unit: str = "", extra: str | None = None, yi: bool = False) -> str | None:
    """拼一段"标签 + 数值"，缺失时返回 None 由调用方丢弃。"""
    if value is None:
        return None
    if yi:
        text = _yi(value)
    elif unit == "%":
        text = f"{value:.1f}%"
    else:
        text = f"{value:.2f}"
    if extra and extra != "—":
        text += f"（{extra}）"
    return f"{label} {text}"


def _ratio_part(label: str, value) -> str | None:
    if value is None:
        return None
    return f"{label} {value:.2f}"


def _times_part(label: str, value, note: str | None = None) -> str | None:
    if value is None:
        return None
    text = f"{label} {value:.1f}倍"
    return text if not note else f"{text}（{note}）"


def _goodwill_part(value, ratio) -> str | None:
    if value is None:
        return None
    extra = None if ratio is None else f"占净资产 {ratio:.1f}%"
    return _part("商誉", value, yi=True, extra=extra)


def _percent_part(label: str, value) -> str | None:
    """把 0.87 这类比例渲染成百分比。"""
    if value is None:
        return None
    return f"{label} {value * 100:.0f}%"


def _signed_part(label: str, value) -> str | None:
    if value is None:
        return None
    return f"{label} {_yi(value)}"


def _join(*parts: str | None) -> str:
    return "  ".join(part for part in parts if part)


def _yi(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.{digits}f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.{digits}f}万"
    return f"{value:.0f}元"


def _yi_short(value) -> str:
    return "—" if value is None else f"{value / 1e8:.1f}"


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _pp(value) -> str:
    return "—" if value is None else f"{value:+.1f}pp"


# ---------------------------------------------------------------------------
# 基础运算
# ---------------------------------------------------------------------------
def _value(row: dict | None, field: str):
    return None if row is None else row.get(field)


def _by_date(rows: list[dict], report_date: str) -> dict | None:
    for row in rows:
        if row["report_date"] == report_date:
            return row
    return None


def _same_quarter_last_year(report_date: str) -> str:
    current = date.fromisoformat(report_date)
    return f"{current.year - 1}-{current.month:02d}-{current.day:02d}"


def _growth(current, previous) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0


def _trailing_streak(rows: list[dict], field: str, predicate: Callable[[float], bool]) -> int:
    """从最新一期往前数，连续满足条件的期数。"""
    count = 0
    for row in reversed(rows):
        value = row.get(field)
        if value is None or not predicate(value):
            break
        count += 1
    return count


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m stock.finance_snapshot 605499 600036 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            print(format_snapshot(build_snapshot(argument)))
        except FinanceError as error:
            print(f"{argument}: {error}")
        print()
