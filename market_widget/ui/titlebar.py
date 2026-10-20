"""自定义标题栏：拖动移动窗口，右侧为收起与关闭按钮。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..theme import PALETTE, ui_font


class TitleBar(QWidget):
    """窗口顶部条：可拖动移动窗口，右侧为收起/关闭按钮。"""

    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None   # 拖动时记录鼠标与窗口左上角的偏移量
        self.setFixedHeight(34)
        self.setCursor(Qt.SizeAllCursor)

        self._title = QLabel(title)               # 标题文字
        self._title.setFont(ui_font(11, bold=True))
        self._title.setStyleSheet(f"color: {PALETTE.text}; background: transparent;")

        self._live_dot = QLabel("●")              # 数据源指示灯：绿色=已接入实时数据
        self._live_dot.setStyleSheet(f"color: {PALETTE.down}; background: transparent;")
        self._live_dot.setToolTip("数据源未接入，示例数据")

        self._min_button = self._icon_button("—", "锁定迷你")   # 收起/锁定按钮
        self._min_button.clicked.connect(self.minimize_clicked.emit)

        self._close_button = self._icon_button("✕", "退出")     # 关闭按钮
        self._close_button.setObjectName("CloseButton")
        self._close_button.clicked.connect(self.close_clicked.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)
        layout.addWidget(self._live_dot)
        layout.addWidget(self._title)
        layout.addStretch(1)
        layout.addWidget(self._min_button)
        layout.addWidget(self._close_button)

    # -- 对外接口 ---------------------------------------------------------
    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_live(self, live: bool, tip: str) -> None:
        """切换数据源指示灯：绿色表示已接入实时数据。"""
        color = PALETTE.down if live else PALETTE.text_muted
        self._live_dot.setStyleSheet(f"color: {color}; background: transparent;")
        self._live_dot.setToolTip(tip)

    def set_minimized(self, minimized: bool) -> None:
        """锁定迷你态时把收起按钮改成「展开」。"""
        if minimized:
            self._min_button.setText("▾")
            self._min_button.setToolTip("展开")
        else:
            self._min_button.setText("—")
            self._min_button.setToolTip("锁定迷你")

    # -- 拖动窗口 ---------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None

    # -- 内部实现 ---------------------------------------------------------
    def _icon_button(self, text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("IconButton")
        button.setFixedSize(22, 22)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setFont(ui_font(10))
        return button
