"""大盘指数面板：三列等宽展示指数点位与涨跌幅，视觉上最突出。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget, QHBoxLayout, QScrollArea, QSizePolicy

from common.stock import *
from ..theme import PALETTE, color_for_direction, format_change_pct, format_price, numeric_font, ui_font


class _IndexCell(QWidget):
    """单个指数单元：名称 / 点位 / 涨跌幅，纵向居中排列。"""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._name = QLabel(name)
        self._name.setFont(ui_font(10))
        self._name.setStyleSheet(f"color: {PALETTE.text_muted};")
        self._name.setAlignment(Qt.AlignCenter)

        self._value = QLabel("--")
        self._value.setFont(numeric_font(16))
        self._value.setAlignment(Qt.AlignCenter)

        self._change = QLabel("--")
        self._change.setFont(numeric_font(10))
        self._change.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self._name)
        layout.addWidget(self._value)
        layout.addWidget(self._change)

    def update_quote(self, quote: Quote) -> None:
        color = color_for_direction(quote.direction)
        self._value.setText(format_price(quote.price))
        self._change.setText(format_change_pct(quote.change_pct))
        self._value.setStyleSheet(f"color: {color};")
        self._change.setStyleSheet(f"color: {color};")


class _HorizontalWheelFilter(QObject):
    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self.scroll_area = scroll_area

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            bar = self.scroll_area.horizontalScrollBar()

            delta = event.angleDelta().y()
            if delta:
                bar.setValue(bar.value() - delta)
                return True

        return False

class _DragScrollFilter(QObject):
    def __init__(self, scroll_area):
        super().__init__(scroll_area)
        self.scroll_area = scroll_area
        self._dragging = False
        self._last_x = 0

    def eventFilter(self, obj, event):
        bar = self.scroll_area.horizontalScrollBar()

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._last_x = event.position().toPoint().x()
                return True

        elif event.type() == QEvent.Type.MouseMove:
            if self._dragging:
                x = event.position().toPoint().x()
                bar.setValue(bar.value() + self._last_x - x)
                self._last_x = x
                return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = False
                return True

        return False

class IndexPanel(QWidget):
    """大盘指数总览：横向滚动，单元格不被压缩。"""

    def __init__(
        self,
        index_names: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # 外层布局
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(14, 12, 14, 10)
        outer_layout.setSpacing(0)

        # 横向滚动区域
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # 装载滑动监听器
        self._wheel_filter = _HorizontalWheelFilter(self._scroll)
        self._scroll.viewport().installEventFilter(
            self._wheel_filter
        )

        self._drag_filter = _DragScrollFilter(self._scroll)
        self._scroll.viewport().installEventFilter(
            self._drag_filter
        )

        # 只允许水平滚动，禁止垂直滚动
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        # 高度由内容决定，不设置固定高度
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        # 滚动区域内部的内容容器
        self._content = QWidget()

        self._row = QHBoxLayout(self._content)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)

        self._scroll.setWidget(self._content)
        outer_layout.addWidget(self._scroll)

        self._cells: dict[str, _IndexCell] = {}
        self.set_index_names(index_names)

    def set_index_names(
        self,
        index_names: tuple[str, ...],
    ) -> None:
        # 删除旧单元格
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        self._cells.clear()

        # 重建单元格
        for name in index_names:
            cell = _IndexCell(name, self._content)

            # 每个单元格至少有这么宽，不会被布局压缩
            cell.setMinimumWidth(125)

            # 使用 Preferred 而非 Expanding，避免强制拉伸
            cell.setSizePolicy(
                cell.sizePolicy().Policy.Preferred,
                cell.sizePolicy().Policy.Preferred,
            )

            self._cells[name] = cell
            self._row.addWidget(cell, 0)

        # 将剩余空间留在尾部，而不是拉伸单元格
        self._row.addStretch(1)

        self._content.adjustSize()

    def update_quotes(self, quotes: list[Quote]) -> None:
        """按名称匹配刷新，未携带的指数保留原值。"""
        by_name = {quote.name: quote for quote in quotes}

        for name, cell in self._cells.items():
            quote = by_name.get(name)

            if quote is not None:
                cell.update_quote(quote)

    def reset(self) -> None:
        for cell in self._cells.values():
            cell.update_quote(Quote(code="", name=""))
