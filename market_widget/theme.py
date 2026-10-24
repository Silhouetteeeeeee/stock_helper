"""样式工具：配色取值、数值格式化与全局样式表。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

from .config import CORNER_RADIUS, Fonts, Palette

PALETTE = Palette()
FONTS = Fonts()
ASSETS = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    """返回样式表可用的资源绝对路径（统一用正斜杠）。"""
    return (ASSETS / name).as_posix()


def color_for_direction(direction: str) -> str:
    """按涨跌方向返回文字颜色。"""
    return {"up": PALETTE.up, "down": PALETTE.down}.get(direction, PALETTE.flat)

def format_change_pct(change_pct: float | None) -> str:
    """把涨跌幅格式化为带符号的百分比文本。"""
    if change_pct is None:
        return "--"
    return f"{change_pct:+.2f}%"

def format_price(price: float | None) -> str:
    """把价格格式化为两位小数文本。"""
    return "--" if price is None else f"{price:,.2f}"

def format_amount(amount: float | None) -> str:
    """把成交额格式化为万 / 亿文本。"""
    if amount is None or amount <= 0:
        return "--"
    if amount >= 1e8:
        return f"{amount / 1e8:.2f}亿"
    if amount >= 1e4:
        return f"{amount / 1e4:.2f}万"
    return f"{amount:.0f}"

def resolve_font_family(candidates: tuple[str, ...] | list[str]) -> str:
    """返回系统中第一个可用的字体名，全部缺失时回退到 Qt 默认字体。"""
    installed = set(QFontDatabase.families())
    for name in candidates:
        if name in installed:
            return name
    return QFont().defaultFamily()

def ui_font(size: int, bold: bool = False) -> QFont:
    """构造界面正文字体。"""
    font = QFont(resolve_font_family((FONTS.family, *FONTS.fallbacks)), size)
    font.setBold(bold)
    return font

def numeric_font(size: int, bold: bool = True) -> QFont:
    """构造数字字体，等宽对齐让涨跌幅更易纵向比较。"""
    family = resolve_font_family((FONTS.numeric_family, FONTS.family, *FONTS.fallbacks))
    font = QFont(family, size)
    font.setBold(bold)
    return font

def build_stylesheet() -> str:
    """生成整个窗口的 QSS 样式表。"""
    return f"""
    QWidget#Root {{
        background: transparent;
    }}
    QFrame#Card {{
        background-color: {PALETTE.background};
        border: 1px solid {PALETTE.border};
        border-radius: {CORNER_RADIUS}px;
    }}
    QLabel {{
        color: {PALETTE.text};
        background: transparent;
    }}
    QLabel#SectionTitle {{
        color: {PALETTE.text_muted};
        letter-spacing: 1px;
    }}
    QFrame#Divider {{
        background-color: {PALETTE.border};
        border: none;
        max-height: 1px;
    }}
    QPlainTextEdit {{
        background-color: {PALETTE.surface_alt};
        border: 1px solid {PALETTE.border};
        border-radius: 6px;
        color: {PALETTE.text};
        padding: 4px 6px;
        selection-background-color: {PALETTE.accent};
    }}
    QPlainTextEdit:focus {{
        border-color: {PALETTE.accent};
    }}
    QPushButton#IconButton {{
        background: transparent;
        border: none;
        border-radius: 6px;
        color: {PALETTE.text_muted};
        font-size: 14px;
        padding: 0px;
    }}
    QPushButton#IconButton:hover {{
        background-color: {PALETTE.hover};
        color: {PALETTE.text};
    }}
    QPushButton#IconButton:checked {{
        background-color: {PALETTE.hover};
        color: {PALETTE.accent};
    }}
    QPushButton#IconButton:focus {{
        outline: none;
    }}
    QPushButton#CloseButton {{
        background: transparent;
        border: none;
        border-radius: 6px;
        color: {PALETTE.text_muted};
        font-size: 14px;
        padding: 0px;
    }}
    QPushButton#CloseButton:hover {{
        background-color: {PALETTE.danger};
        color: #ffffff;
    }}
    QPushButton#PrimaryButton {{
        background-color: {PALETTE.accent};
        border: none;
        border-radius: 6px;
        color: #ffffff;
        padding: 5px 12px;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: #6ba0ff;
    }}
    QPushButton#GhostButton {{
        background: transparent;
        border: 1px solid {PALETTE.border};
        border-radius: 6px;
        color: {PALETTE.text_muted};
        padding: 5px 10px;
    }}
    QPushButton#GhostButton:hover {{
        border-color: {PALETTE.accent};
        color: {PALETTE.text};
    }}
    QLineEdit, QDoubleSpinBox, QSpinBox {{
        background-color: {PALETTE.surface_alt};
        border: 1px solid {PALETTE.border};
        border-radius: 6px;
        color: {PALETTE.text};
        padding: 4px 8px;
        selection-background-color: {PALETTE.accent};
    }}
    QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
        border-color: {PALETTE.accent};
    }}
    QCheckBox {{
        background: transparent;
        color: {PALETTE.text};
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid {PALETTE.border};
        border-radius: 4px;
        background-color: {PALETTE.surface_alt};
    }}
    QCheckBox::indicator:hover {{
        border-color: {PALETTE.text_muted};
    }}
    QCheckBox::indicator:checked {{
        background-color: {PALETTE.accent};
        border-color: {PALETTE.accent};
        image: url("{_asset('check.svg')}");
    }}
    QComboBox {{
        background-color: {PALETTE.surface_alt};
        border: 1px solid {PALETTE.border};
        border-radius: 6px;
        color: {PALETTE.text};
        padding: 4px 8px;
    }}
    QComboBox:hover {{
        border-color: {PALETTE.text_muted};
    }}
    QComboBox:focus {{
        border-color: {PALETTE.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox::down-arrow {{
        image: url("{_asset('chevron-down.svg')}");
        width: 10px;
        height: 6px;
        margin-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {PALETTE.surface_alt};
        border: 1px solid {PALETTE.border};
        border-radius: 6px;
        color: {PALETTE.text};
        selection-background-color: {PALETTE.hover};
        outline: none;
        padding: 2px;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 22px;
        padding-left: 6px;
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {PALETTE.border};
        border-radius: 3px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {PALETTE.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 6px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {PALETTE.border};
        border-radius: 3px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {PALETTE.text_muted};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
        width: 0px;
    }}
    QToolTip {{
        background-color: {PALETTE.surface_alt};
        border: 1px solid {PALETTE.border};
        color: {PALETTE.text};
        padding: 4px 6px;
    }}
    """