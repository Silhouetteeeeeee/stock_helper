import numpy as np
import pandas
from stock.utils import *
from concurrent.futures import ThreadPoolExecutor, as_completed


stocks = load_cache_pickle("filtered_stocks.pkl")
stocks = [s for s in stocks if s.roe3 is not None and s.roe3 > 12]
good_stocks = []
print(len(stocks))

def fetch_one_stock(s: Stock):
    fa = ak.stock_financial_analysis_indicator(symbol=s.code, start_year="2023")

    s.zcfzl = fa.iloc[-1]["资产负债率(%)"]
    s.ldbl = fa.iloc[-1]["流动比率"]
    s.sdbl = fa.iloc[-1]["速动比率"]
    s.xjl_jlr_ratio = fa.iloc[-1]["经营现金净流量与净利润的比率(%)"]

    # 先判断是否存在缺失
    if (s.zcfzl is None
            or s.ldbl is None
            or s.sdbl is None
            or s.xjl_jlr_ratio is None):
        return

    if s.zcfzl > 60 or s.ldbl < 1.2 or s.sdbl < 0.8 or s.xjl_jlr_ratio < 0.7:
        return

    good_stocks.append(s)

with ThreadPoolExecutor(max_workers=8) as executor:
    # 提交任务
    futures = [executor.submit(fetch_one_stock, s) for s in stocks]
    # 遍历完成的任务
    for future in as_completed(futures):
        continue

save_cache_pickle(good_stocks, "filtered_stocks.pkl")
print(load_cache_pickle("filtered_stocks.pkl"))