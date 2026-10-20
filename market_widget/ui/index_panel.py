"""大盘指数面板：三列等宽展示指数点位与涨跌幅，视觉上最突出。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ..config import Quote
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

class IndexPanel(QWidget):
    """大盘指数总览。指数数量变化时会自动重建单元格。"""

    def __init__(self, index_names: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(14, 12, 14, 10)
        self._grid.setSpacing(6)
        self._cells: dict[str, _IndexCell] = {}
        self.set_index_names(index_names)

    def set_index_names(self, index_names: tuple[str, ...]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cells.clear()

        for column, name in enumerate(index_names):
            cell = _IndexCell(name, self)
            self._cells[name] = cell
            self._grid.addWidget(cell, 0, column)
            self._grid.setColumnStretch(column, 1)

    def update_quotes(self, quotes: list[Quote]) -> None:
        """按名称匹配刷新，未携带的指数保留原值（增量语义）。"""
        by_name = {quote.name: quote for quote in quotes}
        for name, cell in self._cells.items():
            quote = by_name.get(name)
            if quote is not None:
                cell.update_quote(quote)

    def reset(self) -> None:
        for cell in self._cells.values():
            cell.update_quote(Quote(code="", name=""))