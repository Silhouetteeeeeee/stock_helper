"""把模型回答里的内部引用 id 换成可读编号。

回答正文用 ``[[source-id]]`` 标注引用，来源表由工具登记。Web 页面把
id 按来源顺序换成 ``[1]``、``[2]``，未知 id 显示 ``[?]``；来源行
同时用于回答下方的说明。纯函数，不依赖 Streamlit 或 Qt。
"""

from __future__ import annotations

import math
import re

CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")
MAX_SOURCE_LINES = 6

# 上下文占用环：r=46 的圆形，周长约为 289；颜色按占用档位切换
RING_RADIUS = 46
RING_CIRCUMFERENCE = 2 * math.pi * RING_RADIUS
USAGE_COLORS = {"normal": "#4c8dff", "warn": "#e8a33d", "over": "#e5534b"}


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


def _clamp(value: float, low: float, high: float) -> float:
    if value != value:  # NaN
        return low
    return max(low, min(high, value))


def _token_label(tokens: int) -> str:
    """23,100 → 23.1k；小于一千就原样显示。"""
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(int(tokens))


def context_ring_html(usage: dict) -> str:
    """把 ``ResearchAgent.context_usage()`` 的结果画成一个占用环。

    纯字符串拼接，不依赖 Streamlit；比例以压缩阈值（软预算）为分母，
    超过 100% 时环画满、中间数字如实显示。
    """
    usage = usage or {}
    ratio = _clamp(float(usage.get("ratio") or 0.0), 0.0, 1.0)
    level = str(usage.get("level") or "normal")
    color = USAGE_COLORS.get(level, USAGE_COLORS["normal"])
    offset = round(RING_CIRCUMFERENCE * (1 - ratio), 1)
    percent = float(usage.get("percent") or 0.0)
    percent_text = f"{percent:.0f}%" if percent < 1000 else "999%+"
    detail = (
        f"上下文 {_token_label(int(usage.get('tokens') or 0))} / "
        f"{_token_label(int(usage.get('budget_tokens') or 0))}"
    )
    hint = (
        "已超过上下文预算，下次提问会先压缩较早的取数结果"
        if level == "over"
        else detail
    )
    return (
        '<div class="ctx-meter" title="' + hint + '" style="display:flex;'
        'flex-direction:column;align-items:center;gap:.2rem;margin:.4rem 0 .6rem;">'
        '<svg width="112" height="112" viewBox="0 0 112 112" role="img" '
        f'aria-label="{detail}">'
        f'<circle cx="56" cy="56" r="{RING_RADIUS}" fill="none" '
        'stroke="currentColor" stroke-opacity="0.16" stroke-width="10"/>'
        f'<circle cx="56" cy="56" r="{RING_RADIUS}" fill="none" stroke="{color}" '
        'stroke-width="10" stroke-linecap="round" '
        f'stroke-dasharray="{RING_CIRCUMFERENCE:.1f}" '
        f'stroke-dashoffset="{offset}" transform="rotate(-90 56 56)"/>'
        '<text x="56" y="62" text-anchor="middle" font-size="20" '
        f'font-weight="600" fill="currentColor">{percent_text}</text>'
        '</svg>'
        f'<div style="font-size:.72rem;opacity:.72;">{detail}</div>'
        '</div>'
    )


def source_lines(sources, limit: int = MAX_SOURCE_LINES) -> list[str]:
    """来源列表，超出上限时补一行“另有 N 条”。"""
    items = list(sources or [])
    lines = [source_line(source, index) for index, source in enumerate(items[:limit], 1)]
    rest = len(items) - limit
    if rest > 0:
        lines.append(f"… 另有 {rest} 条来源")
    return lines
