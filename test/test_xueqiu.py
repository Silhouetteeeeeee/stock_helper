"""雪球文章与评论抓取验证：token 处理、HTML 清洗、分页与失败分支。

不联网：把 ``stock.utils.requests.get`` 换成一个按 URL 返回固定 JSON 的假函数。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.utils as su

STATUS_JSON = "/query/v1/symbol/search/status.json"
COMMENTS_JSON = "/statuses/comments.json"


def article(status_id: int, title: str = "", text: str = "", user: str = "知知知") -> dict:
    return {
        "id": status_id,
        "title": title,
        "text": text,
        "description": text,
        "created_at": 1792666961000,
        "user": {"id": 5056262718, "screen_name": user},
        "view_count": 38285,
        "reply_count": 75,
        "fav_count": 20,
        "like_count": 3,
        "target": f"/5056262718/{status_id}",
    }


def comment(comment_id: int, description: str = "说得有道理", parent: int | None = None) -> dict:
    return {
        "id": comment_id,
        "user_id": 3322127011,
        "user": {"id": 3322127011, "screen_name": "雪球用户"},
        "description": description,
        "created_at": 1792845956000,
        "like_count": 2,
        "reply_count": 1,
        "ip_location": "广东",
        "in_reply_to_comment_id": parent,
    }


class FakeResponse:
    def __init__(self, payload=None, text: str = "", status_code: int = 200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def use_fake(handler):
    """替换 ``requests.get``，返回恢复用的原函数。"""
    original = su.requests.get
    su.requests.get = handler
    return original


def restore(original) -> None:
    su.requests.get = original


def test_token() -> None:
    os.environ.pop(su.XUEQIU_TOKEN_ENV, None)
    try:
        su.get_xueqiu_articles("605499", token=None)
    except su.XueqiuTokenError as error:
        assert "XQ_TOKEN" in str(error), error
    else:
        raise AssertionError("缺少 token 时应当报错")

    # 允许直接粘贴整段 cookie，从中取出 xq_a_token
    seen = {}

    def handler(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse({"count": 0, "list": []})

    original = use_fake(handler)
    try:
        os.environ[su.XUEQIU_TOKEN_ENV] = "u=1; xq_a_token=from-cookie; xqat=dup"
        su.get_xueqiu_articles("605499")
        assert seen["cookies"] == {"xq_a_token": "from-cookie"}, seen
        # 显式传入的 token 优先于环境变量
        su.get_xueqiu_articles("605499", token="direct")
        assert seen["cookies"] == {"xq_a_token": "direct"}, seen
    finally:
        os.environ.pop(su.XUEQIU_TOKEN_ENV, None)
        restore(original)


def test_symbol() -> None:
    assert su.get_xueqiu_symbol("605499") == "SH605499"
    assert su.get_xueqiu_symbol("sh605499") == "SH605499"
    assert su.get_xueqiu_symbol("000001") == "SZ000001"
    assert su.get_xueqiu_symbol("BJ430047") == "BJ430047"
    for bad in ("", "ABC"):
        try:
            su.get_xueqiu_symbol(bad)
        except su.XueqiuError:
            pass
        else:
            raise AssertionError(f"{bad!r} 应当报错")


def test_articles_parse() -> None:
    payload = {
        "count": 300,
        "list": [
            article(1, "关于东鹏饮料的估值", "<p>第一段</p><p>第二段&nbsp;</p>"),
            article(2, "", "没有标题的短帖 <a href='https://xueqiu.com/S/SH605499'>$东鹏饮料(SH605499)$</a>"),
        ],
    }
    original = use_fake(lambda url, **kwargs: FakeResponse(payload))
    try:
        articles = su.fetch_xueqiu_articles("605499")
    finally:
        restore(original)

    assert len(articles) == 2, articles
    first, second = articles
    assert first["id"] == 1 and first["title"] == "关于东鹏饮料的估值"
    assert first["author"] == "知知知"
    assert first["created_at"] == "2026-10-22 19:02", first["created_at"]
    assert first["url"] == "https://xueqiu.com/5056262718/1"
    assert first["view_count"] == 38285 and first["reply_count"] == 75
    # HTML 清洗：段落保留换行，标签和实体去掉
    assert first["text"].startswith("<p>"), first["text"]
    assert first["content"] == "第一段\n第二段", repr(first["content"])
    # 没有标题时用正文首段兜底
    assert second["title"].startswith("没有标题的短帖"), second["title"]
    assert "东鹏饮料" in second["content"] and "<a" not in second["content"]


def test_comments_tree() -> None:
    """只发一次请求；回复挂在父评论的 children 里，父评论不在时回到顶层。"""
    payload = {
        "count": 76, "maxPage": 2,
        "comments": [
            comment(1),
            comment(2, parent=1),
            comment(3, "第二条顶层"),
            comment(4, parent=1),
            comment(5, parent=3),
            comment(9, parent=8),   # 父评论不在本次返回里
        ],
    }
    calls = []

    def handler(url, **kwargs):
        calls.append(kwargs["params"])
        return FakeResponse(payload)

    original = use_fake(handler)
    try:
        comments = su.fetch_xueqiu_comments(413442879)
    finally:
        restore(original)

    assert len(calls) == 1 and calls[0]["page"] == 1, calls   # 不翻页
    assert calls[0]["count"] == su.XUEQIU_COMMENTS == 100

    assert [item.id for item in comments] == [1, 3, 9]
    assert [child.id for child in comments[0].children] == [2, 4]
    assert [child.id for child in comments[1].children] == [5]
    assert comments[2].children == []
    assert comments[2].reply_to_comment_id == 8   # 父评论没取到，信息仍保留

    # 类型和字段
    assert isinstance(comments[0], su.Comment) and not comments[0].is_reply
    assert all(isinstance(child, su.ChildComment) and child.is_reply for child in comments[0].children)
    assert comments[0].content == "说得有道理"
    assert comments[0].ip_location == "广东"
    assert comments[0].created_at == "2026-10-24 20:45", comments[0].created_at

    # 拍平：父在子前，且不丢人
    flat = su.flatten_comments(comments)
    assert [item.id for item in flat] == [1, 2, 4, 3, 5, 9]


def test_comments_reply_only() -> None:
    """整页都是楼中楼回复的帖子也要返回对象，而不是空列表。"""
    payload = {"count": 3, "maxPage": 1, "comments": [comment(7, parent=6), comment(8, parent=6)]}
    original = use_fake(lambda url, **kwargs: FakeResponse(payload))
    try:
        comments = su.fetch_xueqiu_comments(1)
    finally:
        restore(original)

    assert [item.id for item in comments] == [7, 8]
    assert all(isinstance(item, su.ChildComment) for item in comments)
    assert all(item.reply_to_comment_id == 6 for item in comments)


def test_articles_with_comments() -> None:
    """端到端：10 篇文章，每篇附前 10 条顶层评论。"""

    def handler(url, **kwargs):
        if STATUS_JSON in url:
            return FakeResponse({
                "count": 300,
                "list": [article(index, f"标题{index}", f"<p>正文{index}</p>") for index in range(1, 11)],
            })
        status_id = int(kwargs["params"]["id"])
        return FakeResponse({
            "count": 20, "maxPage": 1,
            "comments": [comment(10000 + status_id * 100 + index, f"评论{index}") for index in range(10)],
        })

    original = use_fake(handler)
    try:
        data = su.get_xueqiu_articles("605499")
    finally:
        restore(original)

    assert data["symbol"] == "SH605499"
    assert data["count"] == 10 and len(data["articles"]) == 10
    assert data["fetched_at"]
    for item in data["articles"]:
        comments = item["comments"]
        assert len(comments) == 10, item["id"]
        assert all(isinstance(comment, su.Comment) for comment in comments)
        assert comments[0].content == "评论0"


def test_failures() -> None:
    def token_error(url, **kwargs):
        return FakeResponse({"code": 400016, "message": "", "success": False})

    original = use_fake(token_error)
    try:
        su.fetch_xueqiu_articles("605499", token="stale")
    except su.XueqiuTokenError as error:
        assert "400016" in str(error), error
    else:
        raise AssertionError("400016 应当抛 token 失效")
    finally:
        restore(original)

    # token 失效时接口可能直接甩回阿里云 WAF 的挑战页
    def waf(url, **kwargs):
        return FakeResponse(None, text="<textarea id='renderData'>{\"_waf_x\":1}</textarea>")

    original = use_fake(waf)
    try:
        su.fetch_xueqiu_articles("605499", token="stale")
    except su.XueqiuTokenError as error:
        assert "xq_a_token" in str(error), error
    else:
        raise AssertionError("WAF 挑战页应当抛 token 失效")
    finally:
        restore(original)

    # 限频是单独一类错误，消息里要能看出该等一会儿
    def rate_limited(url, **kwargs):
        return FakeResponse({
            "error_code": "110017",
            "error_description": "操作过于频繁，请稍后再试",
            "error_data": None,
        })

    original = use_fake(rate_limited)
    try:
        su.fetch_xueqiu_comments(1, token="t")
    except su.XueqiuRateLimitError as error:
        assert "操作过于频繁" in str(error), error
    else:
        raise AssertionError("110017 应当抛限频")
    finally:
        restore(original)

    # 限频时接口也可能回 HTTP 400 + JSON，同样是限频错误
    def rate_limited_400(url, **kwargs):
        return FakeResponse({
            "error_code": "110017",
            "error_description": "操作过于频繁，请稍后再试",
        }, status_code=400)

    original = use_fake(rate_limited_400)
    try:
        su.fetch_xueqiu_articles("605499", token="t")
    except su.XueqiuRateLimitError as error:
        assert "操作过于频繁" in str(error), error
    else:
        raise AssertionError("HTTP 400 + 110017 应当抛限频")
    finally:
        restore(original)

    # 其它 HTTP 错误按普通失败处理
    original = use_fake(lambda url, **kwargs: FakeResponse(None, text="", status_code=567))
    try:
        su.fetch_xueqiu_comments(1, token="t")
    except su.XueqiuError as error:
        assert not isinstance(error, su.XueqiuTokenError)
        assert "567" in str(error), error
    else:
        raise AssertionError("HTTP 567 应当报错")
    finally:
        restore(original)


def test_rate_limit_retry() -> None:
    """被限频先退避重试；重试用完才把异常抛给调用方。"""
    wait = su.XUEQIU_RATE_LIMIT_WAIT
    su.XUEQIU_RATE_LIMIT_WAIT = 0
    try:
        state = {"calls": 0}

        def flaky(url, **kwargs):
            state["calls"] += 1
            if state["calls"] < 3:
                return FakeResponse(
                    {"error_code": "110017", "error_description": "操作过于频繁，请稍后再试"},
                    status_code=400,
                )
            return FakeResponse({"count": 0, "list": []})

        original = use_fake(flaky)
        try:
            assert su.fetch_xueqiu_articles("605499", token="t") == []
        finally:
            restore(original)
        assert state["calls"] == 3, state

        calls = {"n": 0}

        def always_limited(url, **kwargs):
            calls["n"] += 1
            return FakeResponse(
                {"error_code": "110017", "error_description": "操作过于频繁，请稍后再试"},
                status_code=400,
            )

        original = use_fake(always_limited)
        try:
            su.fetch_xueqiu_articles("605499", token="t")
        except su.XueqiuRateLimitError:
            pass
        else:
            raise AssertionError("重试用完应当抛限频")
        finally:
            restore(original)
        assert calls["n"] == su.XUEQIU_RATE_LIMIT_RETRIES + 1, calls
    finally:
        su.XUEQIU_RATE_LIMIT_WAIT = wait


def main() -> None:
    test_token()
    # 其余用例不关心 token 从哪来，统一走环境变量；测试里不必真的等节流
    os.environ[su.XUEQIU_TOKEN_ENV] = "test-token"
    su.XUEQIU_REQUEST_INTERVAL = 0
    su.XUEQIU_RATE_LIMIT_WAIT = 0
    try:
        test_symbol()
        test_articles_parse()
        test_comments_tree()
        test_comments_reply_only()
        test_articles_with_comments()
        test_failures()
        test_rate_limit_retry()
    finally:
        os.environ.pop(su.XUEQIU_TOKEN_ENV, None)
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
