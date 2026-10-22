import akshare as ak
import pandas as pd
import sqlite3
import os


DB_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data"
)
DB_PATH = os.path.join(DB_DIR, "stock.db")

conn = sqlite3.connect(DB_PATH)

# 资产负债表
# zcfzb = ak.stock_zcfz_em(date="20260331")
# print(zcfzb.columns)
# zcfzb.to_sql(
#     name="zcfzb",
#     con=conn,
#     if_exists="replace",
#     index=False
# )
# # 利润表
# lrb = ak.stock_lrb_em(date="20260331")
# print(lrb.columns)
# lrb.to_sql(
#     name="lrb",
#     con=conn,
#     if_exists="replace",
#     index=False
# )
# # 现金流量表
# xjllb = ak.stock_xjll_em(date="20260331")
# print(xjllb.columns)
# xjllb.to_sql(
#     name="xjllb",
#     con=conn,
#     if_exists="replace",
#     index=False
# )

stocks_df = ak.stock_info_a_code_name()
print(len(stocks_df))
stocks_df.to_sql(
    name="stock",
    con=conn,
    if_exists="replace",
    index=False
)
