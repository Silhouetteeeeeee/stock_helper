"""引用登记与正文校验。

正文用 ``[[source-id]]`` 标记判断依据。合法的 id 保留原文并登记来源，
未知 id 替换成 ``[?]`` 并记一条 issue；如果整段回答带数字却一条有效
引用都没有，记 ``uncited_numbers`` 提醒——引用表由工具结果生成，
模型只能引用已登记的 id。
"""

from __future__ import annotations

import re

from .models import Source

CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


def validate_citations(
    text: str,
    registry: dict[str, Source],
) -> tuple[str, list[Source], list[dict]]:
    """清洗正文里的引用标记。

    返回 ``(清洗后正文, 按首次出现排序的引用, issues)``。
    """
    used: list[Source] = []
    seen: set[str] = set()
    issues: list[dict] = []

    def replace(match: re.Match) -> str:
        source_id = match.group(1).strip()
        source = registry.get(source_id)
        if source is None:
            issues.append({"type": "unknown_citation", "id": source_id})
            return "[?]"
        if source_id not in seen:
            seen.add(source_id)
            used.append(source)
        return match.group(0)

    clean = CITATION_PATTERN.sub(replace, text or "")
    if not used and re.search(r"\d", clean):
        issues.append({
            "type": "uncited_numbers",
            "message": "回答里有数字，但没有任何有效引用",
        })
    return clean, used, issues
