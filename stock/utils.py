import html
import os
import pickle
import re
import threading
import time as _time  # 下面 from datetime import time 会占用 time 这个名字
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

# ---------------------------------------------------------------------------
# 雪球：个股文章与评论
# ---------------------------------------------------------------------------
# 雪球接口必须带登录态：没有 xq_a_token 会返回 400016，被 WAF 拦时还会甩回
# 一段 JS 挑战页；UA 也得是浏览器 UA，否则直接 567/400。token 会过期，
# 失效时抛 XueqiuTokenError，让调用方提示用户重新从浏览器导出。
XUEQIU_TOKEN_ENV = "XQ_TOKEN"
# 注意：裸域名 xueqiu.com 会走 WAF 挑战，带 www 的接口才正常返回 JSON
XUEQIU_API_BASE = "https://www.xueqiu.com"
XUEQIU_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
)
XUEQIU_ARTICLES = 10
XUEQIU_COMMENTS = 10
XUEQIU_TIMEOUT = 15
# 一次取 10 篇文章要发 11 个请求，不压节奏很容易吃"操作过于频繁"
XUEQIU_REQUEST_INTERVAL = 0.6
# 评论接口的配额很紧，被限频后等一会儿再试，别让整批取数直接失败
XUEQIU_RATE_LIMIT_WAIT = 20
XUEQIU_RATE_LIMIT_RETRIES = 3
# 接口把楼中楼回复混在列表里，一页 20 条要翻几页才够 10 条顶层评论
_COMMENT_PAGE_SIZE = 20
_COMMENT_MAX_PAGES = 1
# 雪球的限频错误码
_RATE_LIMIT_CODE = 110017

_throttle_lock = threading.Lock()
_last_request_at = 0.0


class XueqiuError(Exception):
    """雪球取数失败，消息可直接展示给用户。"""


class XueqiuTokenError(XueqiuError):
    """雪球 token 缺失或失效，需要重新从浏览器导出。"""


class XueqiuRateLimitError(XueqiuError):
    """请求太密被雪球限频，等一会儿再试。"""


def get_xueqiu_articles(
    code: str,
    article_count: int = XUEQIU_ARTICLES,
    comment_count: int = XUEQIU_COMMENTS,
    token: str | None = None,
) -> dict:
    """取个股前 N 篇文章，每篇附上前 M 条评论（默认各 10 条）。

    返回 ``{"symbol", "fetched_at", "count", "articles"}``。每篇文章含
    ``title`` / ``author`` / ``created_at`` / ``url`` / ``text``（原始 HTML）/
    ``content``（纯文本）/ 阅读、评论、收藏、点赞数，以及 ``comments`` 列表。
    评论按雪球默认顺序（最新在前），顶层优先，不足时用楼中楼回复补足。
    """
    symbol = get_xueqiu_symbol(code)
    articles = fetch_xueqiu_articles(symbol, count=article_count, token=token)
    for item in articles:
        item["comments"] = fetch_xueqiu_comments(item["id"], count=comment_count, token=token)
    return {
        "symbol": symbol,
        "fetched_at": datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(articles),
        "articles": articles,
    }


def fetch_xueqiu_articles(
    code: str,
    count: int = XUEQIU_ARTICLES,
    token: str | None = None,
) -> list[dict]:
    """抓个股讨论列表（雪球默认排序），返回解析后的文章。"""
    symbol = get_xueqiu_symbol(code)
    payload = _xueqiu_get(
        "/query/v1/symbol/search/status.json",
        {
            "count": count,
            "comment": 0,
            "symbol": symbol,
            "hl": 0,
            "source": "all",
            "sort": "alpha",
            "page": 1,
            "q": "",
            "type": 11,
        },
        token,
    )

    articles: list[dict] = []
    for raw in payload.get("list") or []:
        item = _parse_xueqiu_article(raw, symbol)
        if item is not None:
            articles.append(item)
        if len(articles) >= count:
            break
    return articles


