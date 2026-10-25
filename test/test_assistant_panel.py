"""投资助手面板验证：渲染、标的联动、后台问答与主窗口接线。"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

import market_widget.main_window as mw
from market_widget.ui import assistant_panel as ap


class FakeSource:
    def __init__(self, source_id: str) -> None:
        self.id = source_id
        self.label = "财报"
        self.detail = "东财"
        self.as_of = "2026-06-30"
        self.fetched_at = "2026-10-25 10:00:00"
        self.url = ""


class FakeAnswer:
    text = (
        "**毛利率**下降 [[fin:1]]\n"
        "- 原因一\n"
        "\n"
        "### 结论\n"
        "继续观察 [[fin:2]]"
    )
    citations = [FakeSource("fin:1"), FakeSource("fin:2")]
    trace = [{"name": "get_finance"}]
    issues = []
    rounds = 2


class FakeAgent:
    def __init__(self, code: str) -> None:
        self.default_company = code
        self.asked: list[str] = []

    def ask(self, question: str):
        self.asked.append(question)
        if question == "失败":
            raise RuntimeError("模型超时")
        return FakeAnswer()


def _wait(app: QApplication, panel: ap.AssistantPanel, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while panel.is_busy() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    QThreadPool.globalInstance().waitForDone(2000)


def test_answer_html() -> None:
    markup = ap._answer_html(
        "**毛利率**下降 [[fin:1]]\n- 原因\n### 结论 [[missing]]",
        {"fin:1": 1},
    )
    assert "<b>毛利率</b>" in markup, markup
    assert "[1]" in markup, markup
    assert "[?]" in markup, markup
    assert "• 原因" in markup, markup
    assert "###" not in markup, markup

    empty = ap._answer_html("", {})
    assert "没有返回正文" in empty, empty


def test_company_state(app: QApplication) -> None:
    panel = ap.AssistantPanel(agent_factory=lambda code: FakeAgent(code))
    assert panel._input.isEnabled() is False

    panel.set_company("605499", "东鹏饮料")
    assert panel._input.isEnabled() is True, panel._input.isEnabled()
    assert "东鹏饮料" in panel._title.text(), panel._title.text()

    panel.set_company("sh000001", "上证指数", is_index=True)
    assert panel._input.isEnabled() is False
    assert "指数" in panel._status.text(), panel._status.text()


def test_ask_flow(app: QApplication) -> None:
    agent = FakeAgent("605499")
    panel = ap.AssistantPanel(agent_factory=lambda code: agent)
    panel.set_company("605499", "东鹏饮料")

    panel.ask("它靠什么赚钱")
    assert panel.is_busy() is True
    _wait(app, panel)

    assert agent.asked == ["它靠什么赚钱"], agent.asked
    assert panel.is_busy() is False
    text = panel.conversation_text()
    assert "你：它靠什么赚钱" in text, text
    assert "毛利率下降 [1]" in text, text
    assert "• 原因一" in text, text
    assert "来源：" in text and "[1] 财报" in text, text
    assert "研究过程：1 次取数" in text, text
    assert panel._input.isEnabled() is True

    # 清空后重新开始，下个问题会重建 agent
    panel.clear()
    assert "选中一只股票后提问" in panel.conversation_text()
    assert panel._agent is None


def test_failure_and_missing_key(app: QApplication) -> None:
    panel = ap.AssistantPanel(agent_factory=lambda code: FakeAgent(code))
    panel.set_company("605499", "东鹏饮料")
    panel.ask("失败")
    _wait(app, panel)
    text = panel.conversation_text()
    assert "研究失败：模型超时" in text, text
    assert panel.is_busy() is False
    assert panel._input.isEnabled() is True

    def broken(code: str):
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")

    panel = ap.AssistantPanel(agent_factory=broken)
    panel.set_company("605499", "东鹏饮料")
    panel.ask("它靠什么赚钱")
    assert "助手不可用" in panel._status.text(), panel._status.text()
    assert panel.is_busy() is False


class FakeScheduler(QObject):
    financials_updated = Signal(str)
    sync_finished = Signal(dict)
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class HeadlessWidget(mw.MarketWidget):
    def _apply_geometry(self) -> None:
        pass


def test_window_wiring(app: QApplication) -> None:
    mw.FinanceScheduler = FakeScheduler  # type: ignore[assignment]
    window = HeadlessWidget()

    assert window._assistant_section.isHidden() is True
    code, _name, is_index = window.selected_symbol()
    if is_index:
        assert window._assistant._company is None
    else:
        assert window._assistant._company == code, window._assistant._company

    window.toggle_assistant_panel()
    assert window._assistant_section.isHidden() is False
    assert window._titlebar._assistant_button.isChecked() is True

    window.toggle_assistant_panel()
    assert window._assistant_section.isHidden() is True
    assert window._titlebar._assistant_button.isChecked() is False

    window.closeEvent(QCloseEvent())


def main() -> None:
    app = QApplication.instance() or QApplication([])
    test_answer_html()
    test_company_state(app)
    test_ask_flow(app)
    test_failure_and_missing_key(app)
    test_window_wiring(app)
    app.quit()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
