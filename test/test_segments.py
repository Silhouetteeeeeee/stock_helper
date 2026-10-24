"""主营构成验证：口径选择、占比/同比、子项过滤、提示、文本与本地缓存。

纯函数部分直接喂构造好的行；缓存部分把数据库换到临时目录，并替换
``fetch_segments``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.segments as sg

CODE = "605499"
NAME = "东鹏饮料"
E8 = 1e8


def row(
    item: str,
    income: float | None,
    kind: str = "2",
    date: str = "2026-06-30",
    share: float | None = None,
    gross_margin: float | None = None,
    code: str = CODE,
) -> dict:
    return {
        "code": code,
        "report_date": date,
        "kind": kind,
        "item_name": item,
        "income": income,
        "share": share,
        "cost": None,
        "profit": None,
        "gross_margin": gross_margin,
        "rank": None,
        "fetched_at": sg._now(),
    }


# 605499 的真实结构：2026 H1 五个分部（含新出的茶饮料），2025 H1 四项
ROWS = [
    row("能量饮料", 89.37 * E8, share=71.8, gross_margin=54.7),
    row("电解质饮料", 16.72 * E8, share=13.4, gross_margin=40.6),
    row("茶饮料", 10.58 * E8, share=8.5, gross_margin=23.7),
    row("其他饮料", 7.59 * E8, share=6.1, gross_margin=26.0),
    row("其他(补充)", 0.17 * E8, share=0.1, gross_margin=33.4),
    row("能量饮料", 83.6 * E8, date="2025-06-30", share=77.9),
    row("电解质饮料", 14.9 * E8, date="2025-06-30", share=13.9),
    row("其他饮料", 8.8 * E8, date="2025-06-30", share=8.2),
    row("其他(补充)", 0.1 * E8, date="2025-06-30", share=0.1),
]


def test_basic() -> None:
    segments = sg.segments_from_rows(ROWS, code=CODE, name=NAME)

    assert segments["available"] is True, segments
    assert segments["code"] == CODE and segments["name"] == NAME
    assert segments["as_of"] == "2026-06-30", segments
    assert segments["kind"] == "2" and segments["kind_label"] == "产品", segments
    assert segments["previous_date"] == "2025-06-30", segments
    assert segments["count"] == 5, segments

    names = [item["name"] for item in segments["items"]]
    assert names == ["能量饮料", "电解质饮料", "茶饮料", "其他饮料", "其他(补充)"], names

    energy = segments["items"][0]
    assert round(energy["share"], 1) == 71.8, energy  # 占比按分部合计重算
    assert round(energy["yoy"], 1) == 6.9, energy
    assert energy["gross_margin"] == 54.7, energy
    # 两期占比都按"分部收入 / 全部业务分部收入"重算，才可比
    assert round(energy["share_delta"], 1) == -6.0, energy

    electrolyte = segments["items"][1]
    assert round(electrolyte["yoy"], 1) == 12.2, electrolyte

    # 2025 H1 还没有茶饮料这个分部，同比留空而不是拿别的项凑
    tea = segments["items"][2]
    assert tea["yoy"] is None and tea["share_delta"] is None, tea

    # "其他(补充)"是汇总项，但不影响算出真实占比
    other = segments["items"][4]
    assert other["is_other"] is True, other
    assert segments["items"][3]["is_other"] is False, segments["items"][3]
    assert "其他饮料" not in ("其他", "其他业务")  # 只是确认名字不是通用占位

    # 金额合计应回到营收 124.4 亿附近
    assert round(segments["total_income"] / E8, 1) == 124.4, segments["total_income"]


def test_kind_pick() -> None:
    both = ROWS + [
        row("华东", 60 * E8, kind="3", share=48.0),
        row("华南", 40 * E8, kind="3", share=32.0),
    ]
    # 同一天有产品也有地区：优先产品口径
    assert sg.segments_from_rows(both)["kind"] == "2"

    # 只有地区数据时回退，标签跟着变
    region_only = [item for item in both if item["kind"] == "3"]
    region = sg.segments_from_rows(region_only)
    assert region["kind"] == "3" and region["items"][0]["name"] == "华东", region
    assert region["kind_label"] == "地区", region

    # 明确指定口径时按指定的来
    product_by_kind = sg.segments_from_rows(both, kind="3")
    assert product_by_kind["items"][0]["name"] == "华东", product_by_kind

    # 指定的口径完全没数据时回退默认顺序
    fallback = sg.segments_from_rows(ROWS, kind="1")
    assert fallback["kind"] == "2", fallback


def test_latest_slice() -> None:
    # 最新一期只有空行（没有收入）时，退回上一期
    rows = ROWS + [row("能量饮料", None, date="2026-09-30")]
    segments = sg.segments_from_rows(rows)
    assert segments["as_of"] == "2026-06-30", segments

    # 子项（"其中:xxx"）会重复计收入，既不进列表也不进合计
    zj = [
        row("贸易精炼等其他销售收入", 600 * E8),
        row("其他主营业务", 300 * E8),
        row("冶炼产铜", 100 * E8),
        row("其中:铜精矿", 80 * E8),
        row("内部销售抵销数", -200 * E8),
    ]
    segments = sg.segments_from_rows(zj)
    names = [item["name"] for item in segments["items"]]
    assert "其中:铜精矿" not in names, names
    assert round(segments["items"][0]["share"], 1) == 75.0, segments["items"]
    assert round(segments["total_income"] / E8, 1) == 800.0, segments["total_income"]
    # 抵销行保留在合计里，但不适合当"主营"代表
    offset = [item for item in segments["items"] if "抵销" in item["name"]][0]
    assert offset["is_other"] is True, offset
    assert offset["share"] < 0, offset

    # 全是子项时算不出来，明确返回不可用
    sub_only = sg.segments_from_rows([row("其中:铜精矿", 10 * E8)])
    assert sub_only["available"] is False, sub_only


def test_flags() -> None:
    concentrated = sg.segments_from_rows([
        row("单一业务", 950 * E8),
        row("其他业务", 50 * E8),
    ])
    codes = [flag["code"] for flag in concentrated["flags"]]
    assert "segment_concentrated" in codes, concentrated["flags"]
    assert "95%" in concentrated["flags"][0]["message"], concentrated["flags"]

    declining = sg.segments_from_rows([
        row("主业", 80 * E8),
        row("副业", 20 * E8),
        row("主业", 100 * E8, date="2025-06-30"),
        row("副业", 20 * E8, date="2025-06-30"),
    ])
    decline = [flag for flag in declining["flags"] if flag["code"] == "segment_decline"]
    assert decline and decline[0]["level"] == "warning", declining["flags"]
    assert "-20.0%" in decline[0]["message"], decline[0]

    growing = sg.segments_from_rows([
        row("主业", 80 * E8),
        row("新业务", 20 * E8),
        row("主业", 76 * E8, date="2025-06-30"),
        row("新业务", 10 * E8, date="2025-06-30"),
    ])
    growth = [flag for flag in growing["flags"] if flag["code"] == "segment_growth"]
    assert growth and growth[0]["level"] == "info", growing["flags"]
    assert "+100.0%" in growth[0]["message"], growth[0]

    # 从零起步的小分部即使翻倍也不报喜：占比不到 5%
    tiny = sg.segments_from_rows([
        row("主业", 99 * E8),
        row("新业务", 1 * E8),
        row("主业", 99 * E8, date="2025-06-30"),
        row("新业务", 0.1 * E8, date="2025-06-30"),
    ])
    assert not any(flag["code"] == "segment_growth" for flag in tiny["flags"]), tiny["flags"]

    stale = sg.segments_from_rows(ROWS, stale=True)
    stale_flags = [flag for flag in stale["flags"] if flag["code"] == "stale_data"]
    assert stale_flags and "本地缓存" in stale_flags[0]["message"], stale["flags"]

    # 正常公司不产生提示
    assert sg.segments_from_rows(ROWS)["flags"] == [], sg.segments_from_rows(ROWS)["flags"]


def test_format() -> None:
    segments = sg.segments_from_rows(ROWS, code=CODE, name=NAME)
    text = sg.format_segments(segments)
    assert text.startswith("东鹏饮料(605499) · 主营构成 2026-06-30（按产品）"), text
    assert "能量饮料 89.4亿，占比 71.8%（同比 +6.9%），毛利率 54.7%" in text, text
    assert "茶饮料 10.6亿，占比 8.5%，毛利率 23.7%" in text, text
    assert "[异常]" not in text and "[关注]" not in text, text

    # limit 控制条数；None 表示全列
    assert len(sg.format_items(segments, 2)) == 2, sg.format_items(segments, 2)
    assert len(sg.format_items(segments, None)) == 5, sg.format_items(segments, None)

    # 空数据给出可读占位
    empty = sg.segments_from_rows([], code=CODE, name=NAME)
    assert empty["available"] is False, empty
    assert "暂无主营构成数据" in empty["summary"], empty
    assert "暂无主营构成数据" in sg.format_segments(empty), empty


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="segments_test_")
    sg.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}

    def fake_fetch(code: str) -> list[dict]:
        calls["fetch"] += 1
        return [dict(item, code=code) for item in ROWS]

    sg.fetch_segments = fake_fetch  # type: ignore[assignment]

    first = sg.ensure_segments(CODE)
    assert calls["fetch"] == 1 and len(first["rows"]) == len(ROWS), (calls, first)
    assert first["stale"] is False

    # 缓存一天内不重复抓取
    second = sg.ensure_segments(CODE)
    assert calls["fetch"] == 1 and len(second["rows"]) == len(ROWS), calls

    # force 强制刷新
    sg.ensure_segments(CODE, force=True)
    assert calls["fetch"] == 2, calls

    # 缓存过期后重新抓取
    original_age = sg._age_hours
    sg._age_hours = lambda rows: 99.0  # type: ignore[assignment]
    sg.ensure_segments(CODE)
    assert calls["fetch"] == 3, calls
    sg._age_hours = original_age  # type: ignore[assignment]

    # 抓取失败但本地有缓存：返回旧数据并标记 stale
    def broken(code: str) -> list[dict]:
        raise sg.SegmentError("接口超时")

    sg.fetch_segments = broken  # type: ignore[assignment]
    # 缓存还是新的，只有强制刷新才会走网络；失败后回退到旧数据
    stale = sg.ensure_segments(CODE, force=True)
    assert stale["stale"] is True and len(stale["rows"]) == len(ROWS), stale

    # 没有缓存时只能报错
    try:
        sg.ensure_segments("600000")
    except sg.SegmentError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 SegmentError")

    # 空代码直接拒绝，不落到网络
    try:
        sg.ensure_segments("")
    except sg.SegmentError:
        pass
    else:
        raise AssertionError("空代码应该抛 SegmentError")


def test_helpers() -> None:
    assert sg._previous_year("2026-06-30") == "2025-06-30"
    assert sg._previous_year("2026-12-31") == "2025-12-31"
    assert sg._previous_year("bad") == ""
    assert round(sg._percent("0.718"), 6) == 71.8
    assert sg._percent(None) is None
    assert sg._percent("-") is None
    assert sg._date_text("2026-06-30 00:00:00") == "2026-06-30"
    assert sg._date_text("-") is None
    assert sg._yi(None) == "—"
    assert sg._yi(89.37 * E8) == "89.4亿"


def main() -> None:
    test_basic()
    test_kind_pick()
    test_latest_slice()
    test_flags()
    test_format()
    test_cache()
    test_helpers()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
