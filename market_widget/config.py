"""界面配置：窗口行为、配色、字体与默认自选股列表。

界面只依赖本文件中的配置，不含任何数据获取逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 窗口行为
# ---------------------------------------------------------------------------
WINDOW_WIDTH = 320          # 窗口宽度（像素）
WINDOW_MARGIN_X = 12        # 距屏幕右边缘的距离
WINDOW_MARGIN_Y = 12        # 距屏幕顶部的距离
ALWAYS_ON_TOP = True        # 始终置顶
START_MINIMIZED = False     # 启动时是否直接收起为小标题栏
MINIMIZED_HEIGHT = 34       # 纯标题栏高度（锁定迷你态的最小高度）
MINI_MODE_ROW_HEIGHT = 42   # 迷你态指数单行的高度
HOVER_EXPAND_DELAY_MS = 0   # 鼠标移入后延迟多少毫秒展开（0 = 立即）
HOVER_COLLAPSE_DELAY_MS = 200  # 鼠标移出后延迟多少毫秒收回（防抖）
OPACITY = 0.7              # 窗口不透明度（0.5 ~ 1.0）
CORNER_RADIUS = 12          # 圆角半径

# ---------------------------------------------------------------------------
# 配色（简约深色，涨红跌绿）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Palette:
    background: str = "#14161c"        # 窗口底色
    surface: str = "#1c1f27"           # 卡片底色
    surface_alt: str = "#22262f"       # 交替行底色
    border: str = "#2b303b"            # 分隔线
    text: str = "#f2f4f8"              # 主文字
    text_muted: str = "#8b93a5"        # 次要文字
    accent: str = "#4c8dff"            # 强调色
    up: str = "#ff4d4f"                # 涨（红）
    down: str = "#22c55e"              # 跌（绿）
    flat: str = "#8b93a5"              # 平（灰）
    hover: str = "#262b36"             # 悬停底色

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Fonts:
    family: str = "Microsoft YaHei UI"
    fallbacks: tuple[str, ...] = ("PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC")
    numeric_family: str = "Consolas"
    title: int = 11
    label: int = 12
    value: int = 13
    big_value: int = 16
    small: int = 10

# ---------------------------------------------------------------------------
# 自选股数据模型（仅承载界面展示所需的字段）
# ---------------------------------------------------------------------------
@dataclass
class Quote:
    """一条行情记录。``change_pct`` 为涨跌幅（百分比数值，例如 1.23 表示 +1.23%）。"""

    code: str
    name: str
    price: float | None = None
    change_pct: float | None = None

    @property
    def direction(self) -> str:
        """返回 ``"up"`` / ``"down"`` / ``"flat"``，供界面决定配色。"""
        if self.change_pct is None or self.change_pct == 0:
            return "flat"
        return "up" if self.change_pct > 0 else "down"

@dataclass
class WatchItem:
    """自选股列表项：代码 + 备注名。"""

    code: str
    name: str

# 默认自选股（示例，可随时替换为真实代码）
DEFAULT_WATCHLIST: list[WatchItem] = [
    WatchItem("sh600519", "贵州茅台"),
    WatchItem("sz000001", "平安银行"),
    WatchItem("sh601318", "中国平安"),
    WatchItem("sz300750", "宁德时代"),
    WatchItem("sh600036", "招商银行"),
]

# 顶部展示的大盘指数
INDEX_NAMES: tuple[str, ...] = ("上证指数", "深证成指", "创业板指")

# 指数面板每个单元格的固定宽度（像素），横向超出后可滚动
INDEX_CELL_WIDTH = 90