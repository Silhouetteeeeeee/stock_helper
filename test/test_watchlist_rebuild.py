"""自选股列表重建时是否保留已有行情。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from common.stock import SinaStockQuote
from market_widget.config import WatchItem
from market_widget.ui.watchlist import WatchListPanel

A = WatchItem("600036", "招商银行")
B = WatchItem("601318", "中国平安")
C = WatchItem("600519", "贵州茅台")


def quote(code: str, price: float, pre_close: float) -> SinaStockQuote:
    return SinaStockQuote(code=code, name=code, price=price, pre_close=pre_close)


def texts(panel: WatchListPanel, code: str) -> tuple[str, str]:
    _, price, change = panel._rows[code]
    return price.text(), change.text()


def main() -> None:
    app = QApplication.instance() or QApplication([])

    panel = WatchListPanel([A, B])
    panel.update_quotes([quote(A.code, 40.0, 39.0), quote(B.code, 50.0, 51.0)])
    assert texts(panel, A.code) == ("40.00", "+2.56%"), texts(panel, A.code)
    assert texts(panel, B.code) == ("50.00", "-1.96%"), texts(panel, B.code)

    # 新增一只股票后重建列表，原有两只的行情必须保留
    panel.set_items([A, B, C])
    assert texts(panel, A.code) == ("40.00", "+2.56%"), texts(panel, A.code)
    assert texts(panel, B.code) == ("50.00", "-1.96%"), texts(panel, B.code)
    assert texts(panel, C.code) == ("--", "--"), texts(panel, C.code)

    # 删除一只股票后重建列表，剩余股票的行情必须保留
    panel.set_items([B, C])
    assert texts(panel, B.code) == ("50.00", "-1.96%"), texts(panel, B.code)

    # reset 后缓存也要清空，下次重建不能把旧行情带回来
    panel.reset()
    panel.set_items([A, B, C])
    assert texts(panel, A.code) == ("--", "--"), texts(panel, A.code)
    assert texts(panel, B.code) == ("--", "--"), texts(panel, B.code)

    app.quit()
    print("PASS")


if __name__ == "__main__":
    main()
