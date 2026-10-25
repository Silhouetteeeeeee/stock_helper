"""回答审计：引用与 skill 路由的硬校验。

只做能确定性判定的检查，不做语义评估：

- 未知引用：``validate_citations`` 记下的 ``unknown_citation``；
- 跨公司引用：引用 id 里的公司代码不在允许范围（对照题传多家）；
- skill 路由：期望的 skill 是否真的通过 ``load_skill`` 加载了。

既接受实时 ``Answer``，也接受落库的消息 dict（``sources`` 为 dict）。
纯函数，不联网、不写库。
"""

from __future__ import annotations

import re

from .models import Source

# 股票代码是六位数字，指数可能带 sh/sz/bj 前缀；其他形态不当成公司
CODE_PATTERN = re.compile(r"^(?:sh|sz|bj)?\d{6}$", re.IGNORECASE)


def _field(item, name: str, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def company_of(source_id: str) -> str | None:
    """从引用 id 里取公司代码；id 规则是 ``工具:代码:日期...``。"""
    parts = (source_id or "").split(":")
    if len(parts) < 2:
        return None
    code = parts[1].strip()
    return code if code and CODE_PATTERN.match(code) else None


def source_id_of(source) -> str:
    if isinstance(source, Source):
        return source.id
    if isinstance(source, dict):
        return str(source.get("id") or "")
    return str(source or "")


def loaded_skills(trace) -> list[str]:
    """按出现顺序去重，只算成功的 ``load_skill``。"""
    skills: list[str] = []
    for step in trace if isinstance(trace, list) else []:
        if not isinstance(step, dict):
            continue
        if step.get("name") != "load_skill" or step.get("ok") is False:
            continue
        arguments = step.get("arguments")
        name = str((arguments or {}).get("name") or "").strip()
        if name and name not in skills:
            skills.append(name)
    return skills


def audit_answer(
    answer,
    expected_skills=(),
    allowed_companies=(),
) -> dict:
    """审计一条回答，返回可直接写进报告的明细。

    ``answer`` 可以是 ``Answer``，也可以是含 ``text/sources/trace/issues``
    的 dict。``expected_skills`` 为空时不检查路由（``routing_ok`` 为 None）。
    """
    issues = [
        item for item in (_field(answer, "issues", None) or [])
        if isinstance(item, dict)
    ]
    trace = _field(answer, "trace", None)
    sources = _field(answer, "citations", None)
    if sources is None:
        sources = _field(answer, "sources", None) or []
    ids = [source_id_of(source) for source in sources if source_id_of(source)]

    unknown: list[str] = []
    unknown_occurrences = 0
    for issue in issues:
        if issue.get("type") != "unknown_citation":
            continue
        unknown_occurrences += 1
        bad_id = str(issue.get("id") or "").strip()
        if bad_id and bad_id not in unknown:
            unknown.append(bad_id)

    allowed = {str(code).strip() for code in (allowed_companies or []) if str(code).strip()}
    companies = [company_of(source_id) for source_id in ids]
    cross_company = [
        source_id
        for source_id, code in zip(ids, companies)
        if allowed and code and code not in allowed
    ]
    id_without_company = [
        source_id for source_id, code in zip(ids, companies) if not code
    ]

    expected = [str(name).strip() for name in (expected_skills or []) if str(name).strip()]
    loaded = loaded_skills(trace)
    missing = [name for name in expected if name not in loaded]
    extra = [name for name in loaded if name not in expected]

    citations_ok = not unknown and not cross_company
    routing_checked = bool(expected)
    routing_ok = (not missing) if routing_checked else None
    return {
        "citations": len(ids),
        "companies_cited": [code for code in companies if code],
        "unknown_citations": unknown,
        "unknown_occurrences": unknown_occurrences,
        "cross_company": cross_company,
        "id_without_company": id_without_company,
        "skills_expected": expected,
        "skills_loaded": loaded,
        "skills_missing": missing,
        "skills_extra": extra,
        "uncited_numbers": any(
            issue.get("type") == "uncited_numbers" for issue in issues
        ),
        "rounds_exhausted": any(
            issue.get("type") == "rounds_exhausted" for issue in issues
        ),
        "citations_ok": citations_ok,
        "routing_checked": routing_checked,
        "routing_ok": routing_ok,
        "ok": citations_ok and routing_ok is not False,
    }
