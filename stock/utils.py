import pickle
import traceback

from akshare import stock_zh_index_spot_sina
from akshare.stock.cons import xq_a_token

from stock import Stock
import akshare as ak

def get_stock_prefix(code: str) -> str:
    """
    根据股票代码判断交易所前缀

    参数:
        code: 股票代码，可以是纯数字，也可以是带前缀/后缀的形式
              例如 "605499"、"SH605499"、"605499.SH"

    返回:
        "SH" / "SZ" / "BJ" / "UNKNOWN"
    """
    # 统一转成大写并去掉空格
    code = str(code).strip().upper()

    # 如果已经带了前缀，直接返回前缀部分
    if code.startswith(("SH", "SZ", "BJ")):
        return code[:2]

    # 如果带了后缀，例如 "605499.SH"
    if "." in code:
        suffix = code.split(".")[-1]
        if suffix in ("SH", "SZ", "BJ"):
            return suffix

    # 提取纯数字部分
    digits = "".join(ch for ch in code if ch.isdigit())

    if not digits:
        return "UNKNOWN"

    # 按开头数字判断
    # 沪市：600/601/603/605 主板，688 科创板
    if digits.startswith(("600", "601", "603", "605", "688")):
        return "SH"

    # 深市：000/001/002/003 主板，300/301 创业板
    if digits.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZ"

    # 北交所：920 新代码，以及 4/8 开头的老代码
    if digits.startswith(("920", "4", "8")):
        return "BJ"

    return "UNKNOWN"

def get_roe3(s: Stock):
    try:
        df = ak.stock_zh_dupont_comparison_em(symbol=s.prefix + s.code)
        return df[df['代码'] == s.code].iloc[0]['ROE-3年平均']
    except IndexError as e:
        print(f"{s.prefix + s.code}对应数据不存在")
    except Exception as e:
        traceback.print_exc()
    return None

def save_cache_pickle(data, file_path):
    with open(file_path, "wb") as f:
        pickle.dump(data, f)

def load_cache_pickle(file_path) -> list[Stock]:
    try:
        with open(file_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None

def get_index_info(codes: list[str]):
    stock_zh_index_spot_sina_df = ak.stock_zh_index_spot_sina()
    if codes is not None and len(codes) > 0:
        return stock_zh_index_spot_sina_df[stock_zh_index_spot_sina_df["代码"].isin(codes)]
    return stock_zh_index_spot_sina_df

print(get_index_info(["sh000001", "sh000300", "sh000688", "sz399006", "sh000905", "sh000852"]))