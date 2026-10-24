import pickle
import traceback
import requests
import akshare as ak

from common.stock import *
from . import db_service as db
from functools import lru_cache
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import pandas as pd


CHINA_TZ = ZoneInfo("Asia/Shanghai")

def get_stock_minute_price(code: str) -> tuple[pd.DataFrame, float]:
    """
    获取股票/指数最近交易日分时交易数据及前一交易日收盘价。

    :param code: 带交易所前缀的代码，例如 "sh605499"、"sh000001"
    :return:
        (df_last, pre_close)
        df_last 包含以下列：
        * day: 时间
        * open: 开盘价
        * high: 最高价
        * low: 最低价
        * close: 收盘价
        * volume: 成交量
        * amount: 成交额
    """
    df = ak.stock_zh_a_minute(symbol=code, period="1")
    if df.empty or "day" not in df.columns:
        return df, 0.0

    df["day"] = pd.to_datetime(df["day"])
    dates = df["day"].dt.date
    last_day = dates.max()
    df_last = df[dates == last_day].reset_index(drop=True)

    # 用上一交易日最后一根分钟线的收盘价作为昨收；
    # 数据只包含当天时退回当天开盘价。
    previous = df[dates < last_day]
    if not previous.empty and "close" in previous.columns:
        pre_close = float(previous["close"].iloc[-1])
    elif not df_last.empty and "open" in df_last.columns:
        pre_close = float(df_last["open"].iloc[0])
    else:
        pre_close = 0.0

    return df_last, pre_close

def get_stock_prefix(code: str) -> tuple[str, str]:
    """
    根据股票代码判断交易所前缀，并提取纯数字代码。

    参数:
        code: 股票代码，例如 "605499"、"SH605499"、"605499.SH"

    返回:
        (prefix, code)，例如 ("SH", "605499")
        无法识别时返回 ("UNKNOWN", 提取出的数字)
    """
    code = str(code).strip().upper()

    # 处理带前缀的形式，例如 SH605499
    if code.startswith(("SH", "SZ", "BJ")):
        prefix = code[:2]
        digits = "".join(ch for ch in code[2:] if ch.isdigit())
        return prefix, digits

    # 处理带后缀的形式，例如 605499.SH
    if "." in code:
        parts = code.split(".")
        if parts[-1] in ("SH", "SZ", "BJ"):
            prefix = parts[-1]
            digits = "".join(ch for ch in parts[0] if ch.isdigit())
            return prefix, digits

    # 纯数字或其他形式
    digits = "".join(ch for ch in code if ch.isdigit())

    if not digits:
        return "UNKNOWN", ""

    if digits.startswith(("600", "601", "603", "605", "688")):
        return "SH", digits

    if digits.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZ", digits

    if digits.startswith(("920", "4", "8")):
        return "BJ", digits

    return "UNKNOWN", digits

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

def get_sina_current_stock_info_list(codes: list[str], ) -> list[SinaStockQuote]:
    """
    通过新浪获取股票实时行情
    codes: ["sh605499", "sz000001"]
    """
    def parse_single_line(line: str) -> SinaStockQuote | None:
        """解析单一条 hq_str 内容"""
        try:
            s = line.split('="')[1].rstrip('";\n')
            arr = s.split(",")
            bid = [
                (float(arr[6]), float(arr[10]) / 100),
                (float(arr[11]), float(arr[12]) / 100),
                (float(arr[13]), float(arr[14]) / 100),
                (float(arr[15]), float(arr[16]) / 100),
                (float(arr[17]), float(arr[18]) / 100),
            ]
            ask = [
                (float(arr[7]), float(arr[19]) / 100),
                (float(arr[21]), float(arr[22]) / 100),
                (float(arr[23]), float(arr[24]) / 100),
                (float(arr[25]), float(arr[26]) / 100),
                (float(arr[27]), float(arr[28]) / 100),
            ]
            stock = db.get_stock_by_name(arr[0])

            return SinaStockQuote(
                code=stock.code if stock is not None else None,
                name=arr[0],
                open=float(arr[1]),
                pre_close=float(arr[2]),
                price=float(arr[3]),
                high=float(arr[4]),
                low=float(arr[5]),
                bid=bid,
                ask=ask,
                volume_hand=float(arr[8]) / 100,
                amount=float(arr[9]),
                date=arr[30],
                time=arr[31],
            )
        except Exception as e:
            print(f"单行解析失败: {e}, line={line[:80]}")
            return None
    _codes = []
    for code in codes:
        _prefix, _code = get_stock_prefix(code)
        _codes.append((_prefix + _code).lower())
    headers = {"Referer": "https://finance.sina.com.cn/"}
    url = f"http://hq.sinajs.cn/list={','.join(_codes)}"
    try:
        resp = requests.get(url, headers=headers, proxies={"http": None, "https": None}, timeout=6)
        resp.encoding = "gbk"
        # 按分号切分成一条条记录，过滤空行
        raw_lines = [item.strip() for item in resp.text.split(";") if item.strip()]
        res = []
        for line in raw_lines:
            item = parse_single_line(line)
            if item:
                res.append(item)
        return res
    except Exception as e:
        print(f"批量请求失败: {e}")
        return []

@lru_cache(maxsize=1)
def get_trade_dates() -> frozenset[date]:
    """获取并缓存 A 股交易日历。"""
    df = ak.tool_trade_date_hist_sina()
    return frozenset(
        date.fromisoformat(str(d)[:10])
        for d in df["trade_date"]
    )


def is_trading_day(check_date: date | None = None) -> bool:
    """
    判断指定日期是否为 A 股交易日。
    不传参数时，默认判断今天。
    """
    if check_date is None:
        check_date = date.today()

    return check_date in get_trade_dates()

def is_trading_time(now: datetime | None = None) -> bool:
    """
    判断当前是否处于 A 股交易时段。

    交易时段：
    上午：09:30 <= time <= 11:30
    下午：13:00 <= time <= 15:00

    默认使用北京时间。
    """
    if now is None:
        now = datetime.now(CHINA_TZ)
    elif now.tzinfo is None:
        # 传入无时区时间时，按北京时间处理
        now = now.replace(tzinfo=CHINA_TZ)
    else:
        now = now.astimezone(CHINA_TZ)

    # 先判断是否为交易日
    if not is_trading_day(now.date()):
        return False

    current_time = now.time().replace(tzinfo=None)

    morning = time(9, 30) <= current_time <= time(11, 30)
    afternoon = time(13, 0) <= current_time <= time(15, 0)

    return morning or afternoon

if __name__ == "__main__":
    print("今天是否为交易日：", is_trading_day())
    print("2026-10-01 是否为交易日：", is_trading_day(date(2026, 10, 1)))
    print(is_trading_time())

    print(is_trading_time(
        datetime(2026, 10, 22, 10, 30)
    ))

    print(is_trading_time(
        datetime(2026, 10, 22, 12, 0)
    ))
    print(get_sina_current_stock_info_list(["sh000001"]))