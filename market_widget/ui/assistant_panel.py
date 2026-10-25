"""投资助手面板：在当前标的上提问，后台跑研究循环，结果显示成对话。

面板只负责交互与渲染：公司来自主窗口当前选中标的，模型与工具都由
``assistant`` 包提供。请求放进 ``QThreadPool``，界面不卡；一次只允许
一个问题在跑，避免同一会话的历史被并发改写。

回答里的 ``[[source-id]]`` 会折叠成 ``[1]`` 这样的编号，来源列在
回答下方；正文按常见 Markdown 记号（标题、列表、加粗）做轻量渲染。
"""

from __future__ import annotations

import html
import re
import time

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..theme import PALETTE, ui_font

WELCOME = "选中一只股票后提问，例如「它靠什么赚钱」「毛利率为什么下滑」"
BUSY_TEXT = "研究中…会调用多个数据工具，通常需要 10–30 秒"
MAX_CITATIONS = 4
CITATION_PATTERN = re.compile(r"\[\[([^\[\]]+)\]\]")


def default_agent_factory(code: str):
    """按需建助手：只有真正提问时才加载 .env 与模型客户端。"""
    from assistant.agent import ResearchAgent, build_client
    from assistant.env import load_env

    load_env()
    return ResearchAgent(build_client(), default_company=code)


class _AskSignals(QObject):
    """跨线程回传结果：QRunnable 自己不能发信号。"""

    answered = Signal(object)
    failed = Signal(str)


class _AskTask(QRunnable):
    """在后台线程里跑一次问答。"""

    def __init__(self, agent, question: str, signals: _AskSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._agent = agent
        self._question = question
        self._signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable 接口
        try:
            answer = self._agent.ask(self._question)
        except Exception as error:  # noqa: BLE001 - 失败要显示在面板里
            self._signals.failed.emit(str(error) or "研究失败")
            return
        self._signals.answered.emit(answer)


class AssistantPanel(QWidget):
    """投资助手对话面板（数据全部来自 ``assistant`` 包）。"""

    def __init__(self, parent: QWidget | None = None, agent_factory=None) -> None:
        super().__init__(parent)
        self._agent = None
        self._agent_factory = agent_factory or default_agent_factory
        self._company: str | None = None
        self._company_name = ""
        self._pending_company: str | None = None
        self._busy = False
        self._started_at = 0.0
        self._welcome_shown = False

        self._signals = _AskSignals(self)
        self._signals.answered.connect(self._on_answered)
        self._signals.failed.connect(self._on_failed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        self._title = QLabel("投资助手")
        self._title.setFont(ui_font(10))
        self._title.setStyleSheet(f"color: {PALETTE.text_muted};")
        self._clear_button = QPushButton("清空")
        self._clear_button.setObjectName("GhostButton")
        self._clear_button.setCursor(Qt.PointingHandCursor)
        self._clear_button.setToolTip("清空对话并重置上下文")
        self._clear_button.clicked.connect(self.clear)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._clear_button)
        layout.addLayout(header)

        self._view = QTextBrowser(self)
        self._view.setObjectName("AssistantView")
        self._view.setOpenExternalLinks(True)
        self._view.setFont(ui_font(10))
        self._view.setMinimumHeight(120)
        self._view.setMaximumHeight(240)
        layout.addWidget(self._view)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("问一句，如：毛利率为什么下滑？")
        self._input.setFont(ui_font(10))
        self._input.returnPressed.connect(self.ask)
        self._send_button = QPushButton("提问")
        self._send_button.setObjectName("PrimaryButton")
        self._send_button.setCursor(Qt.PointingHandCursor)
        self._send_button.clicked.connect(self.ask)
        row.addWidget(self._input, 1)
        row.addWidget(self._send_button)
        layout.addLayout(row)

        self._status = QLabel("")
        self._status.setFont(ui_font(9))
        self._status.setStyleSheet(f"color: {PALETTE.text_muted};")
        layout.addWidget(self._status)

        self.set_company(None, "")
        self._show_welcome()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_company(
        self,
        code: str | None,
        name: str = "",
        is_index: bool = False,
    ) -> None:
        """跟随主窗口选中标的；指数或空标的时禁用提问。"""
        if is_index or not code:
            self._company = None
            self._company_name = name or ""
            self._pending_company = None
            self._title.setText("投资助手")
            self._set_enabled(False)
            self._status.setText("指数没有财报数据，选一只股票再提问")
            return

        self._company = code
        self._company_name = name or code
        self._title.setText(f"投资助手 · {self._company_name}")
        self._set_enabled(True)
        if not self._busy:
            self._status.setText("")
            if self._agent is not None:
                self._agent.default_company = code
        else:
            # 研究中不打断会话，等这一轮结束再切默认公司
            self._pending_company = code

    def clear(self) -> None:
        """清空对话并丢掉会话历史（下次提问重新建 agent）。"""
        self._agent = None
        self._pending_company = None
        self._welcome_shown = False
        self._view.clear()
        if not self._busy:
            self._status.setText("")
            self._show_welcome()

    def ask(self, question: str | None = None) -> None:
        """发一个问题；忙碌或未选中股票时给出提示而不是排队。"""
        text = (question if question is not None else self._input.text()).strip()
        if not text:
            return
        if self._busy:
            self._status.setText("上一个问题还在研究，请稍等")
            return
        if not self._company:
            self._status.setText("先选一只股票再提问")
            return

        agent = self._ensure_agent()
        if agent is None:
            return

        self._input.clear()
        self._append_user(text)
        self._set_busy(True)
        QThreadPool.globalInstance().start(
            _AskTask(agent, text, self._signals)
        )

    def is_busy(self) -> bool:
        return self._busy

    def conversation_text(self) -> str:
        """当前对话的纯文本，供测试与调试。"""
        return self._view.toPlainText()

    # ------------------------------------------------------------------
    # 内部：线程与渲染
    # ------------------------------------------------------------------
    def _ensure_agent(self):
        if self._agent is None:
            try:
                self._agent = self._agent_factory(self._company or "")
            except Exception as error:  # noqa: BLE001 - 缺 key 等要显示出来
                self._status.setText(f"助手不可用：{error}")
                return None
        else:
            self._agent.default_company = self._company
        return self._agent

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._set_enabled(not busy)
        self._clear_button.setEnabled(not busy)
        if busy:
            self._started_at = time.monotonic()
            self._status.setText(BUSY_TEXT)
        else:
            elapsed = max(1, round(time.monotonic() - self._started_at))
            self._status.setText(f"用时 {elapsed} 秒")

    def _set_enabled(self, enabled: bool) -> None:
        has_company = self._company is not None
        self._input.setEnabled(enabled and has_company)
        self._send_button.setEnabled(enabled and has_company)

    def _on_answered(self, answer) -> None:
        self._set_busy(False)
        self._append_answer(answer)
        if self._pending_company and self._agent is not None:
            self._agent.default_company = self._pending_company
            self._pending_company = None
        self._input.setFocus()

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self._append(
            f'<div style="color: {PALETTE.danger}; margin-top: 6px;">'
            f"研究失败：{html.escape(message)}</div>"
        )
        self._pending_company = None

    def _append_user(self, text: str) -> None:
        self._append(
            f'<div style="color: {PALETTE.accent}; margin-top: 8px;">'
            f"你：{html.escape(text)}</div>"
        )

    def _append_answer(self, answer) -> None:
        citations = list(getattr(answer, "citations", []) or [])
        mapping = {
            source.id: index
            for index, source in enumerate(citations, 1)
        }
        body = _answer_html(getattr(answer, "text", "") or "", mapping)
        self._append(f'<div style="margin-top: 4px;">{body}</div>')

        if citations:
            self._append(_citations_html(citations))
        steps = getattr(answer, "trace", []) or []
        if steps:
            self._append(
                f'<div style="color: {PALETTE.text_muted}; margin-top: 2px;">'
                f"研究过程：{len(steps)} 次取数</div>"
            )
        for issue in (getattr(answer, "issues", []) or [])[:2]:
            text = issue.get("message") or issue.get("type") or ""
            if text:
                self._append(
                    f'<div style="color: {PALETTE.text_muted};">'
                    f"提示：{html.escape(str(text))}</div>"
                )

    def _append(self, markup: str) -> None:
        if not self._welcome_shown:
            self._view.clear()
            self._welcome_shown = True
        self._view.append(markup)
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _show_welcome(self) -> None:
        self._welcome_shown = False
        self._view.setHtml(
            f'<div style="color: {PALETTE.text_muted};">{html.escape(WELCOME)}</div>'
        )


def _answer_html(text: str, mapping: dict[str, int]) -> str:
    """轻量渲染回答：去掉标题/列表记号，保留加粗与引用编号。"""
    parts: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            parts.append('<div style="height: 4px;"></div>')
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*]\s+", "• ", line)
        escaped = html.escape(line)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = CITATION_PATTERN.sub(
            lambda match: (
                f'<span style="color: {PALETTE.accent};">'
                f"[{mapping.get(match.group(1).strip(), '?')}]</span>"
            ),
            escaped,
        )
        parts.append(f'<div style="margin-top: 1px;">{escaped}</div>')
    return "".join(parts) or (
        f'<div style="color: {PALETTE.text_muted};">（没有返回正文）</div>'
    )


def _citations_html(citations: list) -> str:
    lines = []
    for index, source in enumerate(citations[:MAX_CITATIONS], 1):
        detail = " · ".join(
            part
            for part in (
                getattr(source, "label", ""),
                getattr(source, "detail", ""),
                getattr(source, "as_of", ""),
            )
            if part
        )
        lines.append(
            f'<div>[{index}] {html.escape(detail or source.id)}</div>'
        )
    rest = len(citations) - MAX_CITATIONS
    if rest > 0:
        lines.append(f"<div>… 另有 {rest} 条来源</div>")
    return (
        f'<div style="color: {PALETTE.text_muted}; margin-top: 2px;">'
        f"来源：{''.join(lines)}</div>"
    )
