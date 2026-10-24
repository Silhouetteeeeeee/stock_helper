"""分时图：最新价、均价、成交量、左右双轴与悬停十字线。

数据通过 ``set_data`` 注入，本文件不包含任何行情获取逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import (
    PALETTE,
    format_amount,
    format_change_pct,
    format_price,
    numeric_font,
    ui_font,
)

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
    amount: float = 0.0        # 该分钟成交额（元）；0 表示未提供


class TimeShareChart(QWidget):
    """分时图控件：价格线 / 均价线 / 成交量副图。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._points: list[MinutePoint] = []
        self._pre_close = 0.0
        self._hover_index: int | None = None

        self._axis_font = numeric_font(8, bold=False)
        self._hover_font = ui_font(9)
        self._background_color = QColor(PALETTE.background)
        self._grid_color = QColor(PALETTE.border)
        self._text_color = QColor(PALETTE.text_muted)
        self._price_color = QColor(PALETTE.accent)
        self._avg_color = QColor(PALETTE.avg)
        self._up_color = QColor(PALETTE.up)
        self._down_color = QColor(PALETTE.down)

        self.setFixedHeight(CHART_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

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

        if self._hover_index is not None:
            if self._points:
                self._hover_index = min(self._hover_index, len(self._points) - 1)
            else:
                self._hover_index = None
                self.unsetCursor()
        self.update()

    # -- 悬停交互 ---------------------------------------------------------
    def _chart_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        """返回主图、成交量副图与外边距区域，绘制和鼠标命中共用。"""
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
        return content, plot, volume

    @staticmethod
    def _x_minute(minute: int, plot: QRectF) -> float:
        """按交易分钟序号映射到绘图区横坐标。"""
        minute = max(0, min(SESSION_MINUTES - 1, minute))
        return plot.left() + minute / (SESSION_MINUTES - 1) * plot.width()

    @staticmethod
    def _minute_text(minute: int) -> str:
        """把横轴槽位还原为行情时间，例如 0 -> 09:31、120 -> 13:01。"""
        minute = max(0, min(SESSION_MINUTES - 1, minute))
        if minute < 120:
            total = 9 * 60 + 31 + minute
        else:
            total = 13 * 60 + 1 + minute - 120
        return f"{total // 60:02d}:{total % 60:02d}"

    def _x_index(self, index: int, plot: QRectF) -> float:
        """按数据点下标映射；优先使用数据点自带的交易分钟序号。"""
        point = self._points[index]
        minute = index if point.minute is None else point.minute
        return self._x_minute(minute, plot)

    def _nearest_index(self, x: float, plot: QRectF) -> int:
        """找到离鼠标横坐标最近的分时点。"""
        return min(
            range(len(self._points)),
            key=lambda index: abs(self._x_index(index, plot) - x),
        )

    def _clear_hover(self) -> None:
        if self._hover_index is None:
            return
        self._hover_index = None
        self.unsetCursor()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if not self._points:
            self._clear_hover()
            return

        _, plot, volume = self._chart_rects()
        pos = event.position()
        if not (
            plot.left() <= pos.x() <= plot.right()
            and plot.top() <= pos.y() <= volume.bottom()
        ):
            self._clear_hover()
            return

        index = self._nearest_index(pos.x(), plot)
        if index != self._hover_index:
            self._hover_index = index
            self.setCursor(Qt.CrossCursor)
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._clear_hover()
        super().leaveEvent(event)

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
        content, plot, volume = self._chart_rects()

        high = max(max(p.price for p in self._points), max(p.avg for p in self._points))
        low = min(min(p.price for p in self._points), min(p.avg for p in self._points))
        span = max(high - self._pre_close, self._pre_close - low) * 1.05
        if span <= 0:
            span = max(abs(self._pre_close) * 0.01, 1.0)
        upper = self._pre_close + span
        lower = self._pre_close - span

        def x_minute(minute: int) -> float:
            return self._x_minute(minute, plot)

        def x(index: int) -> float:
            return self._x_index(index, plot)

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

        # 悬停十字线与数值浮层
        if self._hover_index is not None and 0 <= self._hover_index < len(self._points):
            point = self._points[self._hover_index]
            self._draw_hover(
                painter,
                plot,
                volume,
                x(self._hover_index),
                y(point.price),
            )

    def _draw_hover(
        self,
        painter: QPainter,
        plot: QRectF,
        volume: QRectF,
        line_x: float,
        price_y: float,
    ) -> None:
        """绘制悬停竖线、价格点和数值浮层。"""
        if self._hover_index is None:
            return
        point = self._points[self._hover_index]

        if self._pre_close > 0:
            pct = (point.price / self._pre_close - 1) * 100
        else:
            pct = 0.0
        if pct > 0:
            value_color = self._up_color
        elif pct < 0:
            value_color = self._down_color
        else:
            value_color = self._text_color

        # 垂直虚线贯穿主图与成交量副图
        line_color = QColor(self._text_color)
        line_color.setAlpha(180)
        painter.setPen(QPen(line_color, 1, Qt.DashLine))
        painter.drawLine(QPointF(line_x, plot.top()), QPointF(line_x, volume.bottom()))

        # 对应价格点
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._price_color)
        painter.drawEllipse(QPointF(line_x, price_y), 2.5, 2.5)

        # 数值浮层：最新价、涨跌幅、该分钟成交额与时间
        painter.setFont(self._hover_font)
        fm = painter.fontMetrics()
        padding = 6
        gap = 6
        price_text = format_price(point.price)
        pct_text = format_change_pct(pct)
        time_text = self._minute_text(
            point.minute if point.minute is not None else self._hover_index
        )
        amount_label = "成交额"
        amount_text = format_amount(point.amount)
        line1_width = fm.horizontalAdvance(price_text) + gap + fm.horizontalAdvance(pct_text)
        line2_width = (
            fm.horizontalAdvance(time_text)
            + gap
            + fm.horizontalAdvance(amount_label)
            + gap
            + fm.horizontalAdvance(amount_text)
        )
        box_width = max(line1_width, line2_width) + padding * 2
        box_height = fm.height() * 2 + padding * 2 + 2

        # 默认放在竖线右侧，靠近右边界时翻到左侧
        box_left = line_x + 8
        if box_left + box_width > plot.right() - 2:
            box_left = line_x - 8 - box_width
        box_left = max(plot.left() + 2, min(box_left, plot.right() - box_width - 2))
        # 放到价格点所在半区的另一侧，避免浮层遮住当前价
        if price_y < plot.center().y():
            box_top = plot.bottom() - box_height - 4
        else:
            box_top = plot.top() + 4
        box_top = max(plot.top() + 2, min(box_top, plot.bottom() - box_height - 2))
        box = QRectF(box_left, box_top, box_width, box_height)

        background = QColor(PALETTE.surface_alt)
        background.setAlpha(238)
        painter.setBrush(background)
        painter.setPen(QPen(QColor(PALETTE.border), 1))
        painter.drawRoundedRect(box, 4, 4)

        baseline = box_top + padding + fm.ascent()
        text_x = box_left + padding
        painter.setPen(QColor(PALETTE.text))
        painter.drawText(QPointF(text_x, baseline), price_text)
        text_x += fm.horizontalAdvance(price_text) + gap
        painter.setPen(value_color)
        painter.drawText(QPointF(text_x, baseline), pct_text)

        baseline += fm.height() + 2
        text_x = box_left + padding
        painter.setPen(QColor(PALETTE.text))
        painter.drawText(QPointF(text_x, baseline), time_text)
        text_x += fm.horizontalAdvance(time_text) + gap
        painter.setPen(self._text_color)
        painter.drawText(QPointF(text_x, baseline), amount_label)
        text_x += fm.horizontalAdvance(amount_label) + gap
        painter.setPen(QColor(PALETTE.text))
        painter.drawText(QPointF(text_x, baseline), amount_text)
