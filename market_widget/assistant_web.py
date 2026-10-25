"""把标题栏 AI 按钮接到本地 Web 助手。

启动 Streamlit 要等几秒，放到 QThreadPool 里跑，界面不卡；启动完成后
再交给浏览器打开。窗口每次切换标的都会写一份当前标的状态文件，
页面下次交互时自然读到。
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from assistant import server
from assistant.selection import write_selection


class _OpenSignals(QObject):
    opened = Signal(str)
    failed = Signal(str)


class _OpenTask(QRunnable):
    """后台启动服务并打开浏览器。"""

    def __init__(self, signals: _OpenSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable 接口
        try:
            url = server.ensure_server()
        except Exception as error:  # noqa: BLE001 - 失败要显示给用户
            self._signals.failed.emit(str(error) or "投资助手启动失败")
            return
        try:
            webbrowser.open(url)
        except Exception as error:  # noqa: BLE001 - 浏览器打不开也要提示
            self._signals.failed.emit(f"无法打开浏览器：{error}")
            return
        self._signals.opened.emit(url)


class AssistantWeb(QObject):
    """投资助手 Web 页面的轻量控制器。"""

    opened = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._signals = _OpenSignals(self)
        self._signals.opened.connect(self._on_opened)
        self._signals.failed.connect(self._on_failed)

    def set_company(self, code: str | None, name: str = "", is_index: bool = False) -> None:
        """记录当前标的，供已打开的页面读取。"""
        write_selection(code, name, is_index)

    def open(self) -> None:
        """打开（或聚焦）助手页面；启动过程中重复点击会被忽略。"""
        if self._busy:
            return
        self._busy = True
        QThreadPool.globalInstance().start(_OpenTask(self._signals))

    def is_busy(self) -> bool:
        return self._busy

    def stop(self) -> None:
        """窗口关闭时停掉本进程启动的服务。"""
        server.stop_server()

    def _on_opened(self, url: str) -> None:
        self._busy = False
        self.opened.emit(url)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.failed.emit(message)
