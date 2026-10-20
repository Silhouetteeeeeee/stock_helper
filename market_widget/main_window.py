"""主窗口：右上角常驻悬浮窗，负责组装各面板并提供数据入口。

对外接口（供数据层调用）：
    ``window.set_index_quotes(quotes)``   刷新大盘指数
    ``window.set_stock_quotes(quotes)``   刷新自选股
    ``window.push_quotes(quotes)``        指数与自选股一起刷新
    ``window.set_watchlist(items)``       重建自选股列表
    ``window.data_requested``             信号：界面需要一份新数据时发出
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .config import (
    ALWAYS_ON_TOP,
    DEFAULT_WATCHLIST,
    INDEX_NAMES,
    MINIMIZED_HEIGHT,
    OPACITY,
    START_MINIMIZED,
    WINDOW_MARGIN_X,
    WINDOW_MARGIN_Y,
    WINDOW_WIDTH,
    Quote,
    WatchItem,
)
from .theme import build_stylesheet, ui_font
from .ui import IndexPanel, InputPanel, TitleBar, WatchListPanel

WINDOW_TITLE = "行情速览"

class MarketWidget(QWidget):
    """大盘 + 自选股悬浮窗。"""

    data_requested = Signal()
    item_activated = Signal(str)  # 双击自选股，抛出股票代码

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | (Qt.WindowStaysOnTopHint if ALWAYS_ON_TOP else Qt.Widget)
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(OPACITY)
        self.setFixedWidth(WINDOW_WIDTH)
        self.setStyleSheet(build_stylesheet())

        self._minimized = START_MINIMIZED
        self._card = QFrame(self)
        self._card.setObjectName("Card")
        self._build_ui()
        self.set_minimized(START_MINIMIZED)
        self._apply_geometry()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.data_requested.emit)

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(0)

        self._titlebar = TitleBar(WINDOW_TITLE, pinned=ALWAYS_ON_TOP, parent=self._card)
        self._titlebar.minimize_clicked.connect(self.toggle_minimized)
        self._titlebar.close_clicked.connect(self.close)
        self._titlebar.pin_toggled.connect(self.set_always_on_top)

        # 可折叠主体
        self._body = QWidget(self._card)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._index_panel = IndexPanel(INDEX_NAMES, self._body)
        body_layout.addWidget(self._index_panel)
        body_layout.addWidget(self._divider(self._body))
        body_layout.addWidget(self._section_title("自选股", self._body))
        self._watchlist = WatchListPanel(DEFAULT_WATCHLIST, self._body)
        self._watchlist.item_double_clicked.connect(self._on_item_activated)
        self._watchlist.layout_changed.connect(self._schedule_refit)
        body_layout.addWidget(self._watchlist, 1)
        body_layout.addWidget(self._divider(self._body))
        self._input_panel = InputPanel(parent=self._body)
        self._input_panel.quotes_submitted.connect(self.push_quotes)
        self._input_panel.item_added.connect(self.add_watch_item)
        self._input_panel.item_removed.connect(self.remove_watch_item)
        self._input_panel.refresh_requested.connect(self.data_requested.emit)
        self._input_panel.auto_refresh_toggled.connect(self.set_auto_refresh)
        self._input_panel.layout_changed.connect(self._schedule_refit)
        body_layout.addWidget(self._input_panel)

        self._card_layout.addWidget(self._titlebar)
        self._card_layout.addWidget(self._body, 1)

    def _divider(self, parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        # 整条边线由 QSS 的 background-color 绘制，交给布局横向铺满
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return line

    def _section_title(self, text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("SectionTitle")
        label.setFont(ui_font(9))
        label.setContentsMargins(14, 8, 14, 4)
        return label

    # ------------------------------------------------------------------
    # 数据入口（UI 只消费数据，不关心来源）
    # ------------------------------------------------------------------
    def set_index_quotes(self, quotes: list[Quote]) -> None:
        self._index_panel.update_quotes(quotes)

    def set_stock_quotes(self, quotes: list[Quote]) -> None:
        self._watchlist.update_quotes(quotes)

    def push_quotes(self, quotes: list[Quote]) -> None:
        """指数与自选股一起刷新：代码或名称命中的记录会自动归位。"""
        self._index_panel.update_quotes(quotes)
        self._watchlist.update_quotes(quotes)

    def set_watchlist(self, items: list[WatchItem]) -> None:
        self._watchlist.set_items(items)

    def watchlist_items(self) -> list[WatchItem]:
        return self._watchlist.items()

    def add_watch_item(self, item: WatchItem) -> None:
        items = self.watchlist_items()
        if any(existing.code == item.code for existing in items):
            return
        items.append(item)
        self.set_watchlist(items)
        self.data_requested.emit()

    def remove_watch_item(self, code: str) -> None:
        items = [item for item in self.watchlist_items() if item.code != code]
        self.set_watchlist(items)

    def set_live(self, live: bool, tip: str = "") -> None:
        self._titlebar.set_live(live, tip or ("实时数据已接入" if live else "数据源未接入"))

    def set_auto_refresh(self, enabled: bool, interval_seconds: int = 5) -> None:
        self._refresh_timer.stop()
        if enabled:
            self._refresh_timer.start(max(1, interval_seconds) * 1000)

    def request_data(self) -> None:
        self.data_requested.emit()

    # ------------------------------------------------------------------
    # 窗口行为
    # ------------------------------------------------------------------
    def set_always_on_top(self, pinned: bool) -> None:
        flags = self.windowFlags()
        flags = (flags | Qt.WindowStaysOnTopHint) if pinned else (flags & ~Qt.WindowStaysOnTopHint)
        self.setWindowFlags(flags)
        self.show()

    def toggle_minimized(self) -> None:
        self.set_minimized(not self._minimized)

    def set_minimized(self, minimized: bool) -> None:
        """收起时只保留标题栏，既不占地方又能随时展开。"""
        self._minimized = minimized
        self._body.setVisible(not minimized)
        self._titlebar.set_minimized(minimized)
        self._apply_geometry()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_geometry()

    def _schedule_refit(self) -> None:
        """把重新适配高度推迟到本轮布局完成后执行，否则拿到的是旧的高度提示。"""
        QTimer.singleShot(0, self._apply_geometry)

    def _apply_geometry(self) -> None:
        """限制高度不超出屏幕可用区域，并把窗口贴到右上角。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        limit = (
            MINIMIZED_HEIGHT
            if self._minimized
            else max(MINIMIZED_HEIGHT, area.height() - 2 * WINDOW_MARGIN_Y)
        )
        self.setMinimumHeight(MINIMIZED_HEIGHT)
        self.setMaximumHeight(limit)
        if not self._minimized:
            self._fit_height()
        x = area.right() - self.width() - WINDOW_MARGIN_X
        self.move(max(area.left(), x), area.top() + WINDOW_MARGIN_Y)

    def _fit_height(self) -> None:
        """按内容高度调整窗口。

        用卡片自身的高度提示（已含 1px 描边），不要用内部布局的提示，否则会少算
        描边的高度，列表刚好装满时就会挤出一条多余的滚动条。布局在同一个事件循环
        里要多跑几轮才能把高度算准，因此这里固定迭代几次让高度收敛。
        """
        for _ in range(3):
            self._card_layout.activate()
            self._body.layout().activate()
            self.resize(self.width(), self._card.sizeHint().height())

    def _on_item_activated(self, code: str) -> None:
        self.item_activated.emit(code)

if __name__ == "__main__":
    from .demo import main

    raise SystemExit(main())