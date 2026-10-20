
import sys
import traceback
from datetime import datetime

import akshare as ak
import pandas as pd

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)


# =========================
# 配色
# =========================
BG = "#11151C"
PANEL = "#191F29"
CARD = "#202735"
BORDER = "#2B3443"
TEXT = "#E7ECF3"
MUTED = "#8E9AAF"
BLUE = "#5B8DEF"
RED = "#F16D75"       # A股上涨
GREEN = "#35C99A"     # A股下跌

INDEX_CODES = [
    "sh000001",  # 上证指数
    "sh000300",  # 沪深300
    "sh000688",  # 科创50
    "sz399006",  # 创业板指
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
]


def number(value):
    try:
        value = float(value)
        return value if pd.notna(value) else None
    except (ValueError, TypeError):
        return None


def fmt_price(value):
    n = number(value)
    return "--" if n is None else f"{n:,.2f}"


def fmt_change(value):
    n = number(value)
    return "--" if n is None else f"{n:+.2f}%"


# =========================
# 指数小卡片
# =========================
class IndexCard(QFrame):
    def __init__(self, code, name):
        super().__init__()
        self.code = code
        self.setObjectName("IndexCard")
        self.setMinimumHeight(76)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self.name_label = QLabel(name)
        self.name_label.setObjectName("IndexName")

        self.change_label = QLabel("--")
        self.change_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        top.addWidget(self.name_label)
        top.addStretch()
        top.addWidget(self.change_label)

        self.price_label = QLabel("--")
        self.price_label.setObjectName("IndexPrice")

        layout.addLayout(top)
        layout.addWidget(self.price_label)

    def update_data(self, price, change):
        self.price_label.setText(fmt_price(price))
        n = number(change)
        self.change_label.setText(fmt_change(change))

        color = MUTED
        if n is not None:
            if n > 0:
                color = RED
            elif n < 0:
                color = GREEN

        self.change_label.setStyleSheet(
            f"color: {color}; font-weight: 600;"
        )
        self.price_label.setStyleSheet(
            f"color: {color}; font-size: 17px; font-weight: 700;"
        )


# =========================
# 后台获取行情
# =========================
class MarketWorker(QThread):
    data_ready = Signal(object, object)
    error_occurred = Signal(str)

    def run(self):
        try:
            # 获取 A 股行情
            stocks = ak.stock_zh_a_spot()

            if stocks is None or stocks.empty:
                raise RuntimeError("A 股行情返回为空")

            # 获取指数行情
            indexes = ak.stock_zh_index_spot_sina()

            if indexes is None or indexes.empty:
                raise RuntimeError("指数行情返回为空")

            self.data_ready.emit(stocks, indexes)

        except Exception:
            self.error_occurred.emit(traceback.format_exc())


