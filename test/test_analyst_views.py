"""研报观点验证：评级分布、目标价、一致预期与本地缓存。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.analyst_views as views

CODE = "002230"
NAME = "科大讯飞"
TODAY = "2026-10-25"


def _row(
    publish: str,
    org: str,
    rating: str,
    *,
    last_rating: str | None = None,
    title: str = "深度报告",
    target: float | None = None,
    eps_this: float | None = None,
    eps_next: float | None = None,
    eps_next2: float | None = None,
    pe: float | None = None,
    researcher: str = "张三",
    info: str | None = None,
) -> dict:
    return {
        "publishDate": f"{publish} 00:00:00.000",
        "orgSName": org,
        "researcher": researcher,
        "title": title,
        "emRatingName": rating,
        "lastEmRatingName": last_rating if last_rating is not None else rating,
        "indvAimPriceT": target,
        "indvAimPriceL": target,
        "predictThisYearEps": eps_this,
        "predictNextYearEps": eps_next,
        "predictNextTwoYearEps": eps_next2,
        "predictThisYearPe": pe,
        "infoCode": info or f"AP{publish.replace('-', '')}000000",
        "stockName": NAME,
        "stockCode": CODE,
    }


ROWS = [
    _row(
        "2026-08-20", "中信证券", "买入", title="AI 商业化加速",
        target=100.0, eps_this=3.0, eps_next=3.5, eps_next2=4.0,
    ),
    # 同一机构的旧报告，不参与最新观点统计
    _row(
        "2026-08-10", "中信证券", "增持",
        target=80.0, eps_this=2.7, eps_next=3.1, eps_next2=3.6,
    ),
    _row(
        "2026-07-01", "华泰证券", "买入", last_rating="增持",
        target=90.0, eps_this=3.2, eps_next=3.6, eps_next2=4.1,
    ),
    _row(
        "2026-06-01", "国泰君安", "持有",
        eps_this=2.9, eps_next=3.3, eps_next2=3.8,
    ),
    _row(
        "2025-12-01", "招商证券", "买入",
        eps_this=2.8, eps_next=3.4, eps_next2=3.9,
    ),
    # 一年窗口外
    _row("2024-01-01", "老机构", "买入", eps_this=1.5),
]


def test_from_rows() -> None:
    data = views.reports_from_rows(ROWS, CODE, days=365, today=TODAY)

    assert data["available"] is True, data
    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-08-20", data
    assert data["window_start"] == "2025-10-25", data
    assert data["window_end"] == "2026-10-25", data
    assert data["days"] == 365, data
    assert data["has_reports"] is True, data

    counts = data["counts"]
    assert counts["reports"] == 5, counts
    assert counts["orgs"] == 4, counts
    assert counts["latest_date"] == "2026-08-20", counts
    assert counts["ratings"] == [
        {"rating": "买入", "reports": 3},
        {"rating": "持有", "reports": 1},
    ], counts
    assert counts["rating_changes"] == 1, counts
    assert counts["target_price_orgs"] == 2, counts
    assert counts["target_price_low"] == 90.0, counts
    assert counts["target_price_high"] == 100.0, counts
    assert counts["target_price_median"] == 95.0, counts
    # 目标年度只取当年研报，避免新旧口径混用
    assert counts["consensus_current_year"] == {
        "year": 2026, "eps": 3.0, "orgs": 3,
    }, counts
    assert counts["consensus_next_year"] == {
        "year": 2027, "eps": 3.5, "orgs": 3,
    }, counts

    latest = data["latest"]
    assert latest["publish_date"] == "2026-08-20", latest
    assert latest["org"] == "中信证券", latest
    assert latest["rating"] == "买入", latest
    assert latest["eps_this_year"] == 3.0, latest
    assert latest["url"].startswith(
        "https://data.eastmoney.com/report/info/"
    ), latest

    assert [item["org"] for item in data["latest_by_org"]] == [
        "中信证券", "华泰证券", "国泰君安", "招商证券",
    ], data["latest_by_org"]

    notes = data["notes"]
    assert any("乐观" in note for note in notes), notes
    assert any("目标价" in note for note in notes), notes
    assert any("EPS" in note for note in notes), notes


def test_default_and_window() -> None:
    default = views.reports_from_rows(ROWS, CODE, today=TODAY)
    assert default["days"] == 365, default
    assert default["counts"]["reports"] == 5, default["counts"]

    narrow = views.reports_from_rows(ROWS, CODE, days=100, today=TODAY)
    assert narrow["counts"]["reports"] == 2, narrow["counts"]
    assert narrow["window_start"] == "2026-07-17", narrow

    wide = views.reports_from_rows(ROWS, CODE, days=9999, today=TODAY)
    assert wide["days"] == 730, wide

    bad = views.reports_from_rows(ROWS, CODE, days="bad", today=TODAY)
    assert bad["days"] == 365, bad

    too_small = views.reports_from_rows(ROWS, CODE, days=1, today=TODAY)
    assert too_small["days"] == 30, too_small


def test_empty_and_errors() -> None:
    data = views.reports_from_rows([], CODE, today=TODAY)
    assert data["available"] is True, data
    assert data["has_reports"] is False, data
    assert data["as_of"] is None, data
    assert data["latest"] is None, data
    assert data["reports"] == [], data
    assert data["counts"]["reports"] == 0, data["counts"]
    assert any("没有取到研报" in note for note in data["notes"]), data["notes"]

    try:
        views.reports_from_rows("bad", CODE)  # type: ignore[arg-type]
    except views.AnalystViewError:
        pass
    else:
        raise AssertionError("非列表输入应抛 AnalystViewError")

    try:
        views.fetch_reports("")
    except views.AnalystViewError:
        pass
    else:
        raise AssertionError("空代码应抛 AnalystViewError")


def test_window_empty() -> None:
    old = [_row("2024-01-01", "老机构", "买入", eps_this=1.5)]
    data = views.reports_from_rows(old, CODE, days=365, today=TODAY)
    assert data["as_of"] == "2024-01-01", data
    assert data["latest"]["publish_date"] == "2024-01-01", data
    assert data["counts"]["reports"] == 0, data["counts"]
    assert data["reports"] == [], data
    assert any("近1年" in note for note in data["notes"]), data["notes"]


def test_fetch_error() -> None:
    original = views._get_page

    def broken(code, begin, end, page):
        raise ValueError("接口超时")

    views._get_page = broken  # type: ignore[assignment]
    try:
        views.fetch_reports(CODE)
    except views.AnalystViewError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("接口失败应抛 AnalystViewError")

    views._get_page = lambda code, begin, end, page: {"data": []}  # type: ignore[assignment]
    try:
        empty = views.fetch_reports(CODE)
        assert empty["reports"] == [], empty
    finally:
        views._get_page = original  # type: ignore[assignment]


def test_format() -> None:
    data = views.reports_from_rows(ROWS, CODE, days=365, today=TODAY)
    text = views.format_analyst_views(data, code=CODE, name=NAME)
    assert text.startswith(f"{NAME}({CODE})"), text
    assert "卖方研报近1年" in text, text
    assert "近1年：5 篇 / 覆盖 4 家机构 / 评级：买入 3 / 持有 1" in text, text
    assert "目标价：90.00~100.00元（中位 95.00，2 家）" in text, text
    assert "一致预期 EPS：2026 3.00 元（3 家） / 2027 3.50 元（3 家）" in text, text
    assert "最新：2026-08-20 中信证券 买入《AI 商业化加速》" in text, text
    assert "数据来源：" in text, text

    empty = views.reports_from_rows([], CODE, today=TODAY)
    empty_text = views.format_analyst_views(empty, code=CODE, name=NAME)
    assert "暂无已披露的卖方研报" in empty_text, empty_text

    old = views.reports_from_rows(
        [_row("2024-01-01", "老机构", "买入", eps_this=1.5)],
        CODE, days=365, today=TODAY,
    )
    old_text = views.format_analyst_views(old, code=CODE, name=NAME)
    assert "近1年没有研报覆盖" in old_text, old_text
    assert "最近一篇为 2024-01-01" in old_text, old_text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="views_cache_")
    views.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item
        for row in ROWS
        for item in [views._normalize_row(row)]
        if item is not None
    ]
    calls = {"fetch": 0}

    def fake_fetch(code: str, days: int = 365) -> dict:
        calls["fetch"] += 1
        return {
            "code": code, "days": days, "reports": events,
            "count": len(events), "truncated": False,
        }

    views.fetch_reports = fake_fetch  # type: ignore[assignment]

    first = views.ensure_reports(CODE, days=365)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == len(events), first
    assert first["data"]["fetched_at"], first

    second = views.ensure_reports(CODE, days=365)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    views.ensure_reports(CODE, days=180)
    assert calls["fetch"] == 2, calls

    views.ensure_reports(CODE, days=365, force=True)
    assert calls["fetch"] == 3, calls

    original_age = views._age_hours
    views._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    views.ensure_reports(CODE, days=365)
    assert calls["fetch"] == 4, calls
    views._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str, days: int = 365) -> dict:
        raise views.AnalystViewError("接口超时")

    views.fetch_reports = broken  # type: ignore[assignment]
    stale = views.ensure_reports(CODE, days=365, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["count"] == len(events), stale

    try:
        views.ensure_reports("600000", days=365)
    except views.AnalystViewError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 AnalystViewError")

    try:
        views.ensure_reports("")
    except views.AnalystViewError:
        pass
    else:
        raise AssertionError("空代码应该抛 AnalystViewError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="views_build_")
    views.DB_PATH = os.path.join(tmpdir, "stock.db")

    events = [
        item
        for row in ROWS
        for item in [views._normalize_row(row)]
        if item is not None
    ]
    views.fetch_reports = lambda code, days=365: {  # type: ignore[assignment]
        "code": code, "days": days, "reports": events,
        "count": len(events), "truncated": False,
    }

    class FakeStock:
        name = NAME

    original_stock = views.get_stock
    views.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = views.build_analyst_views(CODE, days=365, today=TODAY)
    finally:
        views.get_stock = original_stock  # type: ignore[assignment]

    assert data["name"] == NAME, data
    assert data["counts"]["reports"] == 5, data["counts"]
    assert data["sources"] == {views.SOURCE_LABEL: "2026-08-20"}, data
    assert data["url"] == (
        "https://data.eastmoney.com/report/stock.jshtml?stockcode=002230"
    ), data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data["summary"]
    assert data["truncated"] is False, data


def test_page_urls() -> None:
    assert views.PAGE_URL.format(code=CODE) == (
        "https://data.eastmoney.com/report/stock.jshtml?stockcode=002230"
    )
    assert views.INFO_URL.format(info="AP123") == (
        "https://data.eastmoney.com/report/info/AP123.html"
    )


def main() -> None:
    test_from_rows()
    test_default_and_window()
    test_empty_and_errors()
    test_window_empty()
    test_fetch_error()
    test_format()
    test_cache()
    test_build()
    test_page_urls()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
