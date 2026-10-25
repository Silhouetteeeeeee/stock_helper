"""估值算术：全部用 Decimal，纯函数，不访问数据层。

工具负责取数与回显假设，这里只负责算。这样模型不用心算，也不会把
浮点误差带进情景结果。所有函数只接受数字或数字字符串，缺项与非有限
值统一抛 ``ValuationInputError``，由工具折成给模型的观察结果。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

PASS_PCT = Decimal("1")
WARN_PCT = Decimal("5")
MIN_SPREAD_PCT = Decimal("5")


class ValuationInputError(ValueError):
    """估值输入缺项、非数字或假设在数学上不成立。"""


def _decimal(value, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValuationInputError(f"{label}缺失")
    if isinstance(value, str) and not value.strip():
        raise ValuationInputError(f"{label}缺失")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValuationInputError(f"{label}不是数字：{value!r}") from error
    if not number.is_finite():
        raise ValuationInputError(f"{label}不是有限数字：{value!r}")
    return number


def _optional(value, label: str) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _decimal(value, label)


def compare(name: str, reported, implied) -> dict:
    """比较报告值与财报反推值；1% 内通过，1%–5% 提示，超过 5% 打回。

    返回的 ``diff_pct`` 是带符号的 ``(implied - reported) / reported``。
    倍数类指标的报告值或反推值非正时直接跳过：亏损公司的 PE、负净资产
    的 PB 都没有可比意义，不能拿来做偏差判断。
    """
    reported_value = _optional(reported, f"{name} 报告值")
    implied_value = _optional(implied, f"{name} 计算值")

    if reported_value is None or implied_value is None:
        return _result(name, reported_value, implied_value, None, "skip", "缺少可比数据")
    if reported_value <= 0:
        return _result(
            name, reported_value, implied_value, None, "skip",
            "报告值非正，倍数没有意义",
        )
    if implied_value <= 0:
        return _result(
            name, reported_value, implied_value, None, "skip",
            "财报反推值非正，倍数没有意义",
        )

    diff = (implied_value - reported_value) / reported_value * 100
    absolute = abs(diff)
    if absolute <= PASS_PCT:
        status, note = "pass", "差异 ≤ 1%，口径一致"
    elif absolute <= WARN_PCT:
        status, note = "warn", "差异 1%–5%，注意 TTM/MRQ 口径或财报同步时点"
    else:
        status, note = "fail", "差异 > 5%，引用前先核对原始财报与估值口径"
    return _result(name, reported_value, implied_value, diff, status, note)


def _result(name, reported, implied, diff, status: str, note: str) -> dict:
    return {
        "name": name,
        "reported": None if reported is None else float(reported),
        "implied": None if implied is None else float(implied),
        "diff_pct": None if diff is None else round(float(diff), 2),
        "status": status,
        "note": note,
    }


def fcf_yield(fcf, market_cap) -> Decimal | None:
    """自由现金流收益率（百分数）；市值非正或缺数据时返回 None。"""
    fcf_value = _optional(fcf, "自由现金流")
    cap_value = _optional(market_cap, "市值")
    if fcf_value is None or cap_value is None or cap_value <= 0:
        return None
    return fcf_value / cap_value * 100


def scenario(base_value, growth_pct, exit_pe, years, price=None) -> dict:
    """一档情景的算术：基准值 ×(1+g)^年数 ×退出 PE。

    ``base_value`` 是每股收益或每股自由现金流，``growth_pct`` 与
    ``exit_pe`` 是百分数/倍数。基准值非正时拒绝计算：亏损公司的
    "EPS 增长"没有意义，不能靠乘方把负值算成正数。
    """
    base = _decimal(base_value, "基准每股价值")
    growth = _decimal(growth_pct, "增速")
    pe = _decimal(exit_pe, "退出 PE")
    try:
        periods = int(years)
    except (TypeError, ValueError) as error:
        raise ValuationInputError(f"情景年数不是整数：{years!r}") from error

    if base <= 0:
        raise ValuationInputError("基准每股价值必须大于 0")
    if growth <= -100:
        raise ValuationInputError("增速不能小于等于 -100%")
    if pe <= 0:
        raise ValuationInputError("退出 PE 必须大于 0")
    if periods < 1 or periods > 10:
        raise ValuationInputError("情景年数需要在 1–10 之间")

    price_value = _optional(price, "股价")
    if price_value is not None and price_value <= 0:
        raise ValuationInputError("股价必须大于 0")

    future_value = base * (Decimal("1") + growth / 100) ** periods
    future_price = future_value * pe
    upside = None
    if price_value is not None:
        upside = (future_price / price_value - 1) * 100
    return {
        "future_value": future_value,
        "future_price": future_price,
        "upside_pct": upside,
    }


def terminal_pe(roic_pct, g_pct, r_pct) -> dict:
    """永续增长模型的终值 PE：``(1 - g/ROIC) / (r - g)``。

    只检查假设是否自洽，不给目标价。``r - g < 5pp`` 仍可计算，但标记
    warning：分母太窄时终值对参数极其敏感。``ROIC ≤ 0``、``g ≥ ROIC``、
    ``r ≤ g`` 属于数学上不成立，返回 ``valid=False`` 且不给 PE。
    """
    roic = _decimal(roic_pct, "ROIC")
    growth = _decimal(g_pct, "永续增速")
    discount = _decimal(r_pct, "折现率")

    checks: list[dict] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    valid = True
    if roic <= 0:
        add("ROIC", "fail", "ROIC 必须大于 0，否则留存率模型没有意义")
        valid = False
    if growth >= roic:
        add("g < ROIC", "fail", "永续增速不低于 ROIC，留存率为负")
        valid = False
    if discount <= growth:
        add("r > g", "fail", "折现率必须高于永续增速，否则分母不为正")
        valid = False

    if valid and discount - growth < MIN_SPREAD_PCT:
        add("r - g", "warn", "r-g 小于 5 个百分点，终值对参数非常敏感")
    if growth < 0:
        add("永续增速", "warn", "永续负增长不常见，确认假设")

    pe = None
    payout = None
    if valid:
        payout = (Decimal("1") - growth / roic) * 100
        pe = (Decimal("1") - growth / roic) / ((discount - growth) / 100)
        if pe <= 0:
            add("终值 PE", "fail", "算出的终值 PE 非正，假设不成立")
            valid = False
            pe = None
        elif pe > 50:
            add("终值 PE", "warn", "终值 PE 高于 50 倍，确认 ROIC 与 g 的假设")
        else:
            add("终值 PE", "pass", "永续增长模型可用")

    status = "fail" if not valid else (
        "warn" if any(item["status"] == "warn" for item in checks) else "pass"
    )
    return {
        "terminal_pe": pe,
        "payout_ratio_pct": payout,
        "valid": valid,
        "status": status,
        "checks": checks,
    }