# =========================
# 主窗口
# =========================
class StockMonitor(QWidget):
    def __init__(self):
        super().__init__()

        # 无 Windows 原生标题栏及最小化/最大化/关闭按钮
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self.resize(650, 660)
        self.setMinimumSize(560, 560)

        self.stocks = pd.DataFrame()
        self.indexes = pd.DataFrame()
        self.worker = None
        self.drag_position = None

        self.build_ui()
        self.apply_style()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start(60_000)

        self.refresh_data()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(9)

        # ----- 自定义标题栏 -----
        title_bar = QHBoxLayout()
        title_bar.setSpacing(8)

        logo = QLabel("M")
        logo.setObjectName("Logo")
        logo.setFixedSize(29, 29)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Market Desk")
        title.setObjectName("MainTitle")

        self.status_label = QLabel("正在连接...")
        self.status_label.setObjectName("StatusLabel")

        refresh_btn = QPushButton("↻")
        refresh_btn.setObjectName("ToolButton")
        refresh_btn.setFixedSize(30, 28)
        refresh_btn.setToolTip("刷新行情")
        refresh_btn.clicked.connect(self.refresh_data)

        close_btn = QPushButton("×")
        close_btn.setObjectName("CloseButton")
        close_btn.setFixedSize(30, 28)
        close_btn.setToolTip("关闭窗口")
        close_btn.clicked.connect(self.close)

        title_bar.addWidget(logo)
        title_bar.addWidget(title)
        title_bar.addStretch()
        title_bar.addWidget(self.status_label)
        title_bar.addWidget(refresh_btn)
        title_bar.addWidget(close_btn)

        root.addLayout(title_bar)

        # ----- 指数监控标题 -----
        section = QHBoxLayout()
        index_title = QLabel("大盘指数")
        index_title.setObjectName("SectionTitle")

        self.update_label = QLabel("更新时间：--")
        self.update_label.setObjectName("MutedText")

        section.addWidget(index_title)
        section.addStretch()
        section.addWidget(self.update_label)
        root.addLayout(section)

        # ----- 2 行 × 3 列指数卡片 -----
        index_grid = QGridLayout()
        index_grid.setSpacing(7)

        index_names = {
            "sh000001": "上证指数",
            "sh000300": "沪深300",
            "sh000688": "科创50",
            "sz399006": "创业板指",
            "sh000905": "中证500",
            "sh000852": "中证1000",
        }

        self.index_cards = {}

        for i, code in enumerate(INDEX_CODES):
            card = IndexCard(code, index_names[code])
            self.index_cards[code] = card
            index_grid.addWidget(card, i // 3, i % 3)

        root.addLayout(index_grid)

        # ----- 股票行情区域 -----
        stock_header = QHBoxLayout()

        stock_title = QLabel("A 股行情")
        stock_title.setObjectName("SectionTitle")

        self.count_label = QLabel("0 只股票")
        self.count_label.setObjectName("MutedText")

        stock_header.addWidget(stock_title)
        stock_header.addStretch()
        stock_header.addWidget(self.count_label)

        root.addLayout(stock_header)

        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索股票名称或代码...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.render_stocks)
        root.addWidget(self.search_input)

        # 表格只展示关键指标
        self.columns = [
            "代码", "名称", "最新价", "涨跌幅", "成交额", "换手率"
        ]

        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(31)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )

        root.addWidget(self.table, 1)

        # 底部说明
        footer = QHBoxLayout()
        self.footer_label = QLabel(
            "数据来源：AKShare"
        )
        self.footer_label.setObjectName("MutedText")

        self.result_label = QLabel("")
        self.result_label.setObjectName("MutedText")

        footer.addWidget(self.footer_label)
        footer.addStretch()
        footer.addWidget(self.result_label)
        root.addLayout(footer)

    def apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: 12px;
            }}

            QLabel#Logo {{
                background: {BLUE};
                color: white;
                border-radius: 8px;
                font-size: 17px;
                font-weight: bold;
            }}

            QLabel#MainTitle {{
                font-size: 16px;
                font-weight: 700;
            }}

            QLabel#SectionTitle {{
                font-size: 13px;
                font-weight: 700;
            }}

            QLabel#MutedText {{
                color: {MUTED};
                font-size: 10px;
            }}

            QLabel#StatusLabel {{
                color: {MUTED};
                font-size: 10px;
            }}

            QFrame#IndexCard {{
                background: {PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}

            QLabel#IndexName {{
                color: {MUTED};
                font-size: 11px;
            }}

            QLabel#IndexPrice {{
                font-size: 17px;
                font-weight: 700;
            }}

            QPushButton#ToolButton {{
                background: {PANEL};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 7px;
                font-size: 17px;
            }}

            QPushButton#ToolButton:hover {{
                background: {CARD};
            }}

            QPushButton#CloseButton {{
                background: {PANEL};
                color: {MUTED};
                border: 1px solid {BORDER};
                border-radius: 7px;
                font-size: 20px;
            }}

            QPushButton#CloseButton:hover {{
                background: #A83F4A;
                color: white;
            }}

            QLineEdit {{
                background: {PANEL};
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 7px;
                padding: 7px 9px;
            }}

            QLineEdit:focus {{
                border: 1px solid {BLUE};
            }}

            QTableWidget {{
                background: {PANEL};
                alternate-background-color: {CARD};
                border: 1px solid {BORDER};
                border-radius: 8px;
                selection-background-color: #30415F;
                selection-color: white;
            }}

            QTableWidget::item {{
                padding: 3px;
                border: none;
            }}

            QHeaderView::section {{
                background: {PANEL};
                color: {MUTED};
                border: none;
                border-bottom: 1px solid {BORDER};
                padding: 8px 3px;
                font-size: 11px;
                font-weight: 600;
            }}

            QScrollBar:vertical {{
                background: {BG};
                width: 7px;
                margin: 0;
            }}

            QScrollBar::handle:vertical {{
                background: #3B4658;
                border-radius: 3px;
                min-height: 24px;
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

    # ----- 无标题栏拖动支持 -----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 仅允许在顶部约 45 像素区域拖动
            if event.position().toPoint().y() <= 45:
                self.drag_position = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint()
                - self.drag_position
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_position = None
        super().mouseReleaseEvent(event)

    # ----- 刷新行情 -----
    def refresh_data(self):
        if self.worker is not None and self.worker.isRunning():
            return

        self.status_label.setText("正在更新...")
        self.worker = MarketWorker(self)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_data_ready(self, stocks, indexes):
        self.stocks = stocks.copy()
        self.indexes = indexes.copy()

        self.update_indexes()
        self.render_stocks()

        self.update_label.setText(
            "更新：" + datetime.now().strftime("%H:%M:%S")
        )
        self.status_label.setText("● 已连接")
        self.status_label.setStyleSheet(f"color: {GREEN};")
        self.footer_label.setText("数据来源：AKShare")

    def update_indexes(self):
        if self.indexes.empty:
            return

        # 兼容代码两侧存在空格的情况
        self.indexes["代码"] = (
            self.indexes["代码"].astype(str).str.strip()
        )

        selected = self.indexes[
            self.indexes["代码"].isin(INDEX_CODES)
        ]

        for _, row in selected.iterrows():
            code = row["代码"]
            card = self.index_cards.get(code)
            if card is None:
                continue

            # 接口通常包含“最新价”和“涨跌幅”
            price = row.get("最新价")
            change = row.get("涨跌幅")

            card.update_data(price, change)

    def render_stocks(self):
        if self.stocks.empty:
            return

        df = self.stocks.copy()

        # 兼容不同接口的字段类型
        if "代码" in df.columns:
            df["代码"] = df["代码"].astype(str).str.zfill(6)
        if "名称" in df.columns:
            df["名称"] = df["名称"].fillna("").astype(str)

        keyword = self.search_input.text().strip().lower()

        if keyword:
            mask = (
                df["代码"].astype(str).str.lower().str.contains(
                    keyword, na=False
                )
                | df["名称"].astype(str).str.lower().str.contains(
                    keyword, na=False
                )
            )
            df = df[mask]

        self.count_label.setText(f"{len(self.stocks):,} 只股票")
        self.result_label.setText(f"当前显示 {len(df):,} 只")

        # 排序列必须在界面刷新期间暂时关闭
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(df))

        for r, (_, row) in enumerate(df.iterrows()):
            change = number(row.get("涨跌幅"))

            for c, col in enumerate(self.columns):
                value = row.get(col, None)

                if col == "成交额" and number(value) is not None:
                    text = f"{number(value) / 1e8:.2f}亿"
                elif col == "涨跌幅":
                    text = fmt_change(value)
                elif col == "最新价":
                    text = fmt_price(value)
                elif col == "换手率" and number(value) is not None:
                    text = f"{number(value):.2f}%"
                elif value is None or pd.isna(value):
                    text = "--"
                else:
                    text = str(value)

                item = QTableWidgetItem(text)

                if col in ("最新价", "涨跌幅"):
                    color = TEXT
                    if change is not None:
                        if change > 0:
                            color = RED
                        elif change < 0:
                            color = GREEN
                    item.setForeground(QColor(color))

                if col in ("代码", "名称"):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter
                        | Qt.AlignmentFlag.AlignLeft
                    )
                else:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter
                        | Qt.AlignmentFlag.AlignRight
                    )

                self.table.setItem(r, c, item)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    def on_error(self, message):
        self.status_label.setText("● 获取失败")
        self.status_label.setStyleSheet(f"color: {RED};")
        self.footer_label.setText("获取失败：请检查接口或网络")
        print("行情获取错误：\n", message)

    def on_worker_finished(self):
        pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = StockMonitor()
    window.show()
    sys.exit(app.exec())
