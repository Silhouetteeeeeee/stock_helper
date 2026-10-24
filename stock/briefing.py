"""一页速览：把财报快照和估值摘要压成几行，供界面和研究助手使用。

``finance_snapshot`` 回答"公司赚了多少"，``valuation`` 回答"市场给了多少钱"，
两份摘要加起来还是太长，悬浮窗只有 320 像素宽。这里只留投资者最先要看
的几个数：营收与净利、盈利能力、估值分位、行业位置，加上最多几条提示，
并给出一份纯文本版本供模型直接引用。

* ``build_briefing(code)``   取数（走各自模块的缓存）后生成速览
* ``briefing_from(...)``     纯函数，由已有摘要生成，便于测试
* ``format_briefing(...)``   渲染成纯文本

``build_briefing`` 会访问网络，界面里请放到后台线程调用。
"""

from __future__ import annotations

from .db_service import normalize_stock_code
from .finance_snapshot import DEFAULT_TREND_PERIODS, FinanceError, build_snapshot
from .valuation import ValuationError, build_valuation

MAX_LINES = 5      # 面板上最多显示几行指标


def build_briefing(
    code: str,
    trend_periods: int = DEFAULT_TREND_PERIODS,
    force: bool = False,
) -> dict:
    """取数并生成一页速览。估值取不到时只影响估值相关的那几行。"""
    code = normalize_stock_code(code)
    if not code:
        raise FinanceError("请输入股票代码")

    snapshot = build_snapshot(code, periods=trend_periods, force=force)
    valuation, valuation_error = None, None
    try:
        valuation = build_valuation(code, force=force)
    except ValuationError as error:
        # 估值接口不稳定时财报部分照常展示，不要整块空掉
        valuation_error = str(error)
    return briefing_from(snapshot, valuation, valuation_error=valuation_error)


def briefing_from(
    snapshot: dict | None,
    valuation: dict | None = None,
    valuation_error: str | None = None,
) -> dict:
    """由财报快照与估值摘要生成速览（纯函数）。"""
    snapshot = snapshot or {}
    name = snapshot.get("name") or (valuation or {}).get("name")
    code = snapshot.get("code") or (valuation or {}).get("code")
    lines: list[str] = []
    alerts: list[dict] = []

    if snapshot.get("available"):
        lines.extend(_financial_lines(snapshot))
        alerts.extend(
            {"level": flag.get("level", "info"), "text": flag.get("message", "")}
            for flag in snapshot.get("flags") or []
        )

    if valuation and valuation.get("available"):
        lines.extend(_valuation_lines(valuation))
        alerts.extend(
            {"level": flag.get("level", "info"), "text": flag.get("message", "")}
            for flag in valuation.get("flags") or []
        )
    elif valuation_error:
        alerts.append({"level": "info", "text": "估值数据暂不可用"})

    if not lines:
        return {
            "available": False,
            "code": code,
            "name": name,
            "headline": "暂无数据",
            "lines": [],
            "alerts": [],
            "summary": f"{name or '该股票'}：暂无财报与估值数据",
        }

    briefing = {
        "available": True,
        "code": code,
        "name": name,
        "headline": _headline(snapshot, valuation),
        "lines": lines[:MAX_LINES],
        "alerts": alerts,
        "sources": {
            **(snapshot.get("sources") or {}),
            **((valuation or {}).get("sources") or {}),
        },
    }
    briefing["summary"] = format_briefing(briefing)
    return briefing


