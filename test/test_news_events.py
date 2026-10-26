"""模块 2 验证：事件分类、行业映射、去重、配额与海外市场。"""

from __future__ import annotations

import os
import sys
from datetime import date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.news_events as news


class Frame:
    """akshare DataFrame 的最小替身：只用到 empty/to_dict/切片。"""

    def __init__(self, rows):
        self.rows = list(rows)

    @property
    def empty(self):
        return not self.rows

    def to_dict(self, orient="records"):
        assert orient == "records"
        return [dict(row) for row in self.rows]

    def __getitem__(self, item):
        return Frame(self.rows[item])


def _event(title, summary="", published="2026-10-26 10:00:00"):
    return news._make_event(
        {"标题": title, "摘要": summary, "发布时间": published},
        "东方财富", "news:em:test", "2026-10-26", "em",
    )


def test_normalize_cls_title_and_time() -> None:
    record = {
        "标题": "",
        "内容": "【沪指翻红 三市上涨个股近3000只】财联社10月26日电，指数探底回升。",
        "发布日期": date(2026, 10, 26),
        "发布时间": time(13, 10, 10),
    }
    normalized = news._normalize(record, "cls")
    assert normalized["title"] == "沪指翻红 三市上涨个股近3000只"
    assert normalized["published_at"] == "2026-10-26 13:10:10"
    assert normalized["summary"].startswith("指数探底回升")
    # 没有标题时从正文首句/括号里取，不能返回空
    assert news._normalize({"内容": "【快讯】某部委发布通知"}, "cls")["title"] == "某部委发布通知"
    assert news._normalize({"内容": "没有标题的一句话。"}, "cls")["title"] == "没有标题的一句话"
    assert news._normalize({"内容": ""}, "cls") is None


def test_policy_event_and_industry_path() -> None:
    event = _event("国务院发布半导体产业规划", "支持芯片国产替代与设备材料攻关")
    assert event is not None
    assert event["category"] == news.POLICY
    assert event["confidence"] == "高"
    assert [item["name"] for item in event["industries"]][0] == "半导体"
    assert event["impact_path"].startswith("政策 → ")
    assert "半导体" in event["impact_path"]
    assert event["published_at"] == "2026-10-26 10:00:00"
    assert event["uncertainty"]


def test_overseas_event() -> None:
    event = _event("美联储降息预期升温 黄金价格创新高", "美元走弱，贵金属走强")
    assert event is not None
    assert event["category"] == news.OVERSEAS
    assert [item["name"] for item in event["industries"]][0] == "贵金属"
    assert event["impact_path"].startswith("海外市场 → ")


def test_company_event() -> None:
    event = _event("某公司公告回购股份", "拟回购不超过2亿元")
    assert event is not None
    assert event["category"] == news.COMPANY
    assert event["confidence"] in ("中", "低")
    assert "公告细节" in event["uncertainty"]


def test_title_label_is_stripped() -> None:
    assert news._strip_label("电报解读】又一车企获得示范运营资格") == "又一车企获得示范运营资格"
    assert news._strip_label("【快讯】某部委发布通知") == "某部委发布通知"
    # 栏目名之外的括号内容属于事件本身，不能删
    assert news._strip_label("央行公告】开展逆回购") == "央行公告】开展逆回购"
    assert news._strip_label("某公司发布新产品") == "某公司发布新产品"


def test_ascii_keywords_use_word_boundary() -> None:
    # EPC 里的 PC 不是计算机硬件，不能把工程公司归到计算机设备
    event = _event(
        "博迈科：2026全年新签合同总额有望创下近几年新高",
        "公司是EPC总承包商，海外订单占比提升",
    )
    assert event is not None
    names = [item["name"] for item in event["industries"]]
    assert "计算机设备" not in names, names


