"""Web 助手接线：状态文件、后台启动与 AI 按钮忙碌态。

不构造主窗口（离屏环境下主窗口测试会偶发退出段错误，项目里已有先例），
只验证真正有分支的胶水代码。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 必须在导入 assistant.selection 之前指向临时状态文件
os.environ["MARKET_ASSISTANT_STATE"] = str(
    Path(tempfile.mkdtemp(prefix="assistant_wiring_")) / "state.json"
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from market_widget import assistant_web
from market_widget.ui.titlebar import TitleBar
from assistant.selection import read_selection


def _drain(app: QApplication) -> None:
    QThreadPool.globalInstance().waitForDone(3000)
    app.processEvents()


def test_glue(app: QApplication) -> None:
    opened: list[str] = []
    browsed: list[str] = []
    failed: list[str] = []
    assistant_web.server.ensure_server = lambda: "http://127.0.0.1:8501/"
    assistant_web.webbrowser.open = lambda url: browsed.append(url) or True

    glue = assistant_web.AssistantWeb()
    glue.opened.connect(opened.append)
    glue.failed.connect(failed.append)

    glue.set_company("605499", "东鹏饮料")
    state = read_selection()
    assert state is not None
    assert state["code"] == "605499" and state["name"] == "东鹏饮料"
    assert state["is_index"] is False

    glue.open()
    assert glue.is_busy() is True
    glue.open()  # 启动中重复点击不应再起一个任务
    _drain(app)
    assert browsed == ["http://127.0.0.1:8501/"], browsed
    assert opened == ["http://127.0.0.1:8501/"], opened
    assert glue.is_busy() is False

    # 切换成指数也照样落盘（页面不读它，这里只验证桥接本身不出错）
    glue.set_company("sh000001", "上证指数", is_index=True)
    state = read_selection()
    assert state is not None and state["is_index"] is True

    # 启动失败走 failed 信号，忙碌态要复位
    def broken() -> str:
        raise RuntimeError("投资助手启动失败")

    assistant_web.server.ensure_server = broken
    glue.open()
    _drain(app)
    assert failed == ["投资助手启动失败"], failed
    assert glue.is_busy() is False


def test_titlebar_busy() -> None:
    bar = TitleBar("行情速览")
    bar.set_assistant_busy(True)
    assert bar._assistant_button.isEnabled() is False
    assert "启动" in bar._assistant_button.toolTip()
    bar.set_assistant_busy(False)
    assert bar._assistant_button.isEnabled() is True
    assert "浏览器" in bar._assistant_button.toolTip()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    test_glue(app)
    test_titlebar_busy()
    app.quit()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