def format_briefing(briefing: dict) -> str:
    """渲染成纯文本：标题一行、指标几行、提示几条。"""
    if not briefing.get("available"):
        return briefing.get("summary") or "暂无数据"

    title = (
        f"{briefing['name']}({briefing['code']})"
        if briefing.get("name")
        else briefing.get("code") or ""
    )
    lines = [f"{title} · {briefing['headline']}"]
    lines.extend(briefing["lines"])
    if briefing["alerts"]:
        lines.append("提示：")
        for alert in briefing["alerts"]:
            lines.append(f"- [{_LEVEL_LABELS.get(alert['level'], alert['level'])}] {alert['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 指标行
# ---------------------------------------------------------------------------
_LEVEL_LABELS = {"warning": "异常", "info": "关注"}


def _financial_lines(snapshot: dict) -> list[str]:
    metrics = snapshot["metrics"]
    revenue, profit = metrics["revenue"], metrics["net_profit"]
    lines = [
        f"营收 {_yi(revenue['value'])} {_pct(revenue['yoy'])}"
        f" · 净利 {_yi(profit['value'])} {_pct(profit['yoy'])}"
    ]

    margin = metrics["gross_margin"]
    profit_line = _join(
        # 银行、保险没有营业成本科目，毛利率一栏直接不显示
        None if margin["value"] is None
        else f"毛利率 {margin['value']:.1f}%"
        + (f"（{_pp(margin['change_pp'])}）" if margin["change_pp"] is not None else ""),
        None if metrics["roe"]["value"] is None
        else f"ROE {metrics['roe']['value']:.1f}%",
    )
    if profit_line:
        lines.append(profit_line)

    cash = metrics["cash"]
    ratio = cash.get("ocf_to_net_profit")
    cash_line = _join(
        None if cash.get("operating_cash_flow") is None
        else f"经营现金流 {_yi(cash['operating_cash_flow'])}",
        None if ratio is None else f"为净利的 {ratio * 100:.0f}%",
    )
    if cash_line:
        lines.append(cash_line)
    return lines


def _valuation_lines(valuation: dict) -> list[str]:
    pe, pb = valuation["valuation"]["pe_ttm"], valuation["valuation"]["pb_mrq"]
    line = f"PE {_plain(pe)} · PB {_plain(pb)}"
    percentiles = _percentiles(pe, pb)
    if percentiles:
        line += f"（{percentiles}）"
    lines = [line]

    industry = valuation.get("industry") or {}
    if industry.get("median_pe") is not None:
        text = f"{industry.get('board') or '同行业'} {industry['count']} 家"
        text += f" · PE 中值 {industry['median_pe']:.1f}"
        if industry.get("rank_pe"):
            text += f" · 第 {industry['rank_pe']}/{industry['rank_total']}"
        lines.append(text)
    return lines


def _headline(snapshot: dict, valuation: dict | None) -> str:
    parts = []
    if snapshot.get("available"):
        period = snapshot["latest"]["report_date"]
        parts.append(f"财报 {period}")
    if valuation and valuation.get("available"):
        parts.append(f"估值 {valuation['as_of']}")
    return " · ".join(parts) or "暂无数据"


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
def _plain(stat: dict) -> str:
    value = stat.get("value")
    if value is None:
        return "—"
    # 亏损公司的 PE 是负数，直接写"亏损"比写 -5.00 好读
    return "亏损" if value <= 0 else f"{value:.2f}"


def _percentiles(pe: dict, pb: dict) -> str | None:
    """把 PE/PB 分位压成一段，例如 ``近 5 年 0%/34% 分位``（顺序同 PE、PB）。"""
    pe_pct, pb_pct = pe.get("percentile"), pb.get("percentile")
    years = pe.get("years") or pb.get("years")
    if pe_pct is not None and pb_pct is not None:
        return f"近 {years} 年 {pe_pct:.0f}%/{pb_pct:.0f}% 分位"
    if pe_pct is not None:
        return f"近 {years} 年 PE {pe_pct:.0f}% 分位"
    if pb_pct is not None:
        return f"近 {years} 年 PB {pb_pct:.0f}% 分位"
    return None


def _pp(value) -> str:
    return "—" if value is None else f"{value:+.1f}pp"


def _yi(value) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.1f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:.0f}元"


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _join(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part and part != "—")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m stock.briefing 605499 600036 ...")
        raise SystemExit(1)

    for argument in sys.argv[1:]:
        try:
            print(build_briefing(argument)["summary"])
        except FinanceError as error:
            print(f"{argument}: {error}")
        print()
