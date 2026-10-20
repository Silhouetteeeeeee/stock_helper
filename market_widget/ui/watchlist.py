"""自选股列表：代码 / 名称 / 现价 / 涨跌幅，涨跌用红绿两色一眼区分。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import Quote, WatchItem
from ..theme import PALETTE, color_for_direction, format_change_pct, format_price, numeric_font, ui_font

COLUMN_HEADERS = ("名称", "现价", "涨跌幅")
ROW_HEIGHT = 26

class _RowWidget(QWidget):
    """自选股单行容器，仅用于接收双击事件。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

class _ListScrollArea(QScrollArea):
    """列表滚动区。

    QScrollArea 自带一个偏大的最小高度，自选股很少时会把面板撑得比内容还高，
    从而凭空冒出一条滚动条。这里把最小高度放开，高度完全由面板按行数决定。
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt 命名)
        return QSize(0, 0)

class WatchListPanel(QWidget):
    """自选股面板。对外只暴露 ``set_items`` 与 ``update_quotes`` 两个数据入口。"""

    item_double_clicked = Signal(str)  # 双击行时抛出股票代码，便于接入详情窗口
    layout_changed = Signal()          # 行数变化，提示窗口重新适配高度

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt 命名)
        """按当前行数给出高度，使窗口能随自选股数量自然伸缩。"""
        base = super().sizeHint()
        layout = self.layout()
        if layout is None:
            return base
        margins = layout.contentsMargins()
        height = (
            margins.top()
            + self._header.sizeHint().height()
            + layout.spacing()
            + ROW_HEIGHT * len(self._items)
            + margins.bottom()
        )
        return QSize(base.width(), height)

    def __init__(self, items: list[WatchItem] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._rows: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
        self._items: list[WatchItem] = []

        self._header = self._build_header()

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        # 行数少时列表收缩到内容高度，避免中间留出一大片空白
        self._body_layout.addStretch(0)

        self._scroll = _ListScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._body)
        self._scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 12)
        layout.setSpacing(2)
        layout.addWidget(self._header)
        layout.addWidget(self._scroll, 1)

        if items:
            self.set_items(items)

    # -- 数据入口 ---------------------------------------------------------
    def set_items(self, items: list[WatchItem]) -> None:
        """重建整个列表（增删自选股后调用）。"""
        self._items = list(items)
        self._clear_rows()
        for item in self._items:
            self._append_row(item)
        # 行数变化后必须让 Qt 丢弃缓存的高度提示，窗口才会重新适配高度
        self._body.updateGeometry()
        self._scroll.updateGeometry()
        self.updateGeometry()
        self.layout_changed.emit()

    def update_quotes(self, quotes: list[Quote]) -> None:
        """按代码刷新行情。

        采用增量语义：只更新本次携带的记录，未携带的代码保留原值，
        因此数据源既可以推送全量快照，也可以只推送变化的那几只。
        需要清空时调用 ``reset()``。
        """
        by_code = {quote.code: quote for quote in quotes}
        for code, labels in self._rows.items():
            quote = by_code.get(code)
            if quote is not None:
                self._apply_row(quote, *labels)

    def reset(self) -> None:
        """清空所有行情数值，回到占位状态。"""
        for labels in self._rows.values():
            self._clear_row(*labels)

    def items(self) -> list[WatchItem]:
        """返回当前自选股列表的副本。"""
        return list(self._items)

    # -- 内部实现 ---------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QWidget(self)
        grid = QGridLayout(header)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)
        for column, text in enumerate(COLUMN_HEADERS):
            label = QLabel(text)
            label.setFont(ui_font(9))
            label.setStyleSheet(f"color: {PALETTE.text_muted};")
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        return header

    def _clear_rows(self) -> None:
        for name_label, price_label, change_label in self._rows.values():
            for label in (name_label, price_label, change_label):
                label.setParent(None)
                label.deleteLater()
        self._rows.clear()
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _append_row(self, item: WatchItem) -> None:
        row = _RowWidget(self._body)
        row.setFixedHeight(ROW_HEIGHT)
        row.setCursor(Qt.PointingHandCursor)
        row.setToolTip(f"{item.name}（{item.code}）")
        row.double_clicked.connect(lambda code=item.code: self.item_double_clicked.emit(code))

        grid = QGridLayout(row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)

        name_label = QLabel(item.name)
        name_label.setFont(ui_font(11))
        name_label.setStyleSheet(f"color: {PALETTE.text};")

        price_label = QLabel("--")
        price_label.setFont(numeric_font(11, bold=False))
        price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        change_label = QLabel("--")
        change_label.setFont(numeric_font(11))
        change_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        grid.addWidget(name_label, 0, 0)
        grid.addWidget(price_label, 0, 1)
        grid.addWidget(change_label, 0, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)

        self._body_layout.insertWidget(self._body_layout.count() - 1, row)
        self._rows[item.code] = (name_label, price_label, change_label)

    def _apply_row(self, quote: Quote, name: QLabel, price: QLabel, change: QLabel) -> None:
        color = color_for_direction(quote.direction)
        price.setText(format_price(quote.price))
        change.setText(format_change_pct(quote.change_pct))
        price.setStyleSheet(f"color: {PALETTE.text};")
        change.setStyleSheet(f"color: {color};")

    def _clear_row(self, name: QLabel, price: QLabel, change: QLabel) -> None:
        price.setText("--")
        change.setText("--")
        price.setStyleSheet(f"color: {PALETTE.text_muted};")
        change.setStyleSheet(f"color: {PALETTE.text_muted};")