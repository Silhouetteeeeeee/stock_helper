"""研究 skill 验证：目录、两问判据与未知名称。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.skills import catalog, get_skill


def test_catalog() -> None:
    items = catalog()
    assert [item["name"] for item in items] == [
        "business_model", "moat", "operating_history", "financial_safety",
        "growth_outlook", "management", "valuation", "margin_of_safety",
        "market_view", "news_events", "sector_rotation", "stock_screening",
        "strategy_risk", "review_eval", "research_memory",
    ]
    assert all(item["description"] for item in items)
    assert all(item["title"] for item in items)
    # 返回副本，调用方改不动全局注册表
    items[0]["description"] = "被改掉了"
    assert catalog()[0]["description"] != "被改掉了"


def test_business_model_skill() -> None:
    skill = get_skill("business_model")
    assert skill is not None
    assert skill.title.startswith("第一问")
    text = skill.instructions
    for phrase in ("三句话", "收入", "毛利率", "非经常性", "不确定",
                   "投资收益", "合同负债", "income_detail", "history",
                   "get_peers", "同业"):
        assert phrase in text, phrase


def test_moat_skill() -> None:
    skill = get_skill("moat")
    assert skill is not None
    assert skill.title.startswith("第二问")
    text = skill.instructions
    for phrase in ("品牌", "成本", "网络效应", "转换成本", "规模",
                   "可证伪", "资料不足", "get_financial_history", "history",
                   "get_peers", "同业"):
        assert phrase in text, phrase
    assert "高毛利" in text and "不是护城河" in text


def test_operating_history_skill() -> None:
    skill = get_skill("operating_history")
    assert skill is not None
    assert skill.title.startswith("第三问")
    text = skill.instructions
    for phrase in ("过去 5", "经营现金流", "自由现金流", "ROE", "ROIC",
                   "跨越周期", "利润质量", "资料不足", "投资收益",
                   "get_financial_history"):
        assert phrase in text, phrase
    assert "不要拿 ROE 冒充" in text


def test_financial_safety_skill() -> None:
    skill = get_skill("financial_safety")
    assert skill is not None
    assert skill.title.startswith("第四问")
    text = skill.instructions
    for phrase in ("有息负债", "利息保障", "短期", "商誉", "应收账款", "存货",
                   "压力测试", "资料不足", "资产负债率", "应付账款",
                   "合同负债", "balance_detail", "get_finance",
                   "get_pledges", "股权质押", "质押比例"):
        assert phrase in text, phrase
    assert "不要机械套用" in text
    assert "口径不可比" in text
    assert "金融机构" in text


def test_growth_outlook_skill() -> None:
    skill = get_skill("growth_outlook")
    assert skill is not None
    assert skill.title.startswith("第五问")
    text = skill.instructions
    for phrase in ("市场空间", "渗透率", "份额", "再投资", "资本开支",
                   "固定资产", "在建工程", "cashflow_detail", "资料不足",
                   "get_segments", "history", "线性外推", "get_peers", "同业",
                   "get_earnings_forecasts", "业绩预告",
                   "get_analyst_views", "一致预期", "卖方"):
        assert phrase in text, phrase
    assert "不要用过去几年的增速外推未来" in text
    assert "不要编数字" in text


def test_management_skill() -> None:
    skill = get_skill("management")
    assert skill is not None
    assert skill.title.startswith("第六问")
    text = skill.instructions
    for phrase in ("诚信", "承诺", "关联交易", "资本配置", "股权激励",
                   "回购", "资料不足", "get_announcements", "get_management",
                   "get_shareholders", "get_dividends", "get_insider_trades",
                   "get_buybacks", "增减持", "持股计划", "分红率",
                   "回购计划", "注销", "薪酬", "持股", "股东户数",
                   "get_lockup_expiries", "限售解禁", "解禁不等于减持",
                   "get_institutional_research", "机构调研", "调研纪要",
                   "get_research_notes", "纪要原文", "自己概括",
                   "红旗"):
        assert phrase in text, phrase
    assert "标题不等于事实" in text
    assert "无法证伪" in text


def test_valuation_skill() -> None:
    skill = get_skill("valuation")
    assert skill is not None
    assert skill.title.startswith("第七问")
    text = skill.instructions
    for phrase in ("PE", "PB", "分位", "自由现金流", "DCF", "目标价",
                   "每股收益", "隐含", "资料不足", "get_valuation",
                   "get_peers", "同业", "rank", "get_dividends", "分红率",
                   "get_buybacks", "注销", "股息率",
                   "get_analyst_views", "一致预期"):
        assert phrase in text, phrase
    assert "不给单一精确目标价" in text
    assert "不要输出 DCF 数字" in text
    assert "calc_valuation" in text
    for phrase in ("check", "scenario", "terminal"):
        assert phrase in text, phrase


def test_margin_of_safety_skill() -> None:
    skill = get_skill("margin_of_safety")
    assert skill is not None
    assert skill.title.startswith("第八问")
    text = skill.instructions
    for phrase in ("内在价值", "安全边际", "折价", "可预测性", "财务风险",
                   "护城河", "资料不足", "买卖建议", "get_valuation"):
        assert phrase in text, phrase
    assert "只做条件式分析" in text
    assert "未加载 valuation 就自行编造估值区间" in text
    assert "load_skill" in text
    assert "calc_valuation" in text


def test_market_view_skill() -> None:
    skill = get_skill("market_view")
    assert skill is not None
    assert "大盘" in skill.title
    text = skill.instructions
    for phrase in ("宽度", "涨跌家数", "量能", "成交额", "MA20", "MA60",
                   "连板", "板块", "盘中", "最近交易日", "情景", "资料不足",
                   "get_market_overview", "不构成投资建议", "触发条件"):
        assert phrase in text, phrase
    assert "不给个股买卖指令" in text
    assert "不预测具体点位" in text


def test_news_events_skill() -> None:
    skill = get_skill("news_events")
    assert skill is not None
    assert "新闻" in skill.title and "海外" in skill.title
    text = skill.instructions
    for phrase in ("政策", "产业", "公司", "海外", "影响路径", "可信度",
                   "不确定性", "发布时间", "get_news_events",
                   "get_announcements", "资料不足", "不构成投资建议"):
        assert phrase in text, phrase
    assert "传闻" in text
    assert "不写传导路径" in text


def test_sector_rotation_skill() -> None:
    skill = get_skill("sector_rotation")
    assert skill is not None
    assert "轮动" in skill.title
    text = skill.instructions
    for phrase in ("超额", "广度", "上涨家数", "持续", "反转信号", "失效条件",
                   "get_sector_rotation", "get_news_events", "资料不足",
                   "不构成投资建议"):
        assert phrase in text, phrase
    assert "一天的涨幅" in text
    assert "领涨股" in text


def test_stock_screening_skill() -> None:
    skill = get_skill("stock_screening")
    assert skill is not None
    assert "筛选" in skill.title
    text = skill.instructions
    for phrase in ("量化", "基本面", "估值", "风险", "需要继续核查的问题",
                   "screen_candidates", "get_financial_history",
                   "get_valuation", "get_announcements", "资料不足",
                   "不构成投资建议"):
        assert phrase in text, phrase
    assert "买入信号" in text
    assert "目标价" in text


def test_strategy_risk_skill() -> None:
    skill = get_skill("strategy_risk")
    assert skill is not None
    assert "仓位" in skill.title
    text = skill.instructions
    for phrase in ("市场状态", "风险承受能力", "当前总仓位", "现金",
                   "持仓", "build_strategy_plan", "caps", "stance",
                   "止损", "触发条件", "观望", "分批", "缺失项",
                   "不构成投资建议"):
        assert phrase in text, phrase
    assert "上限是硬约束" in text
    assert "不要替他假设" in text
    assert "满仓" in text and "清仓" in text
    assert "必涨" in text


def test_review_eval_skill() -> None:
    skill = get_skill("review_eval")
    assert skill is not None
    assert "复盘" in skill.title
    text = skill.instructions
    for phrase in ("命中率", "超额", "最大回撤", "交易成本", "失误", "待观察",
                   "save_daily_judgment", "review_research_reports",
                   "样本量", "不构成投资建议"):
        assert phrase in text, phrase
    assert "不计入命中率" in text
    assert "方法有效" in text


def test_unknown_skill() -> None:
    assert get_skill("q9") is None
    assert get_skill(None) is None


def test_research_memory_skill() -> None:
    skill = get_skill("research_memory")
    assert skill is not None
    text = skill.instructions
    for phrase in ("已验证事实", "待验证假设", "结论变化", "未决问题", "维持", "证伪"):
        assert phrase in text, phrase


def main() -> None:
    test_catalog()
    test_business_model_skill()
    test_moat_skill()
    test_operating_history_skill()
    test_financial_safety_skill()
    test_growth_outlook_skill()
    test_management_skill()
    test_valuation_skill()
    test_margin_of_safety_skill()
    test_market_view_skill()
    test_news_events_skill()
    test_sector_rotation_skill()
    test_stock_screening_skill()
    test_strategy_risk_skill()
    test_review_eval_skill()
    test_research_memory_skill()
    test_unknown_skill()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
