"""数据输入面板：所有与数据对接相关的手动入口都集中在这里。

界面本身不抓取行情，只负责把用户/数据源提供的内容解析成 ``Quote`` 后抛给主窗口。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from common.stock import *
from ..config import WatchItem
from ..theme import PALETTE, ui_font

PLACEHOLDER = (
    "每行一条：代码,名称,现价,涨跌幅\n"
    "sh000001,上证指数,3210.45,+0.86\n"
    "sz000001,平安银行,11.20,-1.35"
)

class InputPanel(QWidget):
    """手动喂数据的区域。默认折叠，点标题即可展开。"""

    quotes_submitted = Signal(list)      # list[Quote]
    item_added = Signal(object)          # WatchItem
    item_removed = Signal(str)           # 代码
    refresh_requested = Signal()
    auto_refresh_toggled = Signal(bool, int)
    layout_changed = Signal()            # 展开/收起后提示窗口重新适配高度

    def __init__(self, refresh_interval: int = 5, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._toggle = QPushButton("数据输入 ▸")
        self._toggle.setObjectName("GhostButton")
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setFont(ui_font(9))
        self._toggle.clicked.connect(self._on_toggle)

        self._content = QWidget(self)
        self._content.setVisible(False)
        self._build_content(refresh_interval)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._toggle)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addWidget(self._content)

    # -- 数据入口 ---------------------------------------------------------
    def toggle(self, expanded: bool | None = None) -> None:
        """展开或收起面板；``expanded`` 为 None 时切换当前状态。"""
        target = (not self._content.isVisible()) if expanded is None else expanded
        self._content.setVisible(target)
        self._toggle.setText("数据输入 ▾" if target else "数据输入 ▸")
        self.layout_changed.emit()

    # -- 内部实现 ---------------------------------------------------------
    def _build_content(self, refresh_interval: int) -> None:
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1) 批量粘贴行情
        self._batch = QPlainTextEdit()
        self._batch.setPlaceholderText(PLACEHOLDER)
        self._batch.setFixedHeight(76)
        self._batch.setFont(ui_font(9))
        layout.addWidget(self._hint("批量填入行情（一行一只，逗号分隔）"))
        layout.addWidget(self._batch)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)
        apply_button = QPushButton("解析并刷新")
        apply_button.setObjectName("PrimaryButton")
        apply_button.setCursor(Qt.PointingHandCursor)
        apply_button.clicked.connect(self._on_apply_batch)
        clear_button = QPushButton("清空输入")
        clear_button.setObjectName("GhostButton")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.clicked.connect(self._batch.clear)
        batch_row.addWidget(apply_button)
        batch_row.addWidget(clear_button)
        batch_row.addStretch(1)
        layout.addLayout(batch_row)

        # 2) 增删自选股
        layout.addWidget(self._hint("自选股管理"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("代码 如 sh600519")
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("名称 如 贵州茅台")
        add_button = QPushButton("添加")
        add_button.setObjectName("GhostButton")
        add_button.setCursor(Qt.PointingHandCursor)
        add_button.clicked.connect(self._on_add_item)
        remove_button = QPushButton("移除")
        remove_button.setObjectName("GhostButton")
        remove_button.setCursor(Qt.PointingHandCursor)
        remove_button.setToolTip("按左侧代码移除自选股")
        remove_button.clicked.connect(self._on_remove_item)
        grid.addWidget(self._code_input, 0, 0)
        grid.addWidget(self._name_input, 0, 1)
        grid.addWidget(add_button, 0, 2)
        grid.addWidget(remove_button, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        layout.addLayout(grid)

        # 3) 刷新控制
        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(6)
        self._auto = QCheckBox("自动刷新")
        self._auto.setFont(ui_font(9))
        self._auto.toggled.connect(self._on_auto_toggled)
        self._interval = QSpinBox()
        self._interval.setRange(1, 600)
        self._interval.setValue(refresh_interval)
        self._interval.setSuffix(" 秒")
        self._interval.setFixedWidth(84)
        manual_button = QPushButton("立即刷新")
        manual_button.setObjectName("GhostButton")
        manual_button.setCursor(Qt.PointingHandCursor)
        manual_button.clicked.connect(self.refresh_requested.emit)
        refresh_row.addWidget(self._auto)
        refresh_row.addWidget(self._interval)
        refresh_row.addWidget(manual_button)
        refresh_row.addStretch(1)
        layout.addLayout(refresh_row)

        note = QLabel("此处仅提供数据入口，未接入真实行情源。")
        note.setFont(ui_font(8))
        note.setStyleSheet(f"color: {PALETTE.text_muted};")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(ui_font(9))
        label.setStyleSheet(f"color: {PALETTE.text_muted};")
        return label

    def _on_toggle(self) -> None:
        self.toggle()

    def _on_apply_batch(self) -> None:
        self.quotes_submitted.emit(parse_batch(self._batch.toPlainText()))

    def _on_add_item(self) -> None:
        code = self._code_input.text().strip()
        if not code:
            return
        name = self._name_input.text().strip() or code
        self.item_added.emit(WatchItem(code=code, name=name))
        self._code_input.clear()
        self._name_input.clear()

    def _on_remove_item(self) -> None:
        code = self._code_input.text().strip()
        if code:
            self.item_removed.emit(code)
            self._code_input.clear()

    def _on_auto_toggled(self, enabled: bool) -> None:
        self.auto_refresh_toggled.emit(enabled, self._interval.value())

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
        quotes.append(Quote(code=code, name=name, price=price, change_pct=change))
    return quotes

def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None