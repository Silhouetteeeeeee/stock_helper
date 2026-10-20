"""桌面行情悬浮窗：始终置顶显示在屏幕右上角，一眼看清大盘与自选股。

本包只负责界面（UI），不包含任何数据抓取逻辑，数据通过公开接口注入：

    from market_widget import MarketWidget, Quote

    window = MarketWidget()
    window.show()
    window.set_live(True)                       # 点亮标题栏的数据指示灯
    window.push_quotes([                        # 指数与自选股一起刷新
        Quote("sh000001", "上证指数", 3210.45, 0.86),
        Quote("sh600519", "贵州茅台", 1688.00, 2.35),
    ])

运行演示：``python -m market_widget.demo``
"""

from .config import Quote, WatchItem
from .main_window import MarketWidget

__all__ = ["MarketWidget", "Quote", "WatchItem", "__version__"]

__version__ = "1.0.0"