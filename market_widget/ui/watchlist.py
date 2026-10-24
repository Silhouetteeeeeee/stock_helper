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
from common.stock import *
from ..config import WatchItem
from ..theme import PALETTE, color_for_direction, format_change_pct, format_price, numeric_font, ui_font

COLUMN_HEADERS = ("名称", "现价", "涨跌幅")
ROW_HEIGHT = 26

class _RowWidget(QWidget):
    """自选股单行容器：接收单击选中与双击打开。"""

    clicked = Signal()
    double_clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

class _ListScrollArea(QScrollArea):
    """列表滚动区。

    QScrollArea 自带一个偏大的最小高度，自选股很少时会把面板撑得比内容还高，
    从而凭空冒出一条滚动条。这里把最小高度放开，高度完全由面板按行数决定。
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt 命名)
        return QSize(0, 0)

class WatchListPanel(QWidget):
    """自选股面板。数据入口为 ``set_items`` / ``update_quotes``，``set_selected`` 控制高亮。"""

    item_clicked = Signal(str)         # 单击行时抛出股票代码，用于选中并展示分时
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
        self._row_widgets: dict[str, _RowWidget] = {}
        self._items: list[WatchItem] = []
        self._quotes: dict[str, Quote] = {}   # 最近一次行情，重建行时用于恢复现价
        self._selected_code: str | None = None

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
        """重建整个列表（增删自选股后调用）。

        行控件是新建的，初始显示为占位符，这里把缓存的上一次行情重新
        填回去；否则增删任意一只股票都会把其它股票的现价和涨跌幅清空，
        直到下一次行情刷新才恢复。
        """
        self._items = list(items)
        self._clear_rows()
        for item in self._items:
            self._append_row(item)
        # 只保留仍在列表中的行情，避免删除后重新添加时显示过期价格
        self._quotes = {
            code: quote for code, quote in self._quotes.items() if code in self._rows
        }
        for code, labels in self._rows.items():
            quote = self._quotes.get(code)
            if quote is not None:
                self._apply_row(quote, *labels)
        # 重建行后恢复选中态（选中的股票可能已被删除，此时自然无高亮）
        self.set_selected(self._selected_code)
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
                self._quotes[code] = quote
                self._apply_row(quote, *labels)

    def reset(self) -> None:
        """清空所有行情数值，回到占位状态。"""
        self._quotes.clear()
        for labels in self._rows.values():
            self._clear_row(*labels)

    def set_selected(self, code: str | None) -> None:
        """高亮指定股票；传入 None 清除全部高亮。"""
        self._selected_code = code
        for row_code, row in self._row_widgets.items():
            selected = row_code == code
            row.setStyleSheet(
                f"background-color: {PALETTE.hover}; border-radius: 4px;"
                if selected
                else "background-color: transparent;"
            )
            name_label = self._rows[row_code][0]
            name_label.setStyleSheet(
                f"color: {PALETTE.accent if selected else PALETTE.text};"
            )

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
            if column > 0:
                # 数值列表头右对齐，和下面的价格/涨跌幅对齐
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        return header

    def _clear_rows(self) -> None:
        self._row_widgets.clear()
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
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setFixedHeight(ROW_HEIGHT)
        row.setCursor(Qt.PointingHandCursor)
        row.setToolTip(f"{item.name}（{item.code}）")
        row.clicked.connect(lambda code=item.code: self.item_clicked.emit(code))
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

        # 点击落到整行上，避免点到文字时收不到选中事件
        for label in (name_label, price_label, change_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        grid.addWidget(name_label, 0, 0)
        grid.addWidget(price_label, 0, 1)
        grid.addWidget(change_label, 0, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)

        self._body_layout.insertWidget(self._body_layout.count() - 1, row)
        self._rows[item.code] = (name_label, price_label, change_label)
        self._row_widgets[item.code] = row

    def _apply_row(self, quote: Quote, name: QLabel, price: QLabel, change: QLabel) -> None:
        color = color_for_direction(quote.direction)
        price.setText(format_price(quote.price))
        change.setText(format_change_pct(quote.change_pct))
        # 价格与涨跌幅统一按方向着色，和顶部指数面板保持一致
        price.setStyleSheet(f"color: {color};")
        change.setStyleSheet(f"color: {color};")

    def _clear_row(self, name: QLabel, price: QLabel, change: QLabel) -> None:
        price.setText("--")
        change.setText("--")
        price.setStyleSheet(f"color: {PALETTE.text_muted};")
        change.setStyleSheet(f"color: {PALETTE.text_muted};")