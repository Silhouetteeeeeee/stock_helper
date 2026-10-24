"""基本面速览：选中股票时展示财报、估值与近期公告的关键几行。

数据由外部注入（``show_briefing`` 接收 ``stock.briefing`` 的产物），
面板本身不抓数据、不判断过期——标的切换时由窗口决定丢弃哪些结果。

320 像素宽放不下全部提示，面板只显示前两条并适度截断；
悬停时通过 tooltip 给出完整内容（即 ``briefing['summary']``）。
公告列表渲染成链接，点击用系统浏览器打开东财详情页。
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import PALETTE, ui_font

# 窄窗口里提示只留最重要的几条，完整内容在 briefing 的纯文本里
MAX_ALERTS = 2
ALERT_MAX_WIDTH = 34   # 估算的显示宽度：汉字算 2，西文算 1
ALERT_COLORS = {"warning": PALETTE.avg, "info": PALETTE.text_muted}
ALERT_LABELS = {"warning": "异常", "info": "关注"}

# 面板底部最多放几条公告链接
ANNOUNCEMENT_LINKS = 3
LINK_MAX_WIDTH = 30


class FundamentalsPanel(QWidget):
    """基本面摘要面板（只负责显示）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._body = QLabel(self)
        self._body.setObjectName("FundamentalsBody")
        self._body.setTextFormat(Qt.RichText)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._body.setFont(ui_font(10))
        self._body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        # 公告链接交给系统浏览器打开
        self._body.setOpenExternalLinks(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 10)
        layout.setSpacing(0)
        layout.addWidget(self._body)

    # -- 数据入口 ---------------------------------------------------------
    def show_loading(self, text: str = "加载中…") -> None:
        self._body.setText(self._line(text, PALETTE.text_muted))
        self._set_tooltip("")

    def show_message(self, text: str) -> None:
        """占位提示：指数没有财报、取数失败等。"""
        self._body.setText(self._line(text, PALETTE.text_muted))
        self._set_tooltip("")

    def show_briefing(self, briefing: dict) -> None:
        """渲染 ``stock.briefing.briefing_from`` 的结果。"""
        if not briefing.get("available"):
            self.show_message(briefing.get("summary") or "暂无财报与估值数据")
            return

        parts = [self._line(briefing.get("headline") or "", PALETTE.text_muted)]
        for line in briefing.get("lines") or []:
            parts.append(self._line(line, PALETTE.text))
        for alert in (briefing.get("alerts") or [])[:MAX_ALERTS]:
            label = ALERT_LABELS.get(alert.get("level"), alert.get("level", ""))
            color = ALERT_COLORS.get(alert.get("level"), PALETTE.text_muted)
            text = _shorten(alert.get("text", ""), ALERT_MAX_WIDTH)
            parts.append(self._line(f"{label}：{text}", color))
        parts.extend(self._announcement_parts(briefing))
        self._body.setText("".join(part for part in parts if part))
        # 窄面板只放得下两条提示；悬停时给出不截断的完整内容
        self._set_tooltip(_tooltip(briefing))

    def text(self) -> str:
        """当前展示的纯文本，供测试断言。"""
        return self._body.text()

    # -- 内部实现 ---------------------------------------------------------
    def _line(self, text: str, color: str) -> str:
        if not text:
            return ""
        return f'<div style="color: {color}; margin-top: 2px;">{html.escape(text)}</div>'

    def _set_tooltip(self, text: str) -> None:
        # 面板本身和标签都挂上：鼠标停在任意位置都能看到完整内容
        self.setToolTip(text)
        self._body.setToolTip(text)

    def _announcement_parts(self, briefing: dict) -> list[str]:
        """底部公告链接：优先带标签的事件，同类只露最新一条。"""
        announcements = briefing.get("announcements") or {}
        if not announcements.get("available"):
            return []
        items = _panel_announcements(announcements, ANNOUNCEMENT_LINKS)
        if not items:
            return []

        parts = [self._line("近期公告（点击查看）", PALETTE.text_muted)]
        parts.extend(self._link(item) for item in items)
        return parts

    def _link(self, item: dict) -> str:
        text = html.escape(_link_text(item))
        url = html.escape(item.get("url") or "", quote=True)
        if not url:
            return self._line(_link_text(item), PALETTE.text)
        return (
            '<div style="margin-top: 2px;">'
            f'<a href="{url}" style="text-decoration: none;">'
            f'<span style="color: {PALETTE.accent};">{text}</span>'
            "</a></div>"
        )


def _tooltip(briefing: dict) -> str:
    """悬停提示：提示条目不截断、也不受 ``MAX_ALERTS`` 限制。"""
    summary = briefing.get("summary")
    if summary:
        return summary

    parts = [briefing.get("headline") or ""]
    parts.extend(briefing.get("lines") or [])
    for alert in briefing.get("alerts") or []:
        label = ALERT_LABELS.get(alert.get("level"), alert.get("level", ""))
        parts.append(f"{label}：{alert.get('text', '')}")
    return "\n".join(part for part in parts if part)


def _shorten(text: str, max_width: int) -> str:
    """提示文案在窄面板里只留前半句，完整内容在 briefing 的纯文本里。

    按显示宽度估算截断位置：汉字占两个西文字符的宽度，只数字符数会把
    中文句子留得太长而换行。
    """
    width = 0
    for index, char in enumerate(text):
        width += 2 if ord(char) > 0x2E80 else 1
        if width > max_width:
            return text[:index].rstrip("，、；： ") + "…"
    return text


def _panel_announcements(announcements: dict, limit: int) -> list[dict]:
    """挑面板上展示的公告：先按标签去重取最新，再补最新的普通公告。"""
    items = announcements.get("items") or []
    chosen: list[dict] = []
    seen_tags: set[str] = set()
    for item in items:
        tags = item.get("tags") or []
        if not tags or tags[0] in seen_tags:
            continue
        seen_tags.add(tags[0])
        chosen.append(item)
        if len(chosen) >= limit:
            break

    # 先用没标签的普通公告补齐，避免同一类事件占掉两行
    seen_ids = {id(item) for item in chosen}
    for item in items:
        if len(chosen) >= limit:
            break
        if id(item) in seen_ids or item.get("tags"):
            continue
        chosen.append(item)
        seen_ids.add(id(item))

    # 还不够（比如全是同类公告）就直接按时间补
    for item in items:
        if len(chosen) >= limit:
            break
        if id(item) in seen_ids:
            continue
        chosen.append(item)
        seen_ids.add(id(item))

    chosen.sort(key=lambda item: item.get("date") or "", reverse=True)
    return chosen


def _link_text(item: dict) -> str:
    tags = item.get("tags") or []
    tag = f"[{tags[0]}] " if tags else ""
    date = item.get("date") or ""
    return f"{date[5:]} {tag}{_shorten(item.get('title') or '', LINK_MAX_WIDTH)}"
