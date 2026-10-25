"""公告事件验证：分类、时间窗口、提示、文本与本地缓存。

纯函数部分直接喂构造好的行；缓存部分把数据库换到临时目录，并替换
``fetch_announcements``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.announcements as ann

CODE = "605499"
NAME = "东鹏饮料"
TODAY = date(2026, 10, 24)   # 与 90 天窗口对齐，测试不随真实日期漂移


def row(
    title: str,
    notice_date: str = "2026-10-10",
    categories: str = "",
    art: str = "AN1",
    code: str = CODE,
) -> dict:
    return {
        "code": code,
        "art_code": art,
        "notice_date": notice_date,
        "title": title,
        "categories": categories,
        "url": f"https://example.invalid/{art}",
        "fetched_at": ann._now(),
    }


ROWS = [
    row("关于以集中竞价交易方式回购A股股份的进展公告", "2026-10-10", "回购进展情况", "AN4"),
    row("关于股东减持股份的预披露公告", "2026-09-01", "股东减持", "AN3"),
    row("2026年半年度权益分派实施公告", "2026-08-20", "分配方案实施", "AN2"),
    row("2025年年度报告", "2026-06-01", "", "AN1"),  # 超出 90 天窗口
]


def test_classify() -> None:
    cases = [
        ("关于以集中竞价交易方式回购A股股份的进展公告", "回购进展情况", ["回购"], "info"),
        ("关于股东减持股份的预披露公告", "股东减持", ["减持"], "warning"),
        ("2026年半年度业绩预告", "", ["业绩预告"], "info"),
        ("2026年半年度业绩预减公告", "", ["业绩预减"], "warning"),
        ("关于收到中国证监会立案告知书的公告", "", ["诉讼处罚"], "warning"),
        ("关于回购股份及股东增持的公告", "", ["回购", "增持"], "info"),
        ("关于召开2026年第三次临时股东会的通知", "召开股东大会通知", [], None),
        # 栏目名也能提供线索
        ("关于2026年半年度利润分配预案的公告", "分配方案", ["分红"], "info"),
        # 利空优先：同一份公告里既回购又减持
        ("关于回购股份及股东减持计划的公告", "", ["减持", "回购"], "warning"),
    ]
    for title, categories, tags, level in cases:
        got_tags, got_level = ann.classify(title, categories)
        assert got_tags == tags, (title, got_tags, tags)
        assert got_level == level, (title, got_level, level)

    # 标题为空时不能误判
    assert ann.classify("", "") == ([], None)


def test_basic() -> None:
    announcements = ann.announcements_from_rows(
        ROWS, code=CODE, name=NAME, today=TODAY
    )

    assert announcements["available"] is True, announcements
    assert announcements["code"] == CODE and announcements["name"] == NAME
    assert announcements["days"] == ann.DEFAULT_DAYS == 90
    assert announcements["as_of"] == "2026-10-10", announcements
    assert announcements["count"] == 3, announcements["count"]  # 06-01 那条被窗口挡掉

    items = announcements["items"]
    assert [item["date"] for item in items] == ["2026-10-10", "2026-09-01", "2026-08-20"]
    assert items[0]["tags"] == ["回购"] and items[0]["level"] == "info", items[0]
    assert items[1]["tags"] == ["减持"] and items[1]["level"] == "warning", items[1]
    assert items[2]["tags"] == ["分红"] and items[2]["level"] == "info", items[2]
    assert items[0]["url"].endswith("/AN4"), items[0]

    # 一条利空 + 一条利好，都取最新
    flags = announcements["flags"]
    assert [flag["level"] for flag in flags] == ["warning", "info"], flags
    assert flags[0]["message"].startswith("09-01 减持："), flags[0]
    assert flags[1]["message"].startswith("10-10 回购："), flags[1]

    summary = announcements["summary"]
    assert summary.startswith("东鹏饮料(605499) · 近期公告（近 90 天 3 条）"), summary
    assert "- 2026-10-10 [回购] 关于以集中竞价交易方式回购A股股份的进展公告" in summary
    assert "- 2026-09-01 [减持] 关于股东减持股份的预披露公告" in summary
    assert "[异常] 09-01 减持：" in summary, summary
    assert "[关注] 10-10 回购：" in summary, summary


def test_window_and_edges() -> None:
    # 窗口内没有记录：明确说"没有公告"，而不是空字符串
    outside = ann.announcements_from_rows(
        [row("2025年年度报告", "2026-01-01")], code=CODE, name=NAME, today=TODAY
    )
    assert outside["available"] is False, outside
    assert outside["count"] == 0 and outside["items"] == [], outside
    assert "近 90 天没有公告" in outside["summary"], outside

    # 完全空数据
    empty = ann.announcements_from_rows([], code=CODE, name=NAME, today=TODAY)
    assert empty["available"] is False
    assert empty["flags"] == [] and empty["items"] == []
    assert "没有公告" in ann.format_announcements(empty), empty

    # 自定义窗口更窄时，同样的行会被挡掉一部分
    narrow = ann.announcements_from_rows(ROWS, today=TODAY, days=30)
    assert narrow["count"] == 1, narrow["count"]
    assert narrow["items"][0]["date"] == "2026-10-10", narrow

    # 同一份公告重复入库只算一条，且按时间倒序
    duplicated = ann.announcements_from_rows(
        [ROWS[0], ROWS[0], ROWS[2]], today=TODAY
    )
    assert duplicated["count"] == 3, duplicated  # 这里不去重，去重发生在 fetch
    assert [item["date"] for item in duplicated["items"]] == [
        "2026-10-10", "2026-10-10", "2026-08-20",
    ], duplicated["items"]

    # 摘要的列表条数受 limit 控制，但总数不变
    limited = ann.announcements_from_rows(ROWS, today=TODAY, limit=1)
    assert limited["count"] == 3, limited
    assert limited["summary"].count("\n- ") == 1, limited["summary"]
    assert len(ann.format_items(limited, 1)) == 1, ann.format_items(limited, 1)
    assert len(ann.format_items(limited, None)) == 3

    # 同名公告只列最新一条，避免 H 股日报这类重复条目占满列表
    repeated = ann.announcements_from_rows([
        row("H股公告(翌日披露报表)", "2026-10-10", art="AN6"),
        row("H股公告(翌日披露报表)", "2026-10-09", art="AN5"),
        row("关于回购股份的公告", "2026-10-08", art="AN4"),
    ], today=TODAY)
    repeated_lines = ann.format_items(repeated, None)
    assert len(repeated_lines) == 2, repeated_lines
    assert repeated_lines[0].startswith("- 2026-10-10 "), repeated_lines

    # 缓存过期提示接在最后
    stale = ann.announcements_from_rows(ROWS, today=TODAY, stale=True)
    assert stale["flags"][-1]["code"] == "stale_data", stale["flags"]
    assert "本地缓存" in stale["flags"][-1]["message"], stale["flags"]


def _raw(art: str, notice_date: str, title: str) -> dict:
    return {
        "art_code": art,
        "notice_date": notice_date + " 00:00:00",
        "title_ch": f"东鹏饮料:{title}",
        "codes": [{"short_name": "东鹏饮料"}],
        "columns": [{"column_name": "临时公告"}],
    }


def _payload(items: list[dict], page: int, total: int = 999) -> dict:
    return {"data": {"list": items, "page_index": page, "page_size": 100, "total_hits": total}}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def test_paging() -> None:
    """只抓第 1 页会漏掉半年前的公告，必须按页往回翻。"""
    original_fetch = ann.fetch_announcements
    original_get = ann.requests.get
    pages = {
        1: [_raw(f"P1-{i}", "2026-10-10", f"第一页第{i}条") for i in range(ann.PAGE_SIZE)],
        2: [_raw(f"P2-{i}", "2026-03-01", f"第二页第{i}条") for i in range(ann.PAGE_SIZE)],
        3: [_raw("P3-0", "2025-11-01", "第三页唯一一条")],
    }
    seen: list[int] = []

    def fake_get(url, params=None, **kwargs):
        page = params["page_index"]
        seen.append(page)
        return _FakeResponse(_payload(pages.get(page, []), page))

    ann.requests.get = fake_get  # type: ignore[assignment]
    try:
        rows = ann.fetch_announcements(CODE)
        assert seen == [1, 2, 3], seen                      # 短页之后停止
        assert len(rows) == ann.PAGE_SIZE * 2 + 1, len(rows)

        # 翻页上限：即使每页都是满的，也不能无限打接口
        seen.clear()
        full = {page: [_raw(f"X{page}-{i}", "2026-10-10", f"第{page}页") for i in range(ann.PAGE_SIZE)]
                for page in range(1, 20)}
        ann.requests.get = lambda url, params=None, **kw: (
            seen.append(params["page_index"]), _FakeResponse(_payload(full.get(params["page_index"], []), params["page_index"])))[1]  # type: ignore[assignment]
        capped = ann.fetch_announcements(CODE, max_pages=3)
        assert seen == [1, 2, 3], seen
        assert len(capped) == ann.PAGE_SIZE * 3, len(capped)

        # 已经翻过保留窗口就不必再翻：老公告入库也会被裁掉
        seen.clear()

        def old_get(url, params=None, **kwargs):
            page = params["page_index"]
            seen.append(page)
            items = [_raw(f"OLD{page}-{i}", "2024-01-0%d" % (i % 9 + 1), "很老的公告")
                     for i in range(ann.PAGE_SIZE)]
            return _FakeResponse(_payload(items, page))

        ann.requests.get = old_get  # type: ignore[assignment]
        assert ann.fetch_announcements(CODE) == []
        assert seen == [1], seen                            # 第 1 页就越界，只打一次

        # 同一份公告在相邻页重复出现时只留一条
        seen.clear()
        dup = _raw("DUP", "2026-10-10", "跨页重复")
        ann.requests.get = lambda url, params=None, **kw: _FakeResponse(_payload(
            [_raw(f"A{params['page_index']}-{i}", "2026-10-10", "第%d页" % params["page_index"])
             for i in range(ann.PAGE_SIZE - 1)] + [dup], params["page_index"]))  # type: ignore[assignment]
        deduped = ann.fetch_announcements(CODE, max_pages=2)
        assert sum(1 for row in deduped if row["art_code"] == "DUP") == 1, deduped
    finally:
        ann.requests.get = original_get  # type: ignore[assignment]
        ann.fetch_announcements = original_fetch  # type: ignore[assignment]

    # 第一页就失败时仍然报错，不能假装成功
    def broken_get(url, params=None, **kwargs):
        raise RuntimeError("连接被重置")

    ann.requests.get = broken_get  # type: ignore[assignment]
    try:
        ann.fetch_announcements(CODE)
    except ann.AnnouncementError as error:
        assert "连接被重置" in str(error), error
    else:
        raise AssertionError("接口失败应该抛 AnnouncementError")
    finally:
        ann.requests.get = original_get  # type: ignore[assignment]


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="announcements_test_")
    ann.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}

    def fake_fetch(code: str) -> list[dict]:
        calls["fetch"] += 1
        return [dict(item, code=code) for item in ROWS]

    ann.fetch_announcements = fake_fetch  # type: ignore[assignment]

    first = ann.ensure_announcements(CODE)
    assert calls["fetch"] == 1 and len(first["rows"]) == len(ROWS), (calls, first)
    assert first["stale"] is False

    # 缓存期内不重复抓取
    second = ann.ensure_announcements(CODE)
    assert calls["fetch"] == 1 and len(second["rows"]) == len(ROWS), calls

    # force 强制刷新
    ann.ensure_announcements(CODE, force=True)
    assert calls["fetch"] == 2, calls

    # 缓存过期后重新抓取
    original_age = ann._age_hours
    ann._age_hours = lambda rows: 99.0  # type: ignore[assignment]
    ann.ensure_announcements(CODE)
    assert calls["fetch"] == 3, calls
    ann._age_hours = original_age  # type: ignore[assignment]

    # 抓取失败但本地有缓存：返回旧数据并标记 stale
    def broken(code: str) -> list[dict]:
        raise ann.AnnouncementError("接口超时")

    ann.fetch_announcements = broken  # type: ignore[assignment]
    stale = ann.ensure_announcements(CODE, force=True)
    assert stale["stale"] is True and len(stale["rows"]) == len(ROWS), stale

    # 没有缓存时只能报错
    try:
        ann.ensure_announcements("600000")
    except ann.AnnouncementError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 AnnouncementError")

    try:
        ann.ensure_announcements("")
    except ann.AnnouncementError:
        pass
    else:
        raise AssertionError("空代码应该抛 AnnouncementError")


def test_helpers() -> None:
    assert ann._md("2026-10-08") == "10-08"
    assert ann._md("bad") == "bad"
    assert ann._clip("短句", 10) == "短句"
    assert ann._clip("一二三四五六", 8) == "一二三四…"
    assert ann._clip("abcdef", 10) == "abcdef"
    assert ann._date_text("2026-10-17 00:00:00") == "2026-10-17"
    assert ann._date_text("-") is None

    # 标题前缀（公司简称:）在抓取时去掉
    codes = [{"short_name": "东鹏饮料"}]
    assert ann._strip_prefix("东鹏饮料:关于回购的公告", codes) == "关于回购的公告"
    assert ann._strip_prefix("东鹏饮料：H股公告", codes) == "H股公告"
    assert ann._strip_prefix("别的公司:公告", codes) == "别的公司:公告"

    # 句首重复的公司全名也去掉，但"关于收购某某有限公司"不能动
    assert ann._clean_title("东鹏饮料(集团)股份有限公司关于回购的公告") == "关于回购的公告"
    assert ann._clean_title("宁德时代新能源科技股份有限公司2026年三季度报告") == "2026年三季度报告"
    assert ann._clean_title("关于收购某某有限公司股权的公告") == "关于收购某某有限公司股权的公告"
    assert ann._clean_title("H股公告(翌日披露报表)") == "H股公告(翌日披露报表)"
    assert ann._clean_title("股份有限公司") == "股份有限公司"


def main() -> None:
    test_classify()
    test_basic()
    test_window_and_edges()
    test_paging()
    test_cache()
    test_helpers()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
