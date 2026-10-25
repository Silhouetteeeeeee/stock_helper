"""同业对比验证：同报告期筛选、统计排名、规模相近公司与缓存。

纯函数部分直接喂业绩报表行；取数部分替换 ``_request``；
缓存与摘要部分把数据库换到临时目录，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.peers as pe

CODE = "605499"
NAME = "东鹏饮料"


def _row(
    code: str,
    name: str,
    revenue: float,
    net_profit: float | None = None,
    revenue_yoy: float | None = 10.0,
    profit_yoy: float | None = 15.0,
    gross_margin: float | None = 30.0,
    roe: float | None = 10.0,
    report: str = "2026-06-30",
    board: str = "BK1282",
    market: str = "上交所主板",
    security_type: str = "A股",
    update: str = "2026-08-30 00:00:00",
) -> dict:
    return {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": name,
        "REPORTDATE": f"{report} 00:00:00",
        "BOARD_CODE": board,
        "BOARD_NAME": "食品饮料",
        "TRADE_MARKET": market,
        "SECURITY_TYPE": security_type,
        "DATATYPE": "2026年半年报",
        "QDATE": "2026年二季度",
        "TOTAL_OPERATE_INCOME": revenue,
        "PARENT_NETPROFIT": net_profit,
        "YSTZ": revenue_yoy,
        "SJLTZ": profit_yoy,
        "XSMLL": gross_margin,
        "WEIGHTAVG_ROE": roe,
        "BASIC_EPS": 5.5,
        "BPS": 30.0,
        "MGJYXJJE": 6.0,
        "UPDATE_DATE": update,
    }


ROWS = [
    _row(CODE, NAME, 12_443_036_555.29, 2_866_801_858.34, 15.89, 20.72, 48.36, 19.03),
    _row("600887", "伊利股份", 64_489_715_236.16, 5_758_628_097.79, 4.13, -20.02, 36.43, 10.09),
    _row("600597", "光明乳业", 11_870_000_000.0, 120_000_000.0, -4.81, -30.0, 17.57, 2.64),
    _row("002946", "新乳业", 5_810_000_000.0, 320_000_000.0, 5.20, 8.0, 29.73, 11.84),
    _row("603156", "养元饮品", 3_500_000_000.0, 900_000_000.0, 2.0, 5.0, 45.0, 15.0),
    _row("600429", "三元股份", 3_470_000_000.0, -50_000_000.0, -8.0, -120.0, 20.0, -2.0),
    _row("600882", "妙可蓝多", 3_300_000_000.0, 150_000_000.0, 12.0, 30.0, 30.0, 5.0),
    _row("600300", "维维股份", 1_670_000_000.0, 50_000_000.0, -2.0, -10.0, 25.0, 3.0),
    _row("000848", "承德露露", 1_540_000_000.0, 300_000_000.0, 3.0, 4.0, 40.0, 12.0),
    _row("600419", "天润乳业", 1_450_000_000.0, 60_000_000.0, 6.0, 9.0, 22.0, 6.0),
]

VALUATIONS = [
    {"code": CODE, "name": NAME, "pe_ttm": 25.0, "pb_mrq": 8.0, "total_mv": 100_000_000_000.0},
    {"code": "600887", "name": "伊利股份", "pe_ttm": 18.0, "pb_mrq": 4.0, "total_mv": 600_000_000_000.0},
    {"code": "600597", "name": "光明乳业", "pe_ttm": -80.17, "pb_mrq": 0.96, "total_mv": 9_300_000_000.0},
    {"code": "002946", "name": "新乳业", "pe_ttm": 16.26, "pb_mrq": 3.68, "total_mv": 13_000_000_000.0},
    {"code": "603156", "name": "养元饮品", "pe_ttm": 20.0, "pb_mrq": 5.0, "total_mv": 9_000_000_000.0},
    {"code": "600429", "name": "三元股份", "pe_ttm": 30.0, "pb_mrq": 2.0, "total_mv": 5_000_000_000.0},
    {"code": "600882", "name": "妙可蓝多", "pe_ttm": 35.0, "pb_mrq": 3.0, "total_mv": 8_000_000_000.0},
    {"code": "600300", "name": "维维股份", "pe_ttm": 25.0, "pb_mrq": 2.5, "total_mv": 4_000_000_000.0},
    {"code": "000848", "name": "承德露露", "pe_ttm": 15.0, "pb_mrq": 3.5, "total_mv": 6_000_000_000.0},
    # 600419 故意不给估值，验证缺失提示
]


def test_from_rows() -> None:
    data = pe.peers_from_rows(
        ROWS,
        CODE,
        valuation_rows=VALUATIONS,
        valuation_date="2026-10-23",
    )

    assert data["available"] is True, data
    assert data["as_of"] == "2026-06-30", data
    assert data["industry"] == {
        "name": "食品饮料", "code": "BK1282", "count": 10,
    }, data["industry"]
    assert data["report"]["qdate"] == "2026年二季度", data["report"]

    subject = data["subject"]
    assert subject["name"] == NAME, subject
    assert round(subject["revenue"] / 1e8, 1) == 124.4, subject
    assert round(subject["net_margin"], 1) == 23.0, subject
    assert subject["revenue_ratio"] == 1.0, subject

    # 营收中位数是 34.85 亿，东鹏排第二（伊利更大）
    revenue = data["stats"]["revenue"]
    assert round(revenue["median"] / 1e8, 2) == 34.85, revenue
    assert revenue["rank"] == 2, revenue
    assert revenue["percentile"] == 90.0, revenue
    assert revenue["count"] == 10, revenue

    # 毛利率东鹏最高
    gross = data["stats"]["gross_margin"]
    assert gross["rank"] == 1, gross
    assert round(gross["median"], 1) == 29.9, gross

    # PE 排名剔除负值：光明乳业 -80.17 不参与
    pe_stat = data["stats"]["pe_ttm"]
    assert pe_stat["count"] == 8, pe_stat
    assert pe_stat["rank"] == 3, pe_stat
    assert round(pe_stat["median"], 2) == 22.5, pe_stat

    # 规模相近：带宽内两家，再按倍数差补到 8 家；伊利作为龙头不计入
    peers = [row["code"] for row in data["peers"]]
    assert peers == [
        "600597", "002946", "603156", "600429",
        "600882", "600300", "000848", "600419",
    ], peers
    assert "600887" not in peers, peers

    leaders = [row["code"] for row in data["leaders"]]
    assert leaders == ["600887", "600597", "002946"], leaders
    assert data["counts"] == {
        "industry": 10, "valuation": 9, "selected": 8, "leaders": 3,
    }, data["counts"]

    notes = " ".join(data["notes"])
    assert "估值日期 2026-10-23 与财报期 2026-06-30 不同" in notes, notes
    assert "1 家同业缺少估值数据" in notes, notes
    assert "PE 为负" in notes, notes

    # limit 夹到 3 时只保留最接近的三家
    small = pe.peers_from_rows(ROWS, CODE, limit=1, valuation_rows=VALUATIONS)
    assert len(small["peers"]) == 3, small["peers"]
    assert [row["code"] for row in small["peers"]] == [
        "600597", "002946", "603156",
    ], small["peers"]


def test_dedupe() -> None:
    rows = ROWS + [
        _row(
            "600597", "光明乳业", 999_000_000_000.0,
            update="2026-08-01 00:00:00",
        )
    ]
    data = pe.peers_from_rows(rows, CODE)
    peer = next(item for item in data["peers"] if item["code"] == "600597")
    # 旧更新时间的重复行不能覆盖新行
    assert round(peer["revenue"] / 1e8, 1) == 118.7, peer

    periods = pe._dedupe_periods([
        _row(CODE, NAME, 100.0, report="2026-06-30", update="2026-08-01"),
        _row(CODE, NAME, 200.0, report="2026-06-30", update="2026-09-01"),
        _row(CODE, NAME, 300.0, report="2026-03-31", update="2026-04-01"),
    ])
    assert len(periods) == 2, periods
    latest = next(
        row for row in periods if row["REPORTDATE"].startswith("2026-06-30")
    )
    assert latest["TOTAL_OPERATE_INCOME"] == 200.0, latest


def test_errors() -> None:
    try:
        pe.peers_from_rows([], CODE)
    except pe.PeerError:
        pass
    else:
        raise AssertionError("空数据应该抛 PeerError")

    try:
        pe.peers_from_rows([row for row in ROWS if row["SECURITY_CODE"] != CODE], CODE)
    except pe.PeerError:
        pass
    else:
        raise AssertionError("没有本公司时应该抛 PeerError")


def test_no_valuation() -> None:
    data = pe.peers_from_rows(ROWS, CODE)
    assert data["stats"]["pe_ttm"]["median"] is None, data["stats"]["pe_ttm"]
    assert data["stats"]["pe_ttm"]["rank"] is None, data["stats"]["pe_ttm"]
    assert "没有取到估值数据" in " ".join(data["notes"]), data["notes"]

    text = pe.format_peers({
        **data,
        "name": NAME,
        "fetched_at": "",
        "stale": False,
    })
    assert "PE 25.0" not in text, text
    assert "毛利率 48.4%" in text, text


def test_fetch_fallback() -> None:
    subject_rows = [
        _row(CODE, NAME, 130.0, report="2026-09-30", update="2026-10-20"),
        _row(CODE, NAME, 120.0, report="2026-06-30", update="2026-08-30"),
    ]
    q3 = [
        _row("000001", "甲公司", 10.0, report="2026-09-30"),
        _row("000002", "乙公司", 9.0, report="2026-09-30"),
    ]
    q2 = [
        _row("000001", "甲公司", 10.0),
        _row("000002", "乙公司", 9.0),
        _row("000003", "丙公司", 8.0),
        _row("000004", "丁公司", 7.0),
        _row("000005", "戊公司", 6.0),
        _row("900001", "B股公司", 5.0, security_type="B股"),
    ]

    def fake_request(filter_expr: str, sort: str, page_size: int):
        if "SECURITY_CODE" in filter_expr:
            return subject_rows
        if "2026-09-30" in filter_expr:
            return q3
        return q2

    original = pe._request
    pe._request = fake_request  # type: ignore[assignment]
    try:
        cohort = pe.fetch_peer_cohort(CODE)
    finally:
        pe._request = original  # type: ignore[assignment]

    assert cohort["report_date"] == "2026-06-30", cohort
    assert cohort["board_code"] == "BK1282", cohort
    # 5 家 A 股同业 + 本公司，B 股被过滤
    assert len(cohort["rows"]) == 6, cohort["rows"]
    assert all(row["SECURITY_TYPE"] == "A股" for row in cohort["rows"]), cohort
    notes = " ".join(cohort["notes"])
    assert "2026-09-30 的同业样本不足 5 家" in notes, notes
    assert "改用 2026-06-30" in notes, notes

    # 最新一期样本足够时直接使用，不回退
    def enough(filter_expr: str, sort: str, page_size: int):
        if "SECURITY_CODE" in filter_expr:
            return subject_rows
        return q3 + q2[:4]

    pe._request = enough  # type: ignore[assignment]
    try:
        cohort = pe.fetch_peer_cohort(CODE)
    finally:
        pe._request = original  # type: ignore[assignment]

    assert cohort["report_date"] == "2026-09-30", cohort
    assert not cohort["notes"], cohort["notes"]

    # 没有业绩报表时直接报错
    pe._request = lambda *args, **kwargs: []  # type: ignore[assignment]
    try:
        try:
            pe.fetch_peer_cohort(CODE)
        except pe.PeerError as error:
            assert "没有业绩报表数据" in str(error), error
        else:
            raise AssertionError("空报表应该抛 PeerError")
    finally:
        pe._request = original  # type: ignore[assignment]


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="peers_test_")
    pe.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return {
            "code": code,
            "board_code": "BK1282",
            "board_name": "食品饮料",
            "report_date": "2026-06-30",
            "qdate": "2026年二季度",
            "datatype": "2026年半年报",
            "rows": ROWS,
            "notes": [],
        }

    pe.fetch_peer_cohort = fake_fetch  # type: ignore[assignment]

    first = pe.ensure_peers(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["report_date"] == "2026-06-30", first
    assert first["data"]["fetched_at"], first

    second = pe.ensure_peers(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    pe.ensure_peers(CODE, force=True)
    assert calls["fetch"] == 2, calls

    original_age = pe._age_hours
    pe._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    pe.ensure_peers(CODE)
    assert calls["fetch"] == 3, calls
    pe._age_hours = original_age  # type: ignore[assignment]

    def broken(code: str) -> dict:
        raise pe.PeerError("接口超时")

    pe.fetch_peer_cohort = broken  # type: ignore[assignment]
    stale = pe.ensure_peers(CODE, force=True)
    assert stale["stale"] is True, stale
    assert stale["data"]["report_date"] == "2026-06-30", stale

    try:
        pe.ensure_peers("600000")
    except pe.PeerError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 PeerError")

    try:
        pe.ensure_peers("")
    except pe.PeerError:
        pass
    else:
        raise AssertionError("空代码应该抛 PeerError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="peers_build_")
    pe.DB_PATH = os.path.join(tmpdir, "stock.db")

    pe.fetch_peer_cohort = lambda code: {  # type: ignore[assignment]
        "code": code,
        "board_code": "BK1282",
        "board_name": "食品饮料",
        "report_date": "2026-06-30",
        "qdate": "2026年二季度",
        "datatype": "2026年半年报",
        "rows": ROWS,
        "notes": [],
    }

    class FakeStock:
        name = NAME

    original_stock = pe.get_stock
    original_valuation = pe.ensure_valuation
    pe.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    pe.ensure_valuation = lambda code: {  # type: ignore[assignment]
        "history": [{"trade_date": "2026-10-23"}],
        "peers": VALUATIONS,
        "stale": False,
        "peers_error": None,
    }
    try:
        data = pe.build_peers(CODE, limit=5)
    finally:
        pe.get_stock = original_stock  # type: ignore[assignment]
        pe.ensure_valuation = original_valuation  # type: ignore[assignment]

    assert data["available"] is True, data
    assert data["name"] == NAME, data
    assert data["as_of"] == "2026-06-30", data
    assert data["counts"]["selected"] == 5, data["counts"]
    assert data["sources"] == {pe.SOURCE_LABEL: "2026-06-30"}, data
    assert data["url"] == "https://data.eastmoney.com/bbsj/202606/yjbb.html", data
    assert data["valuation_date"] == "2026-10-23", data
    assert data["summary"].startswith(
        f"{NAME}({CODE}) · 同业对比 2026-06-30（食品饮料 10 家）"
    ), data["summary"]
    assert "营收 124.4亿（行业中位" in data["summary"], data["summary"]
    assert "第 2/10）" in data["summary"], data["summary"]
    assert "净利 28.7亿（行业中位" in data["summary"], data["summary"]
    assert "毛利率 48.4%（中位" in data["summary"], data["summary"]
    assert "第 1/10）" in data["summary"], data["summary"]
    assert "规模相近：" in data["summary"], data["summary"]
    assert "营收龙头：伊利股份 644.9亿" in data["summary"], data["summary"]
    assert "数据来源：东方财富 业绩报表 + 估值分析，报告期 2026-06-30" in data["summary"], data["summary"]

    # 估值失败不影响财务对比
    def broken_valuation(code: str) -> dict:
        raise pe.ValuationError("估值接口超时")

    pe.ensure_valuation = broken_valuation  # type: ignore[assignment]
    pe.fetch_peer_cohort = lambda code: {  # type: ignore[assignment]
        "code": code,
        "board_code": "BK1282",
        "board_name": "食品饮料",
        "report_date": "2026-06-30",
        "qdate": "2026年二季度",
        "datatype": "2026年半年报",
        "rows": ROWS,
        "notes": [],
    }
    try:
        weak = pe.build_peers(CODE, force=True)
    finally:
        pe.ensure_valuation = original_valuation  # type: ignore[assignment]

    assert weak["available"] is True, weak
    assert weak["valuation_error"] == "估值接口超时", weak
    assert "估值数据抓取失败：估值接口超时" in " ".join(weak["notes"]), weak["notes"]


def test_page_url() -> None:
    assert pe.page_url("2026-06-30") == "https://data.eastmoney.com/bbsj/202606/yjbb.html"
    assert pe.page_url(None) == ""


def main() -> None:
    test_from_rows()
    test_dedupe()
    test_errors()
    test_no_valuation()
    test_fetch_fallback()
    test_cache()
    test_build()
    test_page_url()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
