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
    ``window.add_watch_item(item)``       加入自选股（同时写入数据库）
    ``window.remove_watch_item(code)``    移除自选股（同时写入数据库）
    ``window.set_timeshare(points, pre_close)``  刷新分时图
    ``window.set_fundamentals(code, briefing)``  填入基本面速览（过期的会被丢弃）
    ``window.data_requested``             信号：界面需要一份新数据时发出
    ``window.symbol_selected``            信号：选中自选股/指数时发出（代码、名称、是否指数）
    ``window.fundamentals_requested``     信号：需要某只股票的基本面速览时发出
    ``window.selected_symbol()``          返回当前选中的（代码、名称、是否指数）
    ``window.financials_updated``         信号：某只股票的财报已同步完成
    ``window.finance_status``             信号：财报调度的人话状态（失败/同步结果）
    ``window.finance_scheduler``          后台财报调度器（未启用时为 None）

财报调度器随窗口显示启动、随窗口关闭停止：它只查本地数据库，
网络请求都在后台线程里跑，不会卡界面。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from common.stock import *

from stock.finance_scheduler import FinanceScheduler

from .config import (
    ALWAYS_ON_TOP,
    FINANCE_MONITOR,
    HOVER_COLLAPSE_DELAY_MS,
    HOVER_EXPAND_DELAY_MS,
    INDEX_ITEMS,
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
from .watchlist_store import (
    WatchlistError,
    add_to_watchlist,
    load_watchlist,
    remove_from_watchlist,
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
from .assistant_web import AssistantWeb
from .ui import (
    FundamentalsPanel,
    IndexPanel,
    InputPanel,
    MinutePoint,
    TimeShareChart,
    TitleBar,
    WatchListPanel,
)

WINDOW_TITLE = "行情速览"

# 迷你态默认展示的指数（取 INDEX_NAMES 第一个）
MINI_INDEX_NAME = INDEX_NAMES[0] if INDEX_NAMES else "上证指数"


class MarketWidget(QWidget):
    """大盘 + 自选股悬浮窗。"""

    data_requested = Signal()
    item_activated = Signal(str)  # 双击自选股，抛出股票代码
    symbol_selected = Signal(str, str, bool)  # 选中标的：代码 / 名称 / 是否指数
    fundamentals_requested = Signal(str)  # 需要某只股票的基本面速览（选股/财报更新时）
    financials_updated = Signal(str)  # 某只股票的财报已同步完成
    finance_status = Signal(str)      # 财报调度的人话状态

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
        self._selected_code = ""                    # 当前选中标的（自选股或指数）
        self._selected_name = ""
        self._selected_is_index = False
        self._card = QFrame(self)                  # 卡片容器（承载标题栏 + 主体）
        self._card.setObjectName("Card")
        self._build_ui()
        self._select_default_symbol()

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
        self._input_panel.start_auto_refresh()

        self._finance_started = False                  # 调度器是否已随窗口启动
        self._setup_finance_monitor()

    # ------------------------------------------------------------------
    # 财报调度（披露日历 → 待披露队列 → 自动同步三表）
    # ------------------------------------------------------------------
    def _setup_finance_monitor(self) -> None:
        self._finance_monitor: FinanceScheduler | None = None
        if not FINANCE_MONITOR:
            return

        self._finance_monitor = FinanceScheduler(self)
        self._finance_monitor.financials_updated.connect(self._on_financials_updated)
        self._finance_monitor.sync_finished.connect(self._on_finance_sync_finished)
        self._finance_monitor.error.connect(
            lambda message: self.finance_status.emit(f"财报同步失败：{message}")
        )

    def _on_financials_updated(self, code: str) -> None:
        """财报同步完成后：转发给外部，并刷新当前选中股票的基本面。"""
        self.financials_updated.emit(code)
        if code == self._selected_code and not self._selected_is_index:
            self.fundamentals_requested.emit(code)

    def _on_finance_sync_finished(self, result: dict) -> None:
        codes = result.get("codes") or []
        if codes:
            self.finance_status.emit(f"已同步 {len(codes)} 只股票的财报")

    @property
    def finance_scheduler(self) -> FinanceScheduler | None:
        """后台财报调度器；``FINANCE_MONITOR`` 关闭时为 None。"""
        return self._finance_monitor

    def start_finance_monitor(self) -> None:
        """启动财报调度（窗口显示时自动调用）。"""
        if self._finance_monitor is None or self._finance_started:
            return
        self._finance_started = True
        self._finance_monitor.start()

    def stop_finance_monitor(self) -> None:
        """停止财报调度（窗口关闭时自动调用）。"""
        if self._finance_monitor is None or not self._finance_started:
            return
        self._finance_started = False
        self._finance_monitor.stop()

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

        self._titlebar = TitleBar(WINDOW_TITLE, parent=self._card)  # 顶部栏（拖动 / 数据输入 / 收起 / 关闭）
        self._titlebar.minimize_clicked.connect(self.toggle_minimized)
        self._titlebar.close_clicked.connect(self.close)
        self._titlebar.input_clicked.connect(self.toggle_input_panel)
        self._titlebar.assistant_clicked.connect(self.open_assistant_web)

        # 迷你态单行（标题栏下方，只显示一只指数）
        self._mini_row = self._build_mini_row(self._card)  # 迷你态单行容器

        # 可折叠主体
        self._body = QWidget(self._card)                  # 完整主体（指数面板 + 自选股 + 数据输入）
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self._index_panel = IndexPanel(INDEX_ITEMS, self._body)  # 大盘指数三列面板
        self._index_panel.index_clicked.connect(self._on_index_clicked)
        body_layout.addWidget(self._index_panel)
        body_layout.addWidget(self._divider(self._body))
        self._timeshare_title = self._section_title("分时", self._body)
        body_layout.addWidget(self._timeshare_title)
        self._timeshare = TimeShareChart(self._body)  # 分时图（数据由外部注入）
        body_layout.addWidget(self._timeshare)
        body_layout.addWidget(self._divider(self._body))
        self._fundamentals_title = self._section_title("基本面", self._body)
        body_layout.addWidget(self._fundamentals_title)
        self._fundamentals = FundamentalsPanel(self._body)  # 财报 + 估值速览（数据由外部注入）
        body_layout.addWidget(self._fundamentals)
        body_layout.addWidget(self._divider(self._body))

        # 投资助手跑在本地 Web 页面里，标题栏 AI 按钮负责启动/打开
        self._assistant_web = AssistantWeb(self)
        self._assistant_web.opened.connect(self._on_assistant_opened)
        self._assistant_web.failed.connect(self._on_assistant_failed)

        body_layout.addWidget(self._divider(self._body))
        body_layout.addWidget(self._section_title("自选股", self._body))
        self._watchlist = WatchListPanel(load_watchlist(), self._body)  # 自选股列表（来自数据库）
        self._watchlist.item_clicked.connect(self._on_watch_clicked)
        self._watchlist.item_double_clicked.connect(self._on_item_activated)
        self._watchlist.layout_changed.connect(self._schedule_refit)
        body_layout.addWidget(self._watchlist, 1)

        # 数据输入区：默认隐藏，由标题栏齿轮开关；分隔线放在容器内，隐藏时不会留下孤线
        self._input_section = QWidget(self._body)
        input_layout = QVBoxLayout(self._input_section)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(0)
        input_layout.addWidget(self._divider(self._input_section))

        self._input_panel = InputPanel(parent=self._input_section)  # 数据输入面板（批量粘贴 / 增删自选 / 自动刷新）
        self._input_panel.quotes_submitted.connect(self.push_quotes)
        self._input_panel.item_added.connect(self.add_watch_item)
        self._input_panel.item_removed.connect(self.remove_watch_item)
        self._input_panel.refresh_requested.connect(self.data_requested.emit)
        self._input_panel.auto_refresh_toggled.connect(self.set_auto_refresh)
        input_layout.addWidget(self._input_panel)

        self._input_section.hide()
        body_layout.addWidget(self._input_section)

        self._card_layout.addWidget(self._titlebar)
        self._card_layout.addWidget(self._mini_row)
        self._card_layout.addWidget(self._body, 1)

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - WINDOW_MARGIN_X
        y = area.top() + WINDOW_MARGIN_Y
        self.move(max(area.left(), x), y)

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

    def set_timeshare(self, points: list[MinutePoint], pre_close: float) -> None:
        """刷新分时图（必须在主线程调用）。"""
        self._timeshare.set_data(points, pre_close)

    def set_fundamentals(self, code: str, briefing: dict) -> None:
        """填入基本面速览；标的已经切走时直接丢弃这份过期结果。"""
        if self._selected_is_index or code != self._selected_code:
            return
        self._fundamentals.show_briefing(briefing)
        self._schedule_refit()

    def selected_symbol(self) -> tuple[str, str, bool]:
        """返回当前选中的（代码, 名称, 是否指数）。"""
        return self._selected_code, self._selected_name, self._selected_is_index

    def _select_default_symbol(self) -> None:
        """优先选中第一只自选股，没有自选股时选中第一个指数。"""
        items = self._watchlist.items()
        if items:
            self._select_symbol(items[0].code, items[0].name, False)
        elif INDEX_ITEMS:
            code, name = INDEX_ITEMS[0]
            self._select_symbol(code, name, True)

    def _select_symbol(self, code: str, name: str, is_index: bool) -> None:
        """切换图表标的：更新高亮与标题，并通知数据层拉取对应分时。"""
        if (
            code == self._selected_code
            and name == self._selected_name
            and is_index == self._selected_is_index
        ):
            return

        self._selected_code = code
        self._selected_name = name
        self._selected_is_index = is_index
        self._watchlist.set_selected(None if is_index else code)
        self._index_panel.set_selected(code if is_index else None)
        self._timeshare_title.setText(f"分时 · {name}" if name else "分时")
        # 先清空旧标的的曲线，等新数据到达后再绘制，避免标题与图形不一致
        self._timeshare.set_data([], 0.0)

        # 基本面同理：先说明在加载，切换太快时过期结果会被 set_fundamentals 丢掉
        self._fundamentals_title.setText(
            f"基本面 · {name}" if name and not is_index else "基本面"
        )
        if is_index:
            self._fundamentals.show_message("指数没有财报数据")
        else:
            self._fundamentals.show_loading()
            self.fundamentals_requested.emit(code)

        # 助手页面通过状态文件跟随选中标的
        self._assistant_web.set_company(code, name, is_index)

        self.symbol_selected.emit(code, name, is_index)
        self._schedule_refit()

    def _on_watch_clicked(self, code: str) -> None:
        item = next((item for item in self._watchlist.items() if item.code == code), None)
        if item is not None:
            self._select_symbol(item.code, item.name, False)

    def _on_index_clicked(self, code: str, name: str) -> None:
        self._select_symbol(code, name, True)

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
        # 选中的股票被删除时，自动切换到第一只自选股或第一个指数
        if not self._selected_is_index and not any(
            item.code == self._selected_code for item in items
        ):
            self._select_default_symbol()

    def watchlist_items(self) -> list[WatchItem]:
        return self._watchlist.items()

    def add_watch_item(self, item: WatchItem) -> None:
        """加入自选股并写入数据库；名称统一以 stock 表为准。"""
        try:
            added = add_to_watchlist(item.code)
        except WatchlistError as exc:
            self._input_panel.show_feedback(str(exc), error=True)
            return

        items = [
            existing
            for existing in self.watchlist_items()
            if existing.code != added.code
        ]
        items.append(added)
        self.set_watchlist(items)
        self._input_panel.clear_code_input()
        self._input_panel.show_feedback(f"已添加 {added.name}")
        self.data_requested.emit()

    def remove_watch_item(self, code: str) -> None:
        """从自选股和数据库中移除，代码支持带交易所前缀。"""
        try:
            removed = remove_from_watchlist(code)
        except WatchlistError as exc:
            self._input_panel.show_feedback(str(exc), error=True)
            return
        if removed is None:
            self._input_panel.show_feedback("该股票不在自选股中", error=True)
            return

        items = [item for item in self.watchlist_items() if item.code != removed.code]
        self.set_watchlist(items)
        self._input_panel.clear_code_input()
        self._input_panel.show_feedback(f"已移除 {removed.name}")

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
    # 数据输入面板显隐（标题栏齿轮）
    # ------------------------------------------------------------------
    def toggle_input_panel(self) -> None:
        """切换数据输入面板；迷你态下点击会先展开窗口。"""
        if self._locked_minimized:
            self._locked_minimized = False
        if not self._hover_expanded:
            self._hover_expanded = True
            self._apply_mode_visibilities()

        show = self._input_section.isHidden()
        self._input_section.setVisible(show)
        self._titlebar.set_input_active(show)
        self._schedule_refit()

    def open_assistant_web(self) -> None:
        """打开投资助手页面；首次点击会先启动本地 Streamlit 服务。"""
        self._assistant_web.open()
        self._titlebar.set_assistant_busy(self._assistant_web.is_busy())

    def _on_assistant_opened(self, url: str) -> None:
        self._titlebar.set_assistant_busy(False)

    def _on_assistant_failed(self, message: str) -> None:
        self._titlebar.set_assistant_busy(False)
        QMessageBox.warning(self, "投资助手", message)

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
        self.start_finance_monitor()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_finance_monitor()
        self._assistant_web.stop()
        super().closeEvent(event)

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

        if self._locked_minimized or not self._hover_expanded:
            # 隐藏 body 会留下一个待处理的布局请求。body 里有自动换行的
            # 标签（基本面速览），布局的高度随宽度变化，这个请求如果排在
            # setFixedHeight 之后执行，会按展开时的高度把窗口重新撑开，
            # 迷你态就失效了。所以先在这里把布局消化掉，再固定高度。
            self._card_layout.activate()
            target_height = (
                MINIMIZED_HEIGHT
                if self._locked_minimized
                else MINIMIZED_HEIGHT + MINI_MODE_ROW_HEIGHT
            )
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
