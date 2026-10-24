"""分时图：最新价、均价、成交量与左右双轴。

数据通过 ``set_data`` 注入，本文件不包含任何行情获取逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import PALETTE, numeric_font, ui_font

SESSION_MINUTES = 240          # A 股一个交易日的分钟数
CHART_HEIGHT = 168             # 图表固定高度
TIME_TICKS = (
    (0, "09:30"),
    (60, "10:30"),
    (120, "13:00"),
    (180, "14:00"),
    (239, "15:00"),
)


@dataclass
class MinutePoint:
    """分时图上的一个分钟点。"""

    price: float
    avg: float
    volume: float
    minute: int | None = None  # 分时图横轴槽位（0..239）；None 时按列表下标


class TimeShareChart(QWidget):
    """分时图控件：价格线 / 均价线 / 成交量副图。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._points: list[MinutePoint] = []
        self._pre_close = 0.0

        self._axis_font = numeric_font(8, bold=False)
        self._background_color = QColor(PALETTE.background)
        self._grid_color = QColor(PALETTE.border)
        self._text_color = QColor(PALETTE.text_muted)
        self._price_color = QColor(PALETTE.accent)
        self._avg_color = QColor(PALETTE.avg)
        self._up_color = QColor(PALETTE.up)
        self._down_color = QColor(PALETTE.down)

        self.setFixedHeight(CHART_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # -- 数据入口 ---------------------------------------------------------
    def set_data(self, points: list[MinutePoint], pre_close: float) -> None:
        """刷新分时数据（仅限主线程）；带 minute 时按槽位归位，最多 240 个点。"""
        points = list(points)
        if points and all(point.minute is not None for point in points):
            # 按交易分钟归位并去重，避免缺分钟或重复分钟导致横轴偏移。
            by_minute: dict[int, MinutePoint] = {}
            for point in points:
                if 0 <= point.minute < SESSION_MINUTES:
                    by_minute[point.minute] = point
            points = [by_minute[minute] for minute in sorted(by_minute)]

        self._points = points[:SESSION_MINUTES]
        if pre_close > 0:
            self._pre_close = pre_close
        elif self._points:
            self._pre_close = self._points[0].price
        else:
            self._pre_close = 0.0
        self.update()

    # -- 绘制 -------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self._background_color)

        if not self._points or self._pre_close <= 0:
            painter.setFont(ui_font(9))
            painter.setPen(self._text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无分时数据")
            return

        # 主图 + 成交量副图；左右各留出价格轴和涨跌幅轴的位置
        content = QRectF(self.rect()).adjusted(14, 8, -14, -18)
        plot = QRectF(
            content.left() + 46,
            content.top(),
            max(1.0, content.width() - 86),
            content.height() * 0.72,
        )
        volume = QRectF(
            plot.left(),
            plot.bottom() + 8,
            plot.width(),
            max(1.0, content.bottom() - plot.bottom() - 8),
        )

        high = max(max(p.price for p in self._points), max(p.avg for p in self._points))
        low = min(min(p.price for p in self._points), min(p.avg for p in self._points))
        span = max(high - self._pre_close, self._pre_close - low) * 1.05
        if span <= 0:
            span = max(abs(self._pre_close) * 0.01, 1.0)
        upper = self._pre_close + span
        lower = self._pre_close - span

        def x_minute(minute: int) -> float:
            """按交易分钟序号映射到绘图区横坐标。"""
            minute = max(0, min(SESSION_MINUTES - 1, minute))
            return plot.left() + minute / (SESSION_MINUTES - 1) * plot.width()

        def x(index: int) -> float:
            """按数据点下标映射；优先使用数据点自带的交易分钟序号。"""
            point = self._points[index]
            minute = index if point.minute is None else point.minute
            return x_minute(minute)

        def y(value: float) -> float:
            """按价格映射到绘图区纵坐标。"""
            return plot.bottom() - (value - lower) / (upper - lower) * plot.height()

        painter.setFont(self._axis_font)

        # 横向网格 + 左轴价格 / 右轴涨跌幅
        for level in range(5):
            value = lower + (upper - lower) * level / 4
            grid_y = y(value)

            painter.setPen(QPen(self._grid_color, 1))
            painter.drawLine(QPointF(plot.left(), grid_y), QPointF(plot.right(), grid_y))

            painter.setPen(self._text_color)
            painter.drawText(
                QRectF(content.left(), grid_y - 7, 42, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                f"{value:.2f}",
            )

            change_pct = round((value / self._pre_close - 1) * 100, 2)
            if change_pct > 0:
                pct_color = self._up_color
            elif change_pct < 0:
                pct_color = self._down_color
            else:
                pct_color = self._text_color
            painter.setPen(pct_color)
            painter.drawText(
                QRectF(plot.right() + 4, grid_y - 7, 36, 14),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{change_pct:+.2f}%",
            )

        # 纵向网格：只画中间的整点，首尾与绘图区边缘重合
        painter.setPen(QPen(self._grid_color, 1))
        for index, _ in TIME_TICKS[1:-1]:
            grid_x = x_minute(index)
            painter.drawLine(QPointF(grid_x, plot.top()), QPointF(grid_x, volume.bottom()))

        # 价格线与均价线
        price_path = QPainterPath(QPointF(x(0), y(self._points[0].price)))
        avg_path = QPainterPath(QPointF(x(0), y(self._points[0].avg)))
        for index, point in enumerate(self._points[1:], 1):
            price_path.lineTo(x(index), y(point.price))
            avg_path.lineTo(x(index), y(point.avg))

        painter.save()
        painter.setClipRect(plot)

        # 价格线下方的渐变填充
        fill_path = QPainterPath()
        fill_path.addPath(price_path)
        fill_path.lineTo(x(len(self._points) - 1), plot.bottom())
        fill_path.lineTo(x(0), plot.bottom())
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top_color = QColor(self._price_color)
        top_color.setAlpha(42)
        bottom_color = QColor(self._price_color)
        bottom_color.setAlpha(0)
        gradient.setColorAt(0.0, top_color)
        gradient.setColorAt(1.0, bottom_color)
        painter.fillPath(fill_path, QBrush(gradient))

        painter.setPen(QPen(self._avg_color, 1.0))
        painter.drawPath(avg_path)
        painter.setPen(QPen(self._price_color, 1.2))
        painter.drawPath(price_path)

        painter.restore()

        # 成交量副图：红涨绿跌，柱高按当日最大成交量归一化
        max_volume = max((point.volume for point in self._points), default=0.0)
        if max_volume > 0:
            bar_width = max(1.0, plot.width() / SESSION_MINUTES * 0.8)
            painter.save()
            painter.setClipRect(volume)
            painter.setPen(Qt.NoPen)
            for index, point in enumerate(self._points):
                previous = self._points[index - 1].price if index else self._pre_close
                color = self._up_color if point.price >= previous else self._down_color
                height = point.volume / max_volume * volume.height()
                painter.fillRect(
                    QRectF(x(index) - bar_width / 2, volume.bottom() - height, bar_width, height),
                    color,
                )
            painter.restore()

        # 成交量上边线与横轴
        painter.setPen(QPen(self._grid_color, 1))
        painter.drawLine(
            QPointF(volume.left(), volume.top()),
            QPointF(volume.right(), volume.top()),
        )
        painter.drawLine(
            QPointF(volume.left(), volume.bottom()),
            QPointF(volume.right(), volume.bottom()),
        )

        # 时间刻度
        painter.setFont(self._axis_font)
        painter.setPen(self._text_color)
        for index, text in TIME_TICKS:
            label_rect = QRectF(x_minute(index) - 30, content.bottom() + 2, 60, 14)
            if index == 0:
                alignment = Qt.AlignLeft | Qt.AlignVCenter
            elif index == SESSION_MINUTES - 1:
                alignment = Qt.AlignRight | Qt.AlignVCenter
            else:
                alignment = Qt.AlignHCenter | Qt.AlignVCenter
            painter.drawText(label_rect, alignment, text)