def test_summary_only_hit_is_not_an_industry() -> None:
    # 标题没提，正文顺带一句服务器，不足以把事件归到 IT 服务
    weak = _event("某公司公告回购股份", "公司称服务器采购成本可控")
    assert weak is not None
    assert weak["industries"] == [], weak["industries"]
    assert "行业映射不明确" in weak["impact_path"]

    # 正文里连续命中多个关键词时仍然可以认定
    strong = _event("某公司公告回购股份", "服务器与数据中心需求回暖，算力租赁涨价")
    assert strong is not None
    assert [item["name"] for item in strong["industries"]][0] == "IT服务"


def test_noise_is_dropped() -> None:
    assert _event("今日天气晴朗") is None
    assert _event("某ETF涨超2%") is None
    assert _event("港股午盘：恒指涨0.44%") is None
    assert _event("某公司发布新产品") is None


def test_dedupe_merges_sources() -> None:
    first = _event("商务部：家电以旧换新累计超1亿台", "政策带动零售")
    second = news._make_event(
        {"标题": "商务部：家电以旧换新累计超1亿台", "摘要": "政策带动零售",
         "发布时间": "2026-10-26 10:05:00"},
        "财联社", "news:cls:test", "2026-10-26", "em",
    )
    assert first is not None and second is not None
    merged = news.dedupe_events([first, second])
    assert len(merged) == 1, merged
    event = merged[0]
    assert len(event["sources"]) == 2, event["sources"]
    assert event["confidence"] == "高"
    assert event["score"] > first["score"]
    assert event["published_at"] == "2026-10-26 10:05:00"


def test_select_events_quotas() -> None:
    events = []
    for index in range(10):
        events.append({
            "category": news.POLICY, "score": 100 - index,
            "industries": [{"name": "半导体"}],
        })
    for index in range(5):
        events.append({
            "category": news.INDUSTRY, "score": 90 - index,
            "industries": [{"name": "光伏设备"}],
        })
    for index in range(5):
        events.append({
            "category": news.COMPANY, "score": 80 - index, "industries": [],
        })
    for index in range(5):
        events.append({
            "category": news.OVERSEAS, "score": 70 - index, "industries": [],
        })
    selected = news.select_events(events, 20)
    counts = {}
    for event in selected:
        counts[event["category"]] = counts.get(event["category"], 0) + 1
    assert counts == {"政策": 6, "产业": 4, "公司": 3, "海外": 3}, counts
    assert len(selected) == 16

    # 没有行业落点的政策事件最多两条，避免整份清单都是宏观表述
    only_policy = [
        {"category": news.POLICY, "score": 10 - index, "industries": []}
        for index in range(5)
    ]
    assert len(news.select_events(only_policy, 10)) == 2


def test_overseas_markets() -> None:
    original = news.ak
    class FakeAk:
        @staticmethod
        def index_us_stock_sina(symbol):
            closes = {".INX": (100.0, 99.0), ".DJI": (200.0, 198.0), ".IXIC": (300.0, 301.0)}
            previous, last = closes[symbol]
            return Frame([
                {"date": date(2026, 10, 22), "close": previous},
                {"date": date(2026, 10, 23), "close": last},
            ])

        @staticmethod
        def stock_hk_index_spot_sina():
            return Frame([
                {"代码": "HSI", "名称": "恒生指数", "最新价": 23000, "涨跌幅": 0.6},
                {"代码": "HSTECH", "名称": "恒生科技指数", "最新价": 3800, "涨跌幅": -0.2},
            ])
    news.ak = FakeAk
    try:
        items, errors = news._overseas_markets("2026-10-26")
    finally:
        news.ak = original
    assert errors == {}, errors
    assert len(items) == 5
    by_code = {item["code"]: item for item in items}
    assert by_code[".INX"]["chg_pct"] == -1.0
    assert by_code[".DJI"]["chg_pct"] == -1.0
    assert by_code[".IXIC"]["chg_pct"] == 0.33
    assert by_code["HSI"]["chg_pct"] == 0.6
    assert by_code["HSTECH"]["market"] == "港股"


