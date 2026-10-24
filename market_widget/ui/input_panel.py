"""数据输入面板：批量粘贴、自选股增删与刷新控制。

界面本身不抓取行情，只负责把用户/数据源提供的内容解析成 ``Quote`` 后抛给主窗口。
自选股增删由主窗口写入数据库，面板只负责收集代码和显示操作结果。
面板默认隐藏，由标题栏的齿轮按钮控制显隐。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common.stock import *
from ..config import WatchItem
from ..theme import PALETTE, ui_font

PLACEHOLDER = (
    "代码,名称,现价,涨跌幅\n"
    "sh000001,上证指数,3210.45,+0.86"
)

REFRESH_INTERVALS = (3, 5, 10, 30, 60)


class InputPanel(QWidget):
    """手动喂数据的区域，默认隐藏，由标题栏齿轮展开。"""

    quotes_submitted = Signal(list)      # list[Quote]
    item_added = Signal(object)          # WatchItem（名称可为空，由主窗口从 stock 表补全）
    item_removed = Signal(str)           # 代码
    refresh_requested = Signal()
    auto_refresh_toggled = Signal(bool, int)

    def __init__(self, refresh_interval: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        self._build_content(layout, refresh_interval)

    # -- 对外接口 ---------------------------------------------------------
    def start_auto_refresh(self) -> None:
        """按当前勾选状态启动刷新，供主窗口初始化时调用。"""
        self._on_auto_toggled(self._auto.isChecked())

    def show_feedback(self, message: str, error: bool = False) -> None:
        """显示自选股操作结果，供主窗口写入数据库后回显。"""
        self._feedback.setText(message)
        color = PALETTE.danger if error else PALETTE.text_muted
        self._feedback.setStyleSheet(f"color: {color};")

    def clear_code_input(self) -> None:
        """清空代码框（同时清掉上一条操作提示）。"""
        self._code_input.clear()

    # -- 内部实现 ---------------------------------------------------------
    def _build_content(self, layout: QVBoxLayout, refresh_interval: int) -> None:
        # 1) 批量粘贴行情
        layout.addWidget(self._hint("批量填入行情"))

        self._batch = QPlainTextEdit()
        self._batch.setPlaceholderText(PLACEHOLDER)
        self._batch.setFixedHeight(66)
        self._batch.setFont(ui_font(10))
        layout.addWidget(self._batch)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)

        apply_button = QPushButton("解析并刷新")
        apply_button.setObjectName("PrimaryButton")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(self._on_apply_batch)

        clear_button = QPushButton("清空")
        clear_button.setObjectName("GhostButton")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.clicked.connect(self._batch.clear)

        batch_row.addWidget(apply_button)
        batch_row.addWidget(clear_button)
        batch_row.addStretch(1)
        layout.addLayout(batch_row)

        # 2) 增删自选股（写入 stock 表的 watchlist_order 字段）
        layout.addWidget(self._hint("自选股管理"))

        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("代码，如 600036 / sh600036")
        self._code_input.textChanged.connect(self._clear_feedback)
        layout.addWidget(self._code_input)

        add_button = QPushButton("添加")
        add_button.setObjectName("GhostButton")
        add_button.setCursor(Qt.PointingHandCursor)
        add_button.setToolTip("添加代码框中填写的自选股，名称自动从 stock 表读取")
        add_button.clicked.connect(self._on_add_item)

        remove_button = QPushButton("移除")
        remove_button.setObjectName("GhostButton")
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.setToolTip("移除代码框中填写的自选股")
        remove_button.clicked.connect(self._on_remove_item)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addWidget(add_button)
        action_row.addWidget(remove_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        # 固定高度，显示操作结果时不会让面板跳动
        self._feedback = QLabel("")
        self._feedback.setFont(ui_font(9))
        self._feedback.setFixedHeight(14)
        layout.addWidget(self._feedback)

        # 3) 刷新控制
        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(6)

        self._auto = QCheckBox("自动刷新")
        self._auto.setFont(ui_font(10))
        self._auto.setChecked(True)
        self._auto.toggled.connect(self._on_auto_toggled)

        self._interval = QComboBox()
        self._interval.setFixedWidth(78)
        for seconds in sorted(set(REFRESH_INTERVALS) | {max(1, int(refresh_interval))}):
            self._interval.addItem(f"{seconds} 秒", seconds)
        self._interval.setCurrentIndex(self._interval.findData(refresh_interval))
        self._interval.currentIndexChanged.connect(self._on_interval_changed)

        manual_button = QPushButton("立即刷新")
        manual_button.setObjectName("GhostButton")
        manual_button.setCursor(Qt.PointingHandCursor)
        manual_button.clicked.connect(self.refresh_requested.emit)

        refresh_row.addWidget(self._auto)
        refresh_row.addWidget(self._interval)
        refresh_row.addWidget(manual_button)
        refresh_row.addStretch(1)
        layout.addLayout(refresh_row)

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(10))
        label.setStyleSheet(f"color: {PALETTE.text_muted};")
        return label

    def _interval_seconds(self) -> int:
        return int(self._interval.currentData())

    def _on_apply_batch(self) -> None:
        self.quotes_submitted.emit(parse_batch(self._batch.toPlainText()))

    def _clear_feedback(self) -> None:
        self._feedback.clear()

    def _on_add_item(self) -> None:
        code = self._code_input.text().strip()
        if not code:
            self.show_feedback("请输入股票代码", error=True)
            return
        # 名称由主窗口从 stock 表补全；输入框在添加成功后才清空
        self.item_added.emit(WatchItem(code=code, name=""))

    def _on_remove_item(self) -> None:
        code = self._code_input.text().strip()
        if not code:
            self.show_feedback("请输入股票代码", error=True)
            return
        self.item_removed.emit(code)

    def _on_auto_toggled(self, enabled: bool) -> None:
        self.auto_refresh_toggled.emit(enabled, self._interval_seconds())

    def _on_interval_changed(self) -> None:
        # 间隔变化时如果自动刷新开着，立刻按新间隔重启定时器。
        if self._auto.isChecked():
            self.auto_refresh_toggled.emit(True, self._interval_seconds())


def parse_batch(text: str) -> list[Quote]:
    """把「代码,名称,现价,涨跌幅」格式的文本解析为行情列表。

    无法解析的行会被静默跳过，避免影响其余数据的展示。
    """
    quotes: list[Quote] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.replace("\t", ",").split(",")]
        if len(parts) < 3:
            continue
        code, name = parts[0], parts[1]
        price = _to_float(parts[2])
        change = _to_float(parts[3].replace("%", "")) if len(parts) > 3 else None

        quote = Quote(code=code, name=name, price=price)
        quote.change_pct = change
        quotes.append(quote)
    return quotes


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
