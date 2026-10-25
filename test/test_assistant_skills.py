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
                   "投资收益", "合同负债", "income_detail", "history"):
        assert phrase in text, phrase


def test_moat_skill() -> None:
    skill = get_skill("moat")
    assert skill is not None
    assert skill.title.startswith("第二问")
    text = skill.instructions
    for phrase in ("品牌", "成本", "网络效应", "转换成本", "规模",
                   "可证伪", "资料不足", "get_financial_history", "history"):
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
                   "合同负债", "balance_detail", "get_finance"):
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
                   "get_segments", "history", "线性外推"):
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
                   "get_shareholders", "薪酬", "持股", "股东户数", "红旗"):
        assert phrase in text, phrase
    assert "标题不等于事实" in text
    assert "无法证伪" in text


def test_valuation_skill() -> None:
    skill = get_skill("valuation")
    assert skill is not None
    assert skill.title.startswith("第七问")
    text = skill.instructions
    for phrase in ("PE", "PB", "分位", "自由现金流", "DCF", "目标价",
                   "每股收益", "隐含", "资料不足", "get_valuation"):
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


def test_unknown_skill() -> None:
    assert get_skill("q9") is None
    assert get_skill(None) is None


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
    test_unknown_skill()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
