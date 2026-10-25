"""回答审计：未知引用、跨公司引用与 skill 路由的硬校验。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.audit import audit_answer, company_of, loaded_skills
from assistant.models import Source


def _source(source_id: str) -> Source:
    return Source(source_id, "财报", "明细", "2026-09-30", "2026-10-25 10:00:00")


def _message(sources=(), trace=(), issues=()):
    return {
        "text": "",
        "sources": [source.to_dict() for source in sources],
        "trace": list(trace),
        "issues": list(issues),
    }


def _skill(name: str, ok: bool = True) -> dict:
    return {"name": "load_skill", "arguments": {"name": name}, "ok": ok}


def test_company_of() -> None:
    assert company_of("profile:601899") == "601899"
    assert company_of("finance:601899:2026-09-30") == "601899"
    assert company_of("finance_history:601899:2016-2025") == "601899"
    assert company_of("announcement:601899:2026-10-23:AN1") == "601899"
    assert company_of("index:sh000001:2026-09-30") == "sh000001"
    assert company_of("macro:ppi") is None
    assert company_of("nope") is None
    assert company_of("") is None


def test_loaded_skills() -> None:
    trace = [
        _skill("business_model"),
        {"name": "get_finance", "arguments": {}, "ok": True},
        _skill("business_model"),
        _skill("moat", ok=False),
        _skill("moat"),
        {"name": "load_skill", "arguments": {}, "ok": True},
    ]
    assert loaded_skills(trace) == ["business_model", "moat"]
    # 旧数据把研究过程存成次数，不能当成步骤列表
    assert loaded_skills(2) == []


def test_clean_answer() -> None:
    result = audit_answer(
        _message(
            sources=[_source("finance:601899:2026-09-30"),
                     _source("segments:601899:2026-06-30:product")],
            trace=[_skill("financial_safety")],
        ),
        expected_skills=["financial_safety"],
        allowed_companies=["601899"],
    )
    assert result["ok"] is True, result
    assert result["citations"] == 2
    assert result["companies_cited"] == ["601899", "601899"]
    assert result["skills_loaded"] == ["financial_safety"]
    assert result["routing_checked"] is True and result["routing_ok"] is True


def test_unknown_citation() -> None:
    bad = "finance:601899:2099-12-31"
    result = audit_answer(
        _message(
            sources=[_source("finance:601899:2026-09-30")],
            trace=[_skill("financial_safety")],
            issues=[{"type": "unknown_citation", "id": bad},
                    {"type": "unknown_citation", "id": bad}],
        ),
        expected_skills=["financial_safety"],
        allowed_companies=["601899"],
    )
    assert result["ok"] is False
    assert result["citations_ok"] is False
    assert result["unknown_citations"] == [bad]
    assert result["unknown_occurrences"] == 2


def test_cross_company() -> None:
    maotai = "finance:600519:2026-09-30"
    message = _message(
        sources=[_source("finance:601899:2026-09-30"), _source(maotai)],
        trace=[_skill("moat")],
    )
    single = audit_answer(
        message, expected_skills=["moat"], allowed_companies=["601899"]
    )
    assert single["ok"] is False
    assert single["cross_company"] == [maotai]

    compare = audit_answer(
        message,
        expected_skills=["moat"],
        allowed_companies=["601899", "600519"],
    )
    assert compare["ok"] is True, compare
    assert compare["cross_company"] == []


def test_routing() -> None:
    message = _message(
        sources=[_source("finance:601899:2026-09-30")],
        trace=[_skill("business_model"), _skill("moat")],
    )
    hit = audit_answer(message, expected_skills=["moat"], allowed_companies=["601899"])
    assert hit["ok"] is True, hit
    assert hit["skills_extra"] == ["business_model"]

    missing = audit_answer(
        message, expected_skills=["valuation"], allowed_companies=["601899"]
    )
    assert missing["ok"] is False
    assert missing["skills_missing"] == ["valuation"]

    # 没加载任何 skill 就作答
    none = audit_answer(
        _message(sources=[_source("finance:601899:2026-09-30")]),
        expected_skills=["valuation"],
        allowed_companies=["601899"],
    )
    assert none["ok"] is False and none["skills_loaded"] == []

    # 路由没给期望时不检查，也不因此判失败
    unchecked = audit_answer(
        _message(sources=[_source("finance:601899:2026-09-30")]),
        allowed_companies=["601899"],
    )
    assert unchecked["routing_checked"] is False
    assert unchecked["routing_ok"] is None
    assert unchecked["ok"] is True


def test_answer_object_and_legacy_message() -> None:
    class Answer:
        citations = [_source("finance:601899:2026-09-30")]
        trace = [_skill("financial_safety")]
        issues = [{"type": "uncited_numbers", "message": "有数字没引用"}]

    result = audit_answer(
        Answer(), expected_skills=["financial_safety"], allowed_companies=["601899"]
    )
    assert result["ok"] is True, result
    # 未引用数字只做提示，不进入硬校验
    assert result["uncited_numbers"] is True

    # 旧消息没有 sources/trace，只有次数
    legacy = audit_answer(
        {"text": "旧回答", "trace": 2, "issues": []},
        allowed_companies=["601899"],
    )
    assert legacy["ok"] is True
    assert legacy["citations"] == 0
    assert legacy["routing_checked"] is False


def test_id_without_company_is_reported() -> None:
    result = audit_answer(
        _message(sources=[_source("macro:ppi")], trace=[_skill("growth_outlook")]),
        expected_skills=["growth_outlook"],
        allowed_companies=["601899"],
    )
    assert result["id_without_company"] == ["macro:ppi"]
    # 已登记但解析不出公司，先只提示，不判死
    assert result["ok"] is True, result


def main() -> None:
    test_company_of()
    test_loaded_skills()
    test_clean_answer()
    test_unknown_citation()
    test_cross_company()
    test_routing()
    test_answer_object_and_legacy_message()
    test_id_without_company_is_reported()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
