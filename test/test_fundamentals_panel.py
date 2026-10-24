"""基本面面板验证：标题/指标/提示渲染、提示截断、转义与占位状态。

只构造面板本身，不构造整个悬浮窗——窗口的事件循环在 offscreen 平台下
退出时可能崩溃，和面板要验证的内容无关。
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from market_widget.theme import PALETTE
from market_widget.ui.fundamentals import (
    ALERT_MAX_WIDTH,
    MAX_ALERTS,
    FundamentalsPanel,
    _shorten,
)

BRIEFING = {
    "available": True,
    "headline": "财报 2026-06-30 · 估值 2026-10-23",
    "lines": [
        "营收 124.4亿 +15.9% · 净利 28.7亿 +20.7%",
        '毛利率 48.4% <b>不该加粗</b>',
    ],
    "alerts": [
        {"level": "warning", "text": "单季营收同比增速连续 5 个季度放缓，毛利率也在同步下滑"},
        {"level": "info", "text": "净现金状态"},
        {"level": "info", "text": "第三条提示不该出现"},
    ],
}


def main() -> None:
    app = QApplication.instance() or QApplication([])
    panel = FundamentalsPanel()

    # -- 加载中与占位 -------------------------------------------------------
    panel.show_loading()
    assert "加载中" in panel.text(), panel.text()
    assert PALETTE.text_muted in panel.text(), panel.text()

    panel.show_message("指数没有财报数据")
    assert "指数没有财报数据" in panel.text(), panel.text()

    panel.show_briefing({"available": False, "summary": "基本面数据获取失败"})
    assert "基本面数据获取失败" in panel.text(), panel.text()

    # -- 正常速览 -----------------------------------------------------------
    panel.show_briefing(BRIEFING)
    body = panel.text()
    assert BRIEFING["headline"] in body, body
    assert "营收 124.4亿 +15.9%" in body, body
    assert "PE" not in body  # 这里只断言面板不自己造数据
    assert PALETTE.text_muted in body and PALETTE.text in body, body

    # 尖括号必须转义，不能变成富文本标签
    assert "不该加粗" in body and "<b>" not in body, body
    assert "&lt;b&gt;" in body, body

    # 提示最多两条，级别决定颜色与标签
    assert body.count("关注：") == 1, body
    assert "异常：" in body, body
    assert PALETTE.avg in body, body
    assert "第三条提示不该出现" not in body, body
    assert len(BRIEFING["alerts"]) > MAX_ALERTS

    # 过长的提示被截断，完整内容仍在 briefing 的纯文本里
    assert "…" in body, body
    assert "毛利率也在同步下滑" not in body, body

    # -- 悬停提示：完整内容，不截断也不限条数 ---------------------------------
    tooltip = panel.toolTip()
    assert tooltip == panel._body.toolTip(), "面板和标签都要挂提示"
    assert BRIEFING["headline"] in tooltip, tooltip
    assert "营收 124.4亿 +15.9%" in tooltip, tooltip
    for alert in BRIEFING["alerts"]:
        assert alert["text"] in tooltip, tooltip
    assert "异常：" in tooltip and "关注：" in tooltip, tooltip

    # 带有 summary 的 briefing 直接用官方纯文本
    with_summary = dict(BRIEFING, summary="东鹏饮料(605499) · 财报 2026-06-30\n- [关注] 完整提示")
    panel.show_briefing(with_summary)
    assert panel.toolTip() == with_summary["summary"], panel.toolTip()

    # 加载/占位状态不残留上一次的提示
    panel.show_loading()
    assert panel.toolTip() == "", panel.toolTip()
    panel.show_message("指数没有财报数据")
    assert panel.toolTip() == "", panel.toolTip()
    panel.show_briefing({"available": False, "summary": "基本面数据获取失败"})
    assert panel.toolTip() == "", panel.toolTip()

    # -- _shorten 按显示宽度截断（汉字算 2） --------------------------------
    assert _shorten("短句", 34) == "短句"
    assert _shorten("一二三四五六七八九十", 10) == "一二三四五…"
    assert _shorten("abcdef", 10) == "abcdef"
    # 截断后去掉悬空的标点
    assert _shorten("一二三四，五六七", 8) == "一二三四…"
    assert _shorten("一二，三四", 6) == "一二…"
    assert len(_shorten("长" * 100, ALERT_MAX_WIDTH)) <= ALERT_MAX_WIDTH // 2 + 1

    # -- 空内容 -------------------------------------------------------------
    panel.show_briefing({"available": True, "headline": "", "lines": [], "alerts": []})
    assert panel.text() == "", panel.text()

    app.quit()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