def fetch_xueqiu_comments(
    status_id: int | str,
    count: int = XUEQIU_COMMENTS,
    token: str | None = None,
    include_replies: bool = False,
) -> list[dict]:
    """取一篇雪球帖子的前 ``count`` 条评论。

    默认优先顶层评论，不够 ``count`` 条时再用楼中楼回复补足——有些帖子
    （自己就是回复的帖子）整页都是回复，全过滤掉会一条不剩。每条评论都带
    ``reply_to_comment_id`` / ``reply_to_author``，调用方可以自己区分。
    一页不够就继续翻页，直到凑够或翻完。
    """
    status_id = _to_int(status_id)
    if not status_id:
        raise XueqiuError("雪球评论需要文章 id")

    ordered: list[dict] = []
    top_level: list[dict] = []
    replies: list[dict] = []
    page = 1
    while page <= _COMMENT_MAX_PAGES:
        payload = _xueqiu_get(
            "/statuses/comments.json",
            {
                "id": status_id,
                "count": _COMMENT_PAGE_SIZE,
                "page": page,
                "reply": "true",
                "asc": "false",
                "type": "status",
            },
            token,
        )
        raw_list = payload.get("comments") or []
        for raw in raw_list:
            item = _parse_xueqiu_comment(raw)
            if item is None:
                continue
            ordered.append(item)
            (replies if item["reply_to_comment_id"] else top_level).append(item)

        # include_replies 时要的是列表前 N 条，否则只关心顶层够不够
        got = len(ordered) if include_replies else len(top_level)
        if got >= count:
            break
        max_page = _to_int(payload.get("maxPage")) or 1
        if not raw_list or page >= max_page:
            break
        page += 1

    if include_replies:
        return ordered[:count]
    return (top_level + replies)[:count]


def get_xueqiu_symbol(code: str) -> str:
    """``605499`` / ``sh605499`` -> ``SH605499``；判断不了交易所时报错。"""
    prefix, digits = get_stock_prefix(code)
    if prefix not in ("SH", "SZ", "BJ") or not digits:
        raise XueqiuError(f"{code}：无法判断交易所，雪球需要 SH/SZ/BJ 前缀")
    return f"{prefix}{digits}"


# ---------------------------------------------------------------------------
# 雪球：取数与解析
# ---------------------------------------------------------------------------
def _xueqiu_get(
    path: str,
    params: dict,
    token: str | None = None,
    retries: int | None = None,
) -> dict:
    """请求雪球接口；被限频时按 ``XUEQIU_RATE_LIMIT_WAIT`` 退避重试。"""
    retries = XUEQIU_RATE_LIMIT_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            return _xueqiu_get_once(path, params, token)
        except XueqiuRateLimitError:
            if attempt >= retries:
                raise
            _time.sleep(XUEQIU_RATE_LIMIT_WAIT * (attempt + 1))


def _xueqiu_get_once(path: str, params: dict, token: str | None = None) -> dict:
    """发一次雪球接口请求，把各种失败翻译成可读的异常。"""
    # 先取 token：缺 token 是配置问题，不要被下面的网络异常包成"请求失败"
    cookies = {"xq_a_token": _xueqiu_token(token)}
    headers = {
        "User-Agent": XUEQIU_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{XUEQIU_API_BASE}/",
        "X-Requested-With": "XMLHttpRequest",
    }
    _xueqiu_throttle()
    try:
        response = requests.get(
            XUEQIU_API_BASE + path,
            params=params,
            cookies=cookies,
            headers=headers,
            timeout=XUEQIU_TIMEOUT,
        )
    except XueqiuError:
        raise
    except Exception as error:
        raise XueqiuError(f"雪球请求失败：{error}") from error

    try:
        payload = response.json()
    except ValueError as error:
        # token 失效时接口不一定回 400016，也可能直接甩回 WAF 的挑战页
        if "renderData" in response.text or "_waf_" in response.text:
            raise XueqiuTokenError(
                f"雪球拦截了请求，token 可能已失效：请重新导出 xq_a_token 并更新 {XUEQIU_TOKEN_ENV}"
            ) from error
        raise XueqiuError(
            f"雪球接口返回 HTTP {response.status_code}，且不是 JSON"
        ) from error

    if not isinstance(payload, dict):
        raise XueqiuError(f"雪球接口返回 HTTP {response.status_code}，数据格式不认识")

    # 限频、参数错误时雪球用 HTTP 400 返回 JSON，把具体原因带出来
    if response.status_code != 200:
        detail = payload.get("error_description") or payload.get("error_code")
        if str(payload.get("error_code")) == str(_RATE_LIMIT_CODE):
            raise XueqiuRateLimitError(f"雪球限频：{detail}")
        raise XueqiuError(f"雪球接口返回 HTTP {response.status_code}：{detail}")

    if payload.get("code") == 400016:
        raise XueqiuTokenError(
            f"雪球 token 已失效（code 400016），请重新从浏览器导出并更新 {XUEQIU_TOKEN_ENV}"
        )
    if payload.get("error_code"):
        detail = payload.get("error_description") or payload.get("error_code")
        if str(payload.get("error_code")) == str(_RATE_LIMIT_CODE):
            raise XueqiuRateLimitError(f"雪球限频：{detail}")
        raise XueqiuError(f"雪球接口报错：{detail}")
    return payload


def _xueqiu_throttle() -> None:
    """全局节流：两次雪球请求之间至少隔 ``XUEQIU_REQUEST_INTERVAL`` 秒。"""
    global _last_request_at
    with _throttle_lock:
        wait = XUEQIU_REQUEST_INTERVAL - (_time.monotonic() - _last_request_at)
        if wait > 0:
            _time.sleep(wait)
        _last_request_at = _time.monotonic()


