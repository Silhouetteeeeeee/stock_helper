"""股东结构验证：十大股东、实控人、户数序列与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_shareholders``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.shareholders as sh

CODE = "605499"
NAME = "东鹏饮料"

PAYLOAD = {
    "sdgd": [
        {
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 1,
            "HOLDER_NAME": "林木勤", "SHARES_TYPE": "流通A股,限售流通A股",
            "HOLD_NUM": 343422644.12, "HOLD_NUM_RATIO": 45.8235,
            "HOLD_NUM_CHANGE": "不变", "CHANGE_RATIO": None,
        },
        {
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 2,
            "HOLDER_NAME": "香港中央结算有限公司", "SHARES_TYPE": "流通A股",
            "HOLD_NUM": 59360000.0, "HOLD_NUM_RATIO": 7.9237,
            "HOLD_NUM_CHANGE": 1234567, "CHANGE_RATIO": 2.12,
        },
        {
            # 更早一期应被过滤掉
            "END_DATE": "2025-12-31 00:00:00", "HOLDER_RANK": 1,
            "HOLDER_NAME": "旧股东", "SHARES_TYPE": "流通A股",
            "HOLD_NUM": 1.0, "HOLD_NUM_RATIO": 1.0,
            "HOLD_NUM_CHANGE": "", "CHANGE_RATIO": None,
        },
    ],
    "sdltgd": [
        {
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 1,
            "HOLDER_NAME": "林木勤", "HOLDER_TYPE": "个人",
            "HOLD_NUM": 336435820.81, "FREE_HOLDNUM_RATIO": 45.0,
            "HOLD_NUM_CHANGE": "新进", "CHANGE_RATIO": None,
        },
        {
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_RANK": 2,
            "HOLDER_NAME": "香港中央结算有限公司", "HOLDER_TYPE": "一般企业",
            "HOLD_NUM": 59360000.0, "FREE_HOLDNUM_RATIO": 7.9,
            "HOLD_NUM_CHANGE": -500000, "CHANGE_RATIO": -0.84,
        },
    ],
    "gdrs": [
        {
            "END_DATE": "2026-06-30 00:00:00", "HOLDER_TOTAL_NUM": 46264,
            "TOTAL_NUM_RATIO": 52.42, "AVG_FREE_SHARES": 15304,
            "AVG_FREESHARES_RATIO": -34.4, "HOLD_FOCUS": "非常分散",
            "HOLD_RATIO_TOTAL": 66.5, "FREEHOLD_RATIO_TOTAL": 55.2,
        },
        {
            "END_DATE": "2025-12-31 00:00:00", "HOLDER_TOTAL_NUM": 30000,
            "TOTAL_NUM_RATIO": -5.0, "AVG_FREE_SHARES": 20000,
            "AVG_FREESHARES_RATIO": 6.1, "HOLD_FOCUS": "分散",
            "HOLD_RATIO_TOTAL": 64.0, "FREEHOLD_RATIO_TOTAL": 52.0,
        },
        {
            "END_DATE": "2026-03-31 00:00:00", "HOLDER_TOTAL_NUM": 30364,
            "TOTAL_NUM_RATIO": 1.21, "AVG_FREE_SHARES": 19800,
            "AVG_FREESHARES_RATIO": -1.2, "HOLD_FOCUS": "分散",
            "HOLD_RATIO_TOTAL": 65.2, "FREEHOLD_RATIO_TOTAL": 53.4,
        },
    ],
    "sjkzr": [{"HOLDER_NAME": "林木勤", "HOLD_RATIO": 45.8235}],
}


def test_payload() -> None:
    data = sh.shareholders_from_payload(PAYLOAD, CODE)

    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-06-30", data
    assert data["controller"] == {"name": "林木勤", "ratio": 45.8235}, data

    # 只保留最新一期，并按名次排序
    assert [row["name"] for row in data["top10"]] == [
        "林木勤", "香港中央结算有限公司"
    ], data["top10"]
    assert data["top10"][0]["change"] is None, data["top10"][0]
    assert data["top10"][0]["change_text"] == "不变", data["top10"][0]
    assert data["top10"][1]["change"] == 1234567, data["top10"][1]
    assert data["top10"][1]["change_text"] is None, data["top10"][1]

    # 流通股东用 FREE_HOLDNUM_RATIO，并带上股东类型
    free = data["free_float_top10"]
    assert free[0]["ratio"] == 45.0, free[0]
    assert free[0]["holder_type"] == "个人", free[0]
    assert free[0]["change_text"] == "新进", free[0]
    assert free[1]["change"] == -500000, free[1]

    # 户数按时间升序，最新一期在最后
    history = data["holder_count"]["history"]
    assert [row["period"] for row in history] == [
        "2025-12-31", "2026-03-31", "2026-06-30"
    ], history
    assert data["holder_count"]["latest"] == history[-1], data
    assert history[-1]["holders"] == 46264, history[-1]
    assert history[-1]["holders_change_pct"] == 52.42, history[-1]
    assert data["counts"] == {
        "top10": 2, "free_float_top10": 2, "holder_periods": 3,
    }, data["counts"]


def test_periods() -> None:
    data = sh.shareholders_from_payload(PAYLOAD, CODE, periods=2)
    assert [row["period"] for row in data["holder_count"]["history"]] == [
        "2026-03-31", "2026-06-30"
    ], data["holder_count"]
    assert data["counts"]["holder_periods"] == 2, data["counts"]

    # 非法期数回落到默认值，不把缓存改坏
    sliced = sh._apply_periods(data, periods=0)
    assert sliced["counts"]["holder_periods"] == 1, sliced["counts"]
    assert len(data["holder_count"]["history"]) == 2, data["holder_count"]
    assert sh._apply_periods(data, periods="x")["counts"]["holder_periods"] == 2


def test_errors() -> None:
    try:
        sh.shareholders_from_payload({}, CODE)
    except sh.ShareholderError:
        pass
    else:
        raise AssertionError("没有股东数据时应该抛 ShareholderError")

    try:
        sh.shareholders_from_payload("bad", CODE)  # type: ignore[arg-type]
    except sh.ShareholderError:
        pass
    else:
        raise AssertionError("返回格式异常时应该抛 ShareholderError")


def test_format() -> None:
    data = sh.shareholders_from_payload(PAYLOAD, CODE)
    text = sh.format_shareholders(
        data, code=CODE, name=NAME, as_of=data["as_of"]
    )

    assert text.startswith(f"{NAME}({CODE}) · 股东结构 2026-06-30"), text
    assert "股东 46,264 户，较上期 +52.4%" in text, text
    assert "实控人 林木勤 45.8%" in text, text
    assert "十大股东：林木勤 45.8%（不变）" in text, text
    assert "香港中央结算有限公司 7.9%（+123.5万股）" in text, text
    assert "十大流通股东：林木勤 45.0%（新进）" in text, text
    assert "股东户数：2025-12-31 30,000 户 → 2026-06-30 46,264 户" in text, text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="shareholders_test_")
    sh.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return sh.shareholders_from_payload(PAYLOAD, code, periods=sh.MAX_PERIODS)

    sh.fetch_shareholders = fake_fetch  # type: ignore[assignment]

    first = sh.ensure_shareholders(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["counts"]["holder_periods"] == 3, first
    assert first["data"]["fetched_at"], first

    # 一天内不重复抓取
    second = sh.ensure_shareholders(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    # force 强制刷新
    sh.ensure_shareholders(CODE, force=True)
    assert calls["fetch"] == 2, calls

    # 缓存过期后重新抓取
    original_age = sh._age_hours
    sh._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    sh.ensure_shareholders(CODE)
    assert calls["fetch"] == 3, calls
    sh._age_hours = original_age  # type: ignore[assignment]

    # 抓取失败但本地有缓存：返回旧数据并标记 stale
    def broken(code: str) -> dict:
        raise sh.ShareholderError("接口超时")

    sh.fetch_shareholders = broken  # type: ignore[assignment]
    stale = sh.ensure_shareholders(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["counts"]["top10"] == 2, stale

    # 没有缓存时只能报错
    try:
        sh.ensure_shareholders("600000")
    except sh.ShareholderError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 ShareholderError")

    # 空代码直接拒绝，不落到网络
    try:
        sh.ensure_shareholders("")
    except sh.ShareholderError:
        pass
    else:
        raise AssertionError("空代码应该抛 ShareholderError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="shareholders_build_")
    sh.DB_PATH = os.path.join(tmpdir, "stock.db")

    sh.fetch_shareholders = lambda code: sh.shareholders_from_payload(  # type: ignore[assignment]
        PAYLOAD, code, periods=sh.MAX_PERIODS
    )

    class FakeStock:
        name = NAME

    original_stock = sh.get_stock
    sh.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = sh.build_shareholders(CODE, periods=2)
    finally:
        sh.get_stock = original_stock  # type: ignore[assignment]

    assert data["available"] is True, data
    assert data["name"] == NAME, data
    assert data["as_of"] == "2026-06-30", data
    assert data["counts"]["holder_periods"] == 2, data["counts"]
    assert data["summary"].startswith(f"{NAME}({CODE})"), data
    assert data["sources"] == {sh.SOURCE_LABEL: "2026-06-30"}, data


def main() -> None:
    test_payload()
    test_periods()
    test_errors()
    test_format()
    test_cache()
    test_build()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