def test_build_news_events_from_feeds() -> None:
    original = news.ak

    class FakeAk:
        @staticmethod
        def stock_info_global_cls():
            return Frame([
                {"标题": "商务部：家电以旧换新累计超1亿台", "内容": "",
                 "发布日期": date(2026, 10, 26), "发布时间": time(10, 0)},
            ])

        @staticmethod
        def stock_info_global_em():
            return Frame([
                {"标题": "光伏组件价格上涨", "摘要": "产业链价格回暖",
                 "发布时间": "2026-10-26 10:10:00", "链接": "https://example.com/a"},
                {"标题": "某公司公告回购股份", "摘要": "拟回购2亿元",
                 "发布时间": "2026-10-26 10:20:00", "链接": ""},
            ])

        @staticmethod
        def news_cctv(date):
            if date == "20261025":
                return Frame([{
                    "date": "20261025", "title": "国务院部署扩大内需",
                    "content": "会议指出要扩大消费和有效投资。",
                }])
            return Frame([])

        @staticmethod
        def index_us_stock_sina(symbol):
            return Frame([
                {"date": date(2026, 10, 22), "close": 100.0},
                {"date": date(2026, 10, 23), "close": 101.0},
            ])

        @staticmethod
        def stock_hk_index_spot_sina():
            return Frame([
                {"代码": "HSI", "名称": "恒生指数", "最新价": 23000, "涨跌幅": 0.6},
                {"代码": "HSTECH", "名称": "恒生科技指数", "最新价": 3800, "涨跌幅": -0.2},
            ])

    news.ak = FakeAk
    try:
        result = news.build_news_events(limit=12)
    finally:
        news.ak = original

    assert result["errors"] == {}, result["errors"]
    categories = {event["category"] for event in result["events"]}
    assert "政策" in categories and "产业" in categories and "公司" in categories
    assert len(result["overseas"]) == 5
    assert result["counts"]["政策"] >= 1
    assert all(event["published_at"] for event in result["events"])
    assert all(event["uncertainty"] for event in result["events"])
    feed_names = [feed["name"] for feed in result["feeds"]]
    assert "财联社" in feed_names and "东方财富" in feed_names and "海外市场" in feed_names


def test_feed_failure_is_isolated() -> None:
    original = news.ak

    class FakeAk:
        @staticmethod
        def stock_info_global_cls():
            raise RuntimeError("财联社超时")

        @staticmethod
        def stock_info_global_em():
            return Frame([
                {"标题": "光伏组件价格上涨", "摘要": "",
                 "发布时间": "2026-10-26 10:10:00", "链接": ""},
            ])

        @staticmethod
        def news_cctv(date):
            raise RuntimeError("新闻联播不可用")

        @staticmethod
        def index_us_stock_sina(symbol):
            raise RuntimeError("美股接口不可用")

        @staticmethod
        def stock_hk_index_spot_sina():
            raise RuntimeError("港股接口不可用")

    news.ak = FakeAk
    try:
        result = news.build_news_events(limit=12)
    finally:
        news.ak = original

    assert result["events"], result
    assert "cls" in result["errors"]
    assert any(key.startswith("cctv") for key in result["errors"])
    assert any(key.startswith("overseas") for key in result["errors"])
    assert result["overseas"] == []


def main() -> None:
    test_normalize_cls_title_and_time()
    test_policy_event_and_industry_path()
    test_overseas_event()
    test_company_event()
    test_title_label_is_stripped()
    test_ascii_keywords_use_word_boundary()
    test_summary_only_hit_is_not_an_industry()
    test_noise_is_dropped()
    test_dedupe_merges_sources()
    test_select_events_quotas()
    test_overseas_markets()
    test_build_news_events_from_feeds()
    test_feed_failure_is_isolated()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
