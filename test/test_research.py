"""机构调研验证：同场归并、机构去重、类型统计与本地缓存。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.research as research

CODE = "002230"
NAME = "科大讯飞"
TODAY = "2026-10-25"


def _row(
    date: str,
    notice: str,
    institution: str,
    org_type: str | None,
    *,
    place: str | None = "合肥总部",
    ways: str | None = "业绩说明会,全景路演",
    receptionists: str | None = "董事长 刘庆峰,副总裁、董事会秘书 江涛",
    total: int | None = None,
    content: str | None = None,
) -> dict:
    return {
        "SECURITY_CODE": CODE,
        "SECURITY_NAME_ABBR": NAME,
        "NOTICE_DATE": f"{notice} 00:00:00",
        "RECEIVE_START_DATE": f"{date} 00:00:00",
        "RECEIVE_PLACE": place,
        "RECEIVE_WAY_EXPLAIN": ways,
        "RECEPTIONIST": receptionists,
        "RECEIVE_OBJECT": institution,
        "ORG_TYPE": org_type,
        "INVESTIGATORS": "张三",
        "SUM": total,
        "NUM": 1,
        "CONTENT": content,
    }


ROWS = [
    _row(
        "2026-08-21", "2026-08-23", "北工投资", "其它", total=43,
        content="投资者关系活动主要内容介绍\n一、业绩解读。\n问：国产算力进展？答：逐步追赶。",
    ),
    _row("2026-08-21", "2026-08-23", "国海证券", "证券公司", total=43),
    _row("2026-08-21", "2026-08-23", "广发证券", "证券公司", total=43),
    # 同一机构重复出现，不应重复计数
    _row("2026-08-21", "2026-08-23", "国海证券", "证券公司", total=43),
    _row(
        "2026-02-10", "2026-02-12", "兴全基金", "基金管理公司",
        place=None, ways="电话会议", receptionists="总裁 吴晓如", total=5,
    ),
    _row(
        "2026-02-10", "2026-02-12", "景林资产", "投资公司",
        place=None, ways="电话会议", receptionists="总裁 吴晓如", total=5,
    ),
    # 三年窗口外
    _row(
        "2022-11-01", "2022-11-03", "某券商", "证券公司",
        total=2, ways="现场参观",
    ),
]


def test_from_rows() -> None:
    data = research.research_from_rows(ROWS, CODE, years=3, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-08-23", data
    assert data["window_start"] == "2024-01-01", data
    assert data["window_end"] == "2026-10-25", data
    assert data["years"] == 3, data
    assert data["has_research"] is True, data

    counts = data["counts"]
    assert counts["events"] == 2, counts
    assert counts["institution_visits"] == 48, counts
    assert counts["latest_date"] == "2026-08-21", counts
    assert counts["latest_notice_date"] == "2026-08-23", counts
    assert counts["latest_institution_count"] == 43, counts
    assert counts["latest_place"] == "合肥总部", counts
    assert counts["events_12m"] == 2, counts
    assert counts["institution_visits_12m"] == 48, counts

    latest = data["latest"]
    assert latest["date"] == "2026-08-21", latest
    assert latest["notice_date"] == "2026-08-23", latest
    assert latest["institution_count"] == 43, latest
    assert latest["disclosed_count"] == 3, latest
    assert latest["institutions"] == [
        "北工投资", "国海证券", "广发证券",
    ], latest
    assert latest["ways"] == ["业绩说明会", "全景路演"], latest
    assert latest["receptionists"] == [
        "董事长 刘庆峰", "副总裁、董事会秘书 江涛",
    ], latest
    assert latest["org_types"] == [
        {"type": "证券公司", "count": 2},
        {"type": "其它", "count": 1},
    ], latest
    assert "国产算力" in (latest["content"] or ""), latest

    assert [event["date"] for event in data["events"]] == [
        "2026-08-21", "2026-02-10",
    ], data["events"]

    annual = {item["year"]: item for item in data["annual"]}
    assert sorted(annual) == [2026], annual
    assert annual[2026]["events"] == 2, annual[2026]
    assert annual[2026]["institution_visits"] == 48, annual[2026]

    types = {item["type"]: item["count"] for item in data["org_types"]}
    assert types == {
        "证券公司": 2, "其它": 1, "基金管理公司": 1, "投资公司": 1,
    }, types

    notes = data["notes"]
    assert any("不代表承诺" in note for note in notes), notes
    assert any("不等于基本面" in note for note in notes), notes


def test_default_and_window() -> None:
    default = research.research_from_rows(ROWS, CODE, today=TODAY)
    assert default["years"] == 3, default
    assert default["window_start"] == "2024-01-01", default
    assert default["counts"]["events"] == 2, default["counts"]

    wide = research.research_from_rows(ROWS, CODE, years=99, today=TODAY)
    assert wide["years"] == 10, wide
    assert wide["counts"]["events"] == 3, wide["counts"]

    bad = research.research_from_rows(ROWS, CODE, years="bad", today=TODAY)
    assert bad["years"] == 3, bad


def test_empty_and_errors() -> None:
    data = research.research_from_rows([], CODE, today=TODAY)
    assert data["available"] is True, data
    assert data["has_research"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["events"] == [] and data["annual"] == [], data
    assert data["counts"]["events"] == 0, data["counts"]
    assert any("没有取到" in note for note in data["notes"]), data["notes"]

    try:
        research.research_from_rows("bad", CODE)  # type: ignore[arg-type]
    except research.ResearchError:
        pass
    else:
        raise AssertionError("非列表输入应抛 ResearchError")

    try:
        research.fetch_research("")
    except research.ResearchError:
        pass
    else:
        raise AssertionError("空代码应抛 ResearchError")


def test_window_empty() -> None:
    old = [
        _row(
            "2022-11-01", "2022-11-03", "某券商", "证券公司",
            total=2, ways="现场参观",
        )
    ]
    data = research.research_from_rows(old, CODE, years=3, today=TODAY)
    assert data["as_of"] == "2022-11-03", data
    assert data["latest"]["date"] == "2022-11-01", data
    assert data["counts"]["events"] == 0, data["counts"]
    assert data["events"] == [], data
    assert any("近3年" in note for note in data["notes"]), data["notes"]


def test_fetch_error() -> None:
    original = research._get_page
    research._get_page = lambda code, start, page: {  # type: ignore[assignment]
        "success": False, "message": "接口超时",
    }
    try:
        research.fetch_research(CODE)
    except research.ResearchError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 ResearchError")

    research._get_page = lambda code, start, page: {  # type: ignore[assignment]
        "success": False, "message": "返回数据为空",
    }
    try:
        empty = research.fetch_research(CODE)
        assert empty["events"] == [], empty
    finally:
        research._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = research.research_from_rows(ROWS, CODE, years=3, today=TODAY)
    text = research.format_research(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "机构调研近3年" in text, text
    assert "近3年：2 场 / 机构接待 48 家次 / 最近 2026-08-21（43 家）" in text, text
    assert (
        "最近：2026-08-21 合肥总部 / 业绩说明会、全景路演 / "
        "接待：董事长 刘庆峰、副总裁、董事会秘书 江涛"
    ) in text, text
    assert "机构类型：证券公司 2 家次" in text, text
    assert "参与机构：北工投资、国海证券、广发证券 等 3 家" not in text, text
    assert "参与机构：北工投资、国海证券、广发证券" in text, text
    assert "纪要：" in text and "国产算力" in text, text
    assert "数据来源：" in text, text

    empty = research.research_from_rows([], CODE, today=TODAY)
    empty_text = research.format_research(empty, code=CODE, name=NAME)
    assert "暂无已披露的机构调研记录" in empty_text, empty_text

    old = research.research_from_rows(
        [_row("2022-11-01", "2022-11-03", "某券商", "证券公司",
              total=2, ways="现场参观")],
        CODE, years=3, today=TODAY,
    )
    old_text = research.format_research(old, code=CODE, name=NAME)
    assert "近3年没有已披露的机构调研" in old_text, old_text
    assert "最近一次为 2022-11-01" in old_text, old_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="research_cache_")
    research.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item
        for row in ROWS
        for item in [research._normalize_row(row)]
        if item is not None
    ]
    calls = {"fetch": 0}

    def fake_fetch(code: str, years: int = 3) -> dict:
        calls["fetch"] += 1
        return {
            "code": code, "years": years, "events": events,
            "count": len(events), "truncated": False,
        }

    research.fetch_research = fake_fetch  # type: ignore[assignment]

    first = research.ensure_research(CODE, years=3)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(events), first
    assert first["data"]["fetched_at"], first

    second = research.ensure_research(CODE, years=3)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    # 不同回看年数使用各自的缓存
    research.ensure_research(CODE, years=5)
    assert calls["fetch"] == 2, calls

    research.ensure_research(CODE, years=3, force=True)
    assert calls["fetch"] == 3, calls

    original_age = research._age_hours
    research._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    research.ensure_research(CODE, years=3)
    assert calls["fetch"] == 4, calls
    research._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str, years: int = 3) -> dict:
        raise research.ResearchError("接口超时")

    research.fetch_research = broken  # type: ignore[assignment]
    stale = research.ensure_research(CODE, years=3, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(events), stale

    try:
        research.ensure_research("600000", years=3)
    except research.ResearchError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 ResearchError")

    try:
        research.ensure_research("")
    except research.ResearchError:
        pass
    else:
        raise AssertionError("空代码应该抛 ResearchError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="research_build_")
    research.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item
        for row in ROWS
        for item in [research._normalize_row(row)]
        if item is not None
    ]
    research.fetch_research = lambda code, years=3: {  # type: ignore[assignment]
        "code": code, "years": years, "events": events,
        "count": len(events), "truncated": False,
    }

    class FakeStock:
        name = NAME

    original_stock = research.get_stock
    research.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = research.build_research(CODE, years=3, today=TODAY)
    finally:
        research.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["events"] == 2, data["counts"]
    assert data["sources"] == {research.SOURCE_LABEL: "2026-08-23"}, data
    assert data["url"] == "https://data.eastmoney.com/jgdy/", data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]
    assert data["truncated"] is False, data


def test_page_url() -> None:
    assert research.PAGE_URL == "https://data.eastmoney.com/jgdy/"


def main() -> None:
    test_from_rows()
    test_default_and_window()
    test_empty_and_errors()
    test_window_empty()
    test_fetch_error()
    test_format()
    test_cache()
    test_build()
    test_page_url()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
