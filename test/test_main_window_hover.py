"""悬停模式切换验证：展开后收起 / 直接锁定迷你，窗口必须回到固定高度。

回归背景：基本面面板里有自动换行的标签，body 因此变成 height-for-width。
收起时隐藏 body 留下的布局请求若排在 setFixedHeight 之后处理，会按展开
时的高度把窗口重新撑开，迷你态失效。

这个现象只在真正映射到屏幕的窗口上出现，所以这里不用 offscreen 平台，
也不模拟鼠标悬停（直接改 ``_hover_expanded`` 走同一段显隐逻辑），
窗口会短暂闪现一下。结尾用 os._exit 跳过 Qt 的析构。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

import market_widget.main_window as mw
from market_widget.config import MINIMIZED_HEIGHT, MINI_MODE_ROW_HEIGHT

COLLAPSED_HEIGHT = MINIMIZED_HEIGHT + MINI_MODE_ROW_HEIGHT


def settle(app: QApplication, window) -> int:
    """处理完挂起的布局请求后再读数。"""
    for _ in range(3):
        app.processEvents()
    return window.height()


def expand(window) -> None:
    window._hover_expanded = True
    window._apply_mode_visibilities()


def collapse(window) -> None:
    window._hover_expanded = False
    window._apply_mode_visibilities()


def main() -> None:
    app = QApplication.instance() or QApplication([])

    mw.FINANCE_MONITOR = False  # 不需要财报调度，避免后台线程影响时序

    window = mw.MarketWidget()
    window.show()
    app.processEvents()

    # 初始迷你态
    assert settle(app, window) == COLLAPSED_HEIGHT, window.height()

    # 展开 → 收起（鼠标移出）
    expand(window)
    assert window.height() > COLLAPSED_HEIGHT, window.height()
    assert not window._body.isHidden() and window._mini_row.isHidden()

    collapse(window)
    assert settle(app, window) == COLLAPSED_HEIGHT, window.height()
    assert window._body.isHidden() and not window._mini_row.isHidden()

    # 展开 → 直接锁定迷你（点标题栏 "—"）
    expand(window)
    assert window.height() > COLLAPSED_HEIGHT, window.height()

    window.toggle_minimized()
    assert window._locked_minimized is True
    assert settle(app, window) == MINIMIZED_HEIGHT, window.height()

    # 解锁后回到默认迷你态
    window.toggle_minimized()
    assert window._locked_minimized is False
    assert settle(app, window) == COLLAPSED_HEIGHT, window.height()

    # 反复切换不应漂移
    for _ in range(3):
        expand(window)
        collapse(window)
        assert settle(app, window) == COLLAPSED_HEIGHT, window.height()

    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