def _xueqiu_token(token: str | None = None) -> str:
    """取雪球 token：优先参数，其次环境变量；允许直接粘贴整段 cookie。"""
    raw = (token or os.environ.get(XUEQIU_TOKEN_ENV) or "").strip()
    if not raw:
        raise XueqiuTokenError(
            f"缺少雪球 token：请把浏览器 cookie 里的 xq_a_token 放进环境变量 {XUEQIU_TOKEN_ENV}"
        )

    if "xq_a_token=" in raw:
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "xq_a_token" and value:
                return value
        raise XueqiuTokenError(f"{XUEQIU_TOKEN_ENV} 里没有 xq_a_token")
    return raw


def _parse_xueqiu_article(raw: dict, symbol: str) -> dict | None:
    status_id = _to_int(raw.get("id"))
    if not status_id:
        return None

    text = str(raw.get("text") or raw.get("description") or "")
    content = _html_to_text(text)
    user = raw.get("user") or {}
    target = _clean_text(raw.get("target"))
    if target and target.startswith("/"):
        url = f"https://xueqiu.com{target}"
    else:
        url = target or f"https://xueqiu.com/S/{symbol}/{status_id}"

    return {
        "id": status_id,
        "symbol": symbol,
        # 短帖没有标题，用正文开头兜底
        "title": _clean_text(raw.get("title")) or _xq_clip(content.replace("\n", " "), 30),
        "author": _clean_text(user.get("screen_name")),
        "author_id": _to_int(user.get("id")),
        "created_at": _timestamp_text(raw.get("created_at")),
        "url": url,
        "text": text,
        "content": content,
        "view_count": _to_int(raw.get("view_count")) or 0,
        "reply_count": _to_int(raw.get("reply_count")) or 0,
        "fav_count": _to_int(raw.get("fav_count")) or 0,
        "like_count": _to_int(raw.get("like_count")) or 0,
    }


def _parse_xueqiu_comment(raw: dict) -> dict | None:
    comment_id = _to_int(raw.get("id"))
    if not comment_id:
        return None

    user = raw.get("user") or {}
    return {
        "id": comment_id,
        "author": _clean_text(user.get("screen_name")),
        "author_id": _to_int(raw.get("user_id")) or _to_int(user.get("id")),
        "content": _html_to_text(str(raw.get("description") or raw.get("text") or "")),
        "created_at": _timestamp_text(raw.get("created_at")),
        "like_count": _to_int(raw.get("like_count")) or 0,
        "reply_count": _to_int(raw.get("reply_count")) or 0,
        "ip_location": _clean_text(raw.get("ip_location")),
        # 楼中楼回复会指向被回复的评论，顶层评论为 None
        "reply_to_comment_id": _to_int(raw.get("in_reply_to_comment_id")),
        "reply_to_author": _clean_text(raw.get("reply_screenName")),
    }


# ---------------------------------------------------------------------------
# 雪球：小工具
# ---------------------------------------------------------------------------
_BREAK_TAG = re.compile(r"</(?:p|div|h[1-6]|li|tr|blockquote)>|<br\s*/?>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")


def _html_to_text(value: str) -> str:
    """雪球正文是 HTML（带 ``<a>`` 股票标签），转成纯文本便于阅读和检索。"""
    if not value:
        return ""
    text = _BREAK_TAG.sub("\n", value)
    text = _ANY_TAG.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _timestamp_text(value) -> str | None:
    """雪球时间戳是毫秒，统一转成北京时间字符串。"""
    stamp = _to_int(value)
    if not stamp:
        return None
    return datetime.fromtimestamp(stamp / 1000, CHINA_TZ).strftime("%Y-%m-%d %H:%M")


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _xq_clip(text: str, max_width: int) -> str:
    """按显示宽度截断（中文算两个字符宽）。"""
    width = 0
    for index, char in enumerate(text):
        width += 2 if ord(char) > 0x2E80 else 1
        if width > max_width:
            return text[:index].rstrip("，、；： ") + "…"
    return text


if __name__ == "__main__":
    # print("今天是否为交易日：", is_trading_day())
    # print("2026-10-01 是否为交易日：", is_trading_day(date(2026, 10, 1)))
    # print(is_trading_time())
    #
    # print(is_trading_time(
    #     datetime(2026, 10, 22, 10, 30)
    # ))
    #
    # print(is_trading_time(
    #     datetime(2026, 10, 22, 12, 0)
    # ))
    # print(get_sina_current_stock_info_list(["sh000001"]))

    from assistant.env import load_env

    load_env()   # 从项目根的 .env 取 XQ_TOKEN
    for article in get_xueqiu_articles("605499")["articles"]:
        print(f"{article['title']} · {len(article['comments'])} 条评论")

