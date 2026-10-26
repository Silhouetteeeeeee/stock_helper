"""把模型回答里的内部引用 id 换成可读编号。

回答正文用 ``[[source-id]]`` 标注引用，来源表由工具登记。Web 页面把
id 按来源顺序换成 ``[1]``、``[2]``，未知 id 显示 ``[?]``；来源行
同时用于回答下方的说明。纯函数，不依赖 Streamlit 或 Qt。
"""

from __future__ import annotations

import re

CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")
MAX_SOURCE_LINES = 6

# 上下文占用环：贴在聊天输入框发送按钮左侧的小圆环，颜色按档位切换
# 输入框右侧留白 1.5rem + 发送按钮约 2.5rem + 一点间距，环就贴在按钮左边
METER_RIGHT = "4.3rem"
METER_SIZE = 22
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


def context_meter_style(usage: dict) -> str:
    """把 ``ResearchAgent.context_usage()`` 的结果画成发送按钮旁的小圆环。

    只输出一段 ``<style>``：圆环与百分比都是聊天输入框容器的伪元素，
    窗口缩放、按钮换位时都跟着输入框走，不用计算列宽。比例以压缩阈值
    （软预算）为分母，超过 100% 时环画满、数字如实显示。
    """
    usage = usage or {}
    ratio = _clamp(float(usage.get("ratio") or 0.0), 0.0, 1.0)
    level = str(usage.get("level") or "normal")
    color = USAGE_COLORS.get(level, USAGE_COLORS["normal"])
    percent = float(usage.get("percent") or 0.0)
    percent_text = f"{percent:.0f}%" if percent < 1000 else "999%+"
    filled = round(ratio * 100, 1)
    size = METER_SIZE
    selector = '[data-testid="stChatInput"]'
    return (
        "<style>"
        f"{selector} {{ position: relative !important; }}"
        f"{selector}::before, {selector}::after {{"
        "position: absolute; top: 50%;"
        f"right: {METER_RIGHT}; width: {size}px; height: {size}px;"
        f"margin-top: -{size / 2}px; border-radius: 50%;"
        "pointer-events: none;"
        "}"
        # 圆环画在 ::before，百分比文字画在 ::after，压住输入框自身的层
        f"{selector}::before {{"
        'content: ""; z-index: 5;'
        f"background: conic-gradient({color} {filled}%, "
        "rgba(127, 127, 127, 0.28) 0);"
        "-webkit-mask: radial-gradient(farthest-side, transparent "
        "calc(100% - 3.5px), #000 calc(100% - 3px));"
        "mask: radial-gradient(farthest-side, transparent "
        "calc(100% - 3.5px), #000 calc(100% - 3px));"
        "}"
        f"{selector}::after {{"
        f'content: "{percent_text}"; z-index: 6;'
        "display: flex; align-items: center; justify-content: center;"
        f"font-size: 8px; font-weight: 600; line-height: 1; color: {color};"
        "}"
        "</style>"
    )


def source_lines(sources, limit: int = MAX_SOURCE_LINES) -> list[str]:
    """来源列表，超出上限时补一行“另有 N 条”。"""
    items = list(sources or [])
    lines = [source_line(source, index) for index, source in enumerate(items[:limit], 1)]
    rest = len(items) - limit
    if rest > 0:
        lines.append(f"… 另有 {rest} 条来源")
    return lines
