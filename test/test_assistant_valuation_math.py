"""估值算术验证：Decimal 计算、PE/PB 校验、情景与终值边界。"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.valuation_math import (
    ValuationInputError,
    compare,
    fcf_yield,
    scenario,
    terminal_pe,
)


def test_compare() -> None:
    passed = compare("PE(TTM)", Decimal("10.0"), Decimal("10.05"))
    assert passed["status"] == "pass", passed
    assert passed["diff_pct"] == 0.5, passed

    warning = compare("PE(TTM)", 10.0, 10.3)
    assert warning["status"] == "warn", warning
    assert warning["diff_pct"] == 3.0, warning

    failed = compare("PE(TTM)", 10.0, 11.0)
    assert failed["status"] == "fail", failed
    assert "5%" in failed["note"], failed

    # 缺数据、报告值为 0 或负数时，倍数偏差没有意义
    for reported, implied in ((None, 10.0), (10.0, None), (0, 10.0), (-5, 10.0)):
        skipped = compare("PE(TTM)", reported, implied)
        assert skipped["status"] == "skip", skipped
        assert skipped["diff_pct"] is None, skipped

    try:
        compare("PE(TTM)", "不是数字", 10.0)
    except ValuationInputError:
        pass
    else:
        raise AssertionError("非法输入应抛 ValuationInputError")


def test_fcf_yield() -> None:
    value = fcf_yield(Decimal("3.2"), Decimal("37"))
    assert round(float(value), 2) == 8.65, value
    # 市值缺失或非正时不能算收益率
    assert fcf_yield(None, 37) is None
    assert fcf_yield(3.2, 0) is None
    assert fcf_yield(3.2, -1) is None


def test_scenario() -> None:
    result = scenario(
        Decimal("3"), Decimal("10"), Decimal("15"), 3, price=Decimal("50")
    )
    assert round(float(result["future_value"]), 3) == 3.993, result
    assert round(float(result["future_price"]), 2) == 59.90, result
    assert round(float(result["upside_pct"]), 2) == 19.79, result

    # 没有当前股价时只算价值，不硬造涨跌幅
    bare = scenario(1, 0, 10, 3)
    assert bare["upside_pct"] is None, bare

    # 基准为负、增速跌破 -100%、退出 PE 非正、年数越界都不能算
    for args in (
        (0, 10, 15, 3),
        (-1, 10, 15, 3),
        (3, -100, 15, 3),
        (3, 10, 0, 3),
        (3, 10, 15, 0),
        (3, 10, 15, 11),
    ):
        try:
            scenario(*args)
        except ValuationInputError:
            pass
        else:
            raise AssertionError(f"应当拒绝：{args}")

    try:
        scenario(3, 10, 15, 3, price=0)
    except ValuationInputError:
        pass
    else:
        raise AssertionError("股价为 0 时应拒绝")


def test_terminal_pe() -> None:
    normal = terminal_pe(20, 2, 8)
    assert normal["valid"] is True, normal
    assert normal["status"] == "pass", normal
    assert round(float(normal["terminal_pe"]), 2) == 15.0, normal
    assert round(float(normal["payout_ratio_pct"]), 1) == 90.0, normal
    assert all(check["status"] != "fail" for check in normal["checks"]), normal

    # r-g 只有 4 个百分点：模型还能算，但必须提示终值对参数极敏感
    narrow = terminal_pe(20, 2, 6)
    assert narrow["valid"] is True, narrow
    assert narrow["status"] == "warn", narrow
    assert round(float(narrow["terminal_pe"]), 2) == 22.5, narrow
    assert any(
        check["name"] == "r - g" and check["status"] == "warn"
        for check in narrow["checks"]
    ), narrow

    # 数学上不成立的假设不能产出 PE
    for args in ((0, 2, 8), (2, 2, 8), (20, 2, 2), (20, 8, 2)):
        broken = terminal_pe(*args)
        assert broken["valid"] is False, (args, broken)
        assert broken["terminal_pe"] is None, (args, broken)
        assert broken["status"] == "fail", (args, broken)

    # 永续负增长可以算，但要提示不常见
    shrinking = terminal_pe(20, -1, 8)
    assert shrinking["valid"] is True, shrinking
    assert any(
        check["status"] == "warn" for check in shrinking["checks"]
    ), shrinking


def main() -> None:
    test_compare()
    test_fcf_yield()
    test_scenario()
    test_terminal_pe()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
