"""界面配置：窗口行为、配色、字体与自选股初始列表。

界面只依赖本文件中的配置，不含任何数据获取逻辑。
自选股实际保存在 ``data/stock.db`` 的 ``stock.watchlist_order`` 字段中，
本文件的列表只用于首次升级时写入数据库。
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
OPACITY = 0.8              # 窗口不透明度（0.5 ~ 1.0）
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
    avg: str = "#f5a623"               # 分时均价线（橙）
    up: str = "#ff4d4f"                # 涨（红）
    down: str = "#22c55e"              # 跌（绿）
    flat: str = "#8b93a5"              # 平（灰）
    hover: str = "#262b36"             # 悬停底色
    danger: str = "#e5484d"            # 危险操作（关闭等），与涨跌红区分

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
class WatchItem:
    """自选股列表项：代码 + 备注名。"""

    code: str
    name: str

# 首次迁移到数据库时写入的初始自选股；之后增删都以数据库为准
DEFAULT_WATCHLIST: list[WatchItem] = [
    WatchItem("605499", "东鹏饮料"),
    WatchItem("600036", "招商银行"),
    WatchItem("601899", "紫金矿业"),
    WatchItem("601318", "中国平安"),
    WatchItem("600066", "宇通客车"),
]

# 顶部展示的大盘指数（代码, 名称）
INDEX_ITEMS: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sh000688", "科创50"),
    ("sz399006", "创业板指"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
)
INDEX_CODES: tuple[str, ...] = tuple(code for code, _ in INDEX_ITEMS)
INDEX_NAMES: tuple[str, ...] = tuple(name for _, name in INDEX_ITEMS)

# 指数面板每个单元格的固定宽度（像素），横向超出后可滚动
INDEX_CELL_WIDTH = 90