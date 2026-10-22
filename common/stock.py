from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from dataclasses import dataclass
from abc import ABC


@dataclass
class Stock:
    code: str
    name: str
    prefix: str = None
    # 三年ROE3
    roe3: Optional[np.float64] = None
    zcfzl: Optional[np.float64] = None
    ldbl: Optional[np.float64] = None
    sdbl: Optional[np.float64] = None
    xjl_jlr_ratio: Optional[np.float64] = None



@dataclass(kw_only=True)
class Quote:
    """通用行情基类。

    change_pct：涨跌幅百分比，例如 1.23 表示上涨 1.23%。
    优先使用行情源提供的涨跌幅；没有提供时，根据价格计算。
    """

    code: str
    name: str
    price: float | None = None
    pre_close: float | None = None

    # 保存行情源提供的涨跌额和涨跌幅
    _change: float | None = field(default=None, repr=False)
    _change_pct: float | None = field(default=None, repr=False)

    @property
    def change(self) -> float | None:
        """涨跌额：优先使用行情源的值，否则自行计算。"""
        if self._change is not None:
            return self._change

        if self.price is None or self.pre_close is None:
            return None

        return round(self.price - self.pre_close, 2)

    @change.setter
    def change(self, value: float | None) -> None:
        self._change = value

    @property
    def change_pct(self) -> float | None:
        """涨跌幅：优先使用行情源的值，否则自行计算。"""
        if self._change_pct is not None:
            return self._change_pct

        if self.price is None or self.pre_close in (None, 0):
            return None

        return round(
            (self.price - self.pre_close) / self.pre_close * 100,
            2,
        )

    @change_pct.setter
    def change_pct(self, value: float | None) -> None:
        self._change_pct = value

    @property
    def direction(self) -> str:
        """涨跌方向：up、down 或 flat。"""
        pct = self.change_pct

        if pct is None or pct == 0:
            return "flat"

        return "up" if pct > 0 else "down"


@dataclass(kw_only=True)
class SinaStockQuote(Quote):
    """新浪个股行情。"""

    open: float | None = None
    high: float | None = None
    low: float | None = None

    # 每档盘口：(价格, 数量)
    bid: list[tuple[float, float]] = field(default_factory=list)
    ask: list[tuple[float, float]] = field(default_factory=list)

    volume_hand: float | None = None
    amount: float | None = None
    date: str | None = None
    time: str | None = None


@dataclass(kw_only=True)
class IndexQuote(Quote):
    """指数行情。"""

    pass


if __name__ == "__main__":
    # 情况一：指数接口直接提供涨跌幅
    index = IndexQuote(
        code="sh000001",
        name="上证指数",
        price=3200.0,
        pre_close=3210.0,
    )
    index.change_pct = -0.31

    print(index.change_pct)  # -0.31，优先使用数据源值
    print(index.direction)   # down

    # 情况二：个股没有提供涨跌幅，自动计算
    stock = SinaStockQuote(
        code="605499",
        name="东鹏饮料",
        price=100.0,
        pre_close=102.0,
    )

    print(stock.change_pct)  # -1.96
    print(stock.change)      # -2.0

    # 情况三：先计算，后赋值覆盖
    stock.change_pct = -1.95
    print(stock.change_pct)  # -1.95，使用显式赋值的值

    # 情况四：清除数据源值，恢复自动计算
    stock.change_pct = None
    print(stock.change_pct)  # -1.96
