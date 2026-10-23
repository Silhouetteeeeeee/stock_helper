"""主窗口：右上角常驻悬浮窗，负责组装各面板并提供数据入口。

三种显示模式：
- 锁定迷你态：只保留标题栏（点击 "—" 锁定）
- 默认迷你态：标题栏 + 迷你指数单行（鼠标不在窗口上时）
- 完整展开：标题栏 + 完整主体（鼠标悬停时自动展开）

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
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from common.stock import *

from .config import (
    ALWAYS_ON_TOP,
    DEFAULT_WATCHLIST,
    HOVER_COLLAPSE_DELAY_MS,
    HOVER_EXPAND_DELAY_MS,
    INDEX_NAMES,
    MINIMIZED_HEIGHT,
    MINI_MODE_ROW_HEIGHT,
    OPACITY,
    START_MINIMIZED,
    WINDOW_MARGIN_X,
    WINDOW_MARGIN_Y,
    WINDOW_WIDTH,
    WatchItem,
)
from .theme import (
    PALETTE,
    build_stylesheet,
    color_for_direction,
    format_change_pct,
    format_price,
    numeric_font,
    ui_font,
)
from .ui import IndexPanel, InputPanel, TitleBar, WatchListPanel

WINDOW_TITLE = "行情速览"

# 迷你态默认展示的指数（取 INDEX_NAMES 第一个）
MINI_INDEX_NAME = INDEX_NAMES[0] if INDEX_NAMES else "上证指数"


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

        self._locked_minimized = START_MINIMIZED   # 锁定迷你态：只保留标题栏
        self._hover_expanded = False                # 当前是否因悬停而完整展开
        self._card = QFrame(self)                  # 卡片容器（承载标题栏 + 主体）
        self._card.setObjectName("Card")
        self._build_ui()

        # 悬停防抖定时器
        self._hover_collapse_timer = QTimer(self)     # 鼠标移出后延迟多少毫秒再收起（防抖）
        self._hover_collapse_timer.setSingleShot(True)
        self._hover_collapse_timer.setInterval(HOVER_COLLAPSE_DELAY_MS)
        self._hover_collapse_timer.timeout.connect(self._collapse_on_hover_timeout)

        self._hover_expand_timer = QTimer(self)        # 鼠标移入后延迟多少毫秒再展开（0 = 立即）
        self._hover_expand_timer.setSingleShot(True)
        self._hover_expand_timer.setInterval(HOVER_EXPAND_DELAY_MS)
        self._hover_expand_timer.timeout.connect(self._expand_on_hover_timeout)

        self._apply_mode_visibilities()
        self._apply_geometry()

        self._refresh_timer = QTimer(self)             # 自动刷新定时器（由数据输入面板控制启停）
        self._refresh_timer.timeout.connect(self.data_requested.emit)
        self._input_panel._on_auto_toggled(True)

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        self._card_layout = QVBoxLayout(self._card)        # 卡片内部布局，包含 titlebar / mini_row / body
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(0)

        self._titlebar = TitleBar(WINDOW_TITLE, parent=self._card)  # 顶部栏（拖动 / 收起 / 关闭）
        self._titlebar.minimize_clicked.connect(self.toggle_minimized)
        self._titlebar.close_clicked.connect(self.close)

        # 迷你态单行（标题栏下方，只显示一只指数）
        self._mini_row = self._build_mini_row(self._card)  # 迷你态单行容器

        # 可折叠主体
        self._body = QWidget(self._card)                  # 完整主体（指数面板 + 自选股 + 数据输入）
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self._index_panel = IndexPanel(INDEX_NAMES, self._body)  # 大盘指数三列面板
        body_layout.addWidget(self._index_panel)
        body_layout.addWidget(self._divider(self._body))
        body_layout.addWidget(self._section_title("自选股", self._body))
        self._watchlist = WatchListPanel(DEFAULT_WATCHLIST, self._body)  # 自选股列表
        self._watchlist.item_double_clicked.connect(self._on_item_activated)
        self._watchlist.layout_changed.connect(self._schedule_refit)
        body_layout.addWidget(self._watchlist, 1)
        body_layout.addWidget(self._divider(self._body))
        self._input_panel = InputPanel(parent=self._body)  # 数据输入面板（批量粘贴 / 增删自选 / 自动刷新）
        self._input_panel.quotes_submitted.connect(self.push_quotes)
        self._input_panel.item_added.connect(self.add_watch_item)
        self._input_panel.item_removed.connect(self.remove_watch_item)
        self._input_panel.refresh_requested.connect(self.data_requested.emit)
        self._input_panel.auto_refresh_toggled.connect(self.set_auto_refresh)
        self._input_panel.layout_changed.connect(self._schedule_refit)

        body_layout.addWidget(self._input_panel)

        self._card_layout.addWidget(self._titlebar)
        self._card_layout.addWidget(self._mini_row)
        self._card_layout.addWidget(self._body, 1)

    def _build_mini_row(self, parent: QWidget) -> QWidget:
        """迷你态单行：指数名称 / 点位 / 涨跌幅。"""
        row = QWidget(parent)
        row.setFixedHeight(MINI_MODE_ROW_HEIGHT)
        row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._mini_name = QLabel(MINI_INDEX_NAME)
        self._mini_name.setFont(ui_font(10))
        self._mini_name.setStyleSheet(f"color: {PALETTE.text_muted};")

        self._mini_price = QLabel("--")
        self._mini_price.setFont(numeric_font(15))
        self._mini_price.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self._mini_change = QLabel("--")
        self._mini_change.setFont(numeric_font(10))
        self._mini_change.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(8)
        layout.addWidget(self._mini_name)
        layout.addStretch(1)
        layout.addWidget(self._mini_price, 0, Qt.AlignRight)
        layout.addWidget(self._mini_change, 0, Qt.AlignRight)

        return row

    def _divider(self, parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return line

    def _section_title(self, text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("SectionTitle")
        label.setFont(ui_font(9))
        label.setContentsMargins(14, 8, 14, 4)
        return label

    # ------------------------------------------------------------------
    # 三种模式的可见性切换
    # ------------------------------------------------------------------
    def _apply_mode_visibilities(self) -> None:
        """根据当前模式设置 titlebar / mini_row / body 的显隐。"""
        if self._locked_minimized:
            # 锁定迷你：只留标题栏
            self._mini_row.hide()
            self._body.hide()
        elif self._hover_expanded:
            # 完整展开：隐藏 mini_row，显示完整 body
            self._mini_row.hide()
            self._body.show()
        else:
            # 默认迷你：mini_row 显示，完整 body 隐藏
            self._mini_row.show()
            self._body.hide()

        self._titlebar.set_minimized(self._locked_minimized)
        self._apply_geometry()

    # ------------------------------------------------------------------
    # 数据入口（UI 只消费数据，不关心来源）
    # ------------------------------------------------------------------
    def set_index_quotes(self, quotes: list[Quote]) -> None:
        self._index_panel.update_quotes(quotes)
        self._update_mini_index(quotes)

    def set_stock_quotes(self, quotes: list[Quote]) -> None:
        self._watchlist.update_quotes(quotes)

    def push_quotes(self, quotes: list[Quote]) -> None:
        """指数与自选股一起刷新：代码或名称命中的记录会自动归位。"""
        self._index_panel.update_quotes(quotes)
        self._watchlist.update_quotes(quotes)
        self._update_mini_index(quotes)

    def _update_mini_index(self, quotes: list[Quote]) -> None:
        """从 quotes 中按名称取出迷你指数并刷新单行显示。"""
        target = next((q for q in quotes if q.name == MINI_INDEX_NAME), None)
        if target is None:
            return
        color = color_for_direction(target.direction)
        self._mini_price.setText(format_price(target.price))
        self._mini_change.setText(format_change_pct(target.change_pct))
        self._mini_price.setStyleSheet(f"color: {color};")
        self._mini_change.setStyleSheet(f"color: {color};")

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
    # 悬停展开 / 离开收起
    # ------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        if self._locked_minimized:
            return
        self._hover_collapse_timer.stop()
        if HOVER_EXPAND_DELAY_MS == 0:
            self._hover_expanded = True
            self._apply_mode_visibilities()
        else:
            self._hover_expand_timer.start()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        if self._locked_minimized:
            return
        self._hover_expand_timer.stop()
        self._hover_collapse_timer.start()

    def _expand_on_hover_timeout(self) -> None:
        if not self.underMouse():
            return
        self._hover_expanded = True
        self._apply_mode_visibilities()

    def _collapse_on_hover_timeout(self) -> None:
        if self.underMouse():
            return
        self._hover_expanded = False
        print("鼠标是否仍在窗口内：", self.underMouse())
        print("完整展开状态：", self._hover_expanded)
        print("收起前窗口高度：", self.height())
        self._apply_mode_visibilities()

    # ------------------------------------------------------------------
    # 窗口行为
    # ------------------------------------------------------------------
    def toggle_minimized(self) -> None:
        """切换锁定迷你态：点击 "—" 锁定，再次点击解锁回到迷你行/悬停模式。"""
        self._locked_minimized = not self._locked_minimized
        if self._locked_minimized:
            self._hover_collapse_timer.stop()
            self._hover_expand_timer.stop()
            self._hover_expanded = False
        self._apply_mode_visibilities()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_geometry()

    def _schedule_refit(self):
        print("[调用 _schedule_refit 前]")
        # 只有完整展开时，才根据内容重新计算窗口高度。
        # 收起状态下，不允许输入面板的布局变化触发重新撑高窗口。
        if self._locked_minimized or not self._hover_expanded:
            return

        QTimer.singleShot(0, self._apply_geometry)

    def _apply_geometry(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return

        area = screen.availableGeometry()

        if self._locked_minimized:
            # 锁定迷你模式：只显示标题栏
            target_height = MINIMIZED_HEIGHT
            self.setFixedHeight(target_height)

        elif not self._hover_expanded:
            # 普通迷你模式：标题栏 + 指数迷你行
            target_height = MINIMIZED_HEIGHT + MINI_MODE_ROW_HEIGHT
            self.setFixedHeight(target_height)

        else:
            # 完整展开模式：解除固定高度限制
            self.setMinimumHeight(MINIMIZED_HEIGHT)
            self.setMaximumHeight(
                max(MINIMIZED_HEIGHT, area.height() - 2 * WINDOW_MARGIN_Y)
            )

            self._card_layout.invalidate()
            self._card_layout.activate()

            if self._body.layout() is not None:
                self._body.layout().invalidate()
                self._body.layout().activate()

            self._card.adjustSize()
            self._fit_height()

        x = area.right() - self.width() - WINDOW_MARGIN_X
        y = area.top() + WINDOW_MARGIN_Y
        self.move(max(area.left(), x), y)


    def _fit_height(self) -> None:
        print("\n[调用 _fit_height]")
        """按内容高度调整窗口。"""
        for _ in range(3):
            self._card_layout.activate()
            self._body.layout().activate()
            self.resize(self.width(), self._card.sizeHint().height())

    def _on_item_activated(self, code: str) -> None:
        self.item_activated.emit(code)

    def resizeEvent(self, event):
        old_size = event.oldSize()
        new_size = event.size()

        print(
            f"[resizeEvent] "
            f"{old_size.width()}x{old_size.height()} -> "
            f"{new_size.width()}x{new_size.height()}, "
            f"locked={self._locked_minimized}, "
            f"expanded={self._hover_expanded}, "
            f"minH={self.minimumHeight()}, "
            f"maxH={self.maximumHeight()}"
        )

        super().resizeEvent(event)


if __name__ == "__main__":
    from .demo import main

    raise SystemExit(main())
