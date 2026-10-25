"""把模型回答里的内部引用 id 换成可读编号。

回答正文用 ``[[source-id]]`` 标注引用，来源表由工具登记。Web 页面把
id 按来源顺序换成 ``[1]``、``[2]``，未知 id 显示 ``[?]``；来源行
同时用于回答下方的说明。纯函数，不依赖 Streamlit 或 Qt。
"""

from __future__ import annotations

import re

CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")
MAX_SOURCE_LINES = 6


def _field(source, name: str) -> str:
    """来源既可能是 ``Source`` 对象，也可能是存进会话的 dict。"""
    value = source.get(name, "") if isinstance(source, dict) else getattr(source, name, "")
    return str(value or "")


def citation_ids(sources) -> dict[str, int]:
    """来源 id → 展示编号（从 1 开始，先到先得）。"""
    mapping: dict[str, int] = {}
    for source in sources or []:
        source_id = _field(source, "id")
        if source_id and source_id not in mapping:
            mapping[source_id] = len(mapping) + 1
    return mapping


def cite_text(text: str, sources) -> str:
    """把 ``[[id]]`` 换成 ``[n]``，未登记的引用显示 ``[?]``。"""
    mapping = citation_ids(sources)

    def replace(match: re.Match) -> str:
        ref = match.group(1).strip()
        return f"[{mapping.get(ref, '?')}]"

    return CITATION_PATTERN.sub(replace, text or "")


def stream_text(text: str) -> str:
    """流式过程中的正文：先藏起内部 id，收尾时再换成角标。"""
    return CITATION_PATTERN.sub("", text or "")


def _escape_html(text: str) -> str:
    """转义模型正文里的标签，但保留 ``>``，别把 Markdown 引用块弄坏。"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;")


def source_tip(source) -> str:
    """悬停提示：来源类型、明细与数据日期。"""
    detail = " · ".join(
        part
        for part in (
            _field(source, "label"),
            _field(source, "detail"),
            _field(source, "as_of"),
        )
        if part
    )
    return detail or _field(source, "id") or "未知来源"


def citation_html(text: str, sources) -> str:
    """把 ``[[id]]`` 换成可悬停的 ``<sup>``；正文转义后再插入 HTML。

    角标编号与 ``cite_text`` 一致，悬停内容放在 ``data-tip``，由页面
    的 CSS 负责展示。
    """
    indexed: dict[str, tuple[int, object]] = {}
    for source in sources or []:
        source_id = _field(source, "id")
        if source_id and source_id not in indexed:
            indexed[source_id] = (len(indexed) + 1, source)

    def replace(match: re.Match) -> str:
        ref = match.group(1).strip()
        entry = indexed.get(ref)
        if entry is None:
            tip = "来源未登记"
            index = "?"
        else:
            index, source = entry
            tip = source_tip(source)
        tip = (
            tip.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace('"', "&quot;")
        )
        return f'<sup class="cite" data-tip="{tip}">[{index}]</sup>'

    return CITATION_PATTERN.sub(replace, _escape_html(text))


def source_line(source, index: int) -> str:
    """一条来源：``[1] 财报 · 东财 · 2026-06-30``。"""
    detail = " · ".join(
        part
        for part in (
            _field(source, "label"),
            _field(source, "detail"),
            _field(source, "as_of"),
        )
        if part
    )
    return f"[{index}] {detail or _field(source, 'id') or '未知来源'}"


def source_lines(sources, limit: int = MAX_SOURCE_LINES) -> list[str]:
    """来源列表，超出上限时补一行“另有 N 条”。"""
    items = list(sources or [])
    lines = [source_line(source, index) for index, source in enumerate(items[:limit], 1)]
    rest = len(items) - limit
    if rest > 0:
        lines.append(f"… 另有 {rest} 条来源")
    return lines
