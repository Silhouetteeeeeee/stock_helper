"""投资助手 Web 页面验证：标的桥、引用渲染与服务启动参数。"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 页面读默认状态文件与对话库；测试指向临时目录，必须在导入前设置
_STATE_DIR = tempfile.mkdtemp(prefix="assistant_web_")
os.environ["MARKET_ASSISTANT_STATE"] = str(Path(_STATE_DIR) / "state.json")
os.environ["MARKET_ASSISTANT_DB"] = str(Path(_STATE_DIR) / "assistant.db")

try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover - 没装 streamlit 时只跳过页面测试
    AppTest = None  # type: ignore[assignment]

from assistant import server
from assistant.models import Source
from assistant.render import (
    citation_html,
    cite_text,
    context_ring_html,
    source_lines,
    stream_text,
)
from assistant.selection import read_selection, write_selection
from assistant.store import ConversationStore, build_title


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_selection_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "state.json"
        assert read_selection(path) is None

        write_selection("605499", "东鹏饮料", path=path)
        state = read_selection(path)
        assert state is not None
        assert state["code"] == "605499"
        assert state["name"] == "东鹏饮料"
        assert state["is_index"] is False
        assert state["updated_at"]

        write_selection("sh000001", "上证指数", is_index=True, path=path)
        state = read_selection(path)
        assert state is not None
        assert state["code"] == "sh000001"
        assert state["is_index"] is True

        path.write_text("{not json", encoding="utf-8")
        assert read_selection(path) is None

        path.write_text('["605499"]', encoding="utf-8")
        assert read_selection(path) is None


def test_render_citations() -> None:
    sources = [
        Source("fin:1", "财报", "东财", "2026-06-30", "2026-10-25 10:00:00"),
        Source("fin:2", "公告", "东财", "2026-08-20", "2026-10-25 10:00:00"),
    ]
    text = "毛利率下降 [[fin:1]]，费用上升 [[fin:2]]，未知 [[nope]]"
    rendered = cite_text(text, sources)
    assert "[1]" in rendered and "[2]" in rendered and "[?]" in rendered
    assert "[[fin:1]]" not in rendered

    # 存进会话的是 dict，也要能渲染
    dicts = [source.to_dict() for source in sources]
    assert cite_text(text, dicts) == rendered

    lines = source_lines(dicts)
    assert lines[0] == "[1] 财报 · 东财 · 2026-06-30", lines[0]
    assert lines[1] == "[2] 公告 · 东财 · 2026-08-20", lines[1]

    many = [Source(f"s{i}", "财报", "东财", "2026-06-30", "") for i in range(8)]
    lines = source_lines(many, limit=6)
    assert len(lines) == 7
    assert "另有 2 条" in lines[-1]

    # 角标用 HTML，悬停内容放在 data-tip 里
    html = citation_html("毛利率 < 30% & 回升 [[fin:1]]，未知 [[nope]]", sources)
    assert "&lt; 30% &amp; 回升" in html, html
    assert (
        '<sup class="cite" data-tip="财报 · 东财 · 2026-06-30">[1]</sup>' in html
    ), html
    assert '<sup class="cite" data-tip="来源未登记">[?]</sup>' in html, html
    # 引用以外的 Markdown 语法要留着
    assert "> 引用" in citation_html("> 引用\n\n毛利率 [[fin:1]]", sources)


def test_context_ring() -> None:
    normal = context_ring_html({
        "tokens": 23100, "budget_tokens": 55000, "hard_tokens": 80000,
        "percent": 42.0, "ratio": 0.42, "level": "normal",
    })
    assert 'class="ctx-meter"' in normal
    assert "42%" in normal
    assert "23.1k" in normal and "55.0k" in normal
    assert "#4c8dff" in normal
    # 42% → 环留出 58% 的空缺
    assert 'stroke-dashoffset="167.6"' in normal, normal

    warn = context_ring_html({
        "tokens": 50000, "budget_tokens": 55000, "percent": 90.9,
        "ratio": 0.909, "level": "warn",
    })
    assert "#e8a33d" in warn and "91%" in warn

    over = context_ring_html({
        "tokens": 64900, "budget_tokens": 55000, "percent": 118.0,
        "ratio": 1.0, "level": "over",
    })
    assert "#e5534b" in over and "118%" in over
    assert "下次提问会先压缩" in over
    # 超额说明跟着环走，更新占位符时不会残留旧提示
    assert 'class="ctx-warn"' in over
    assert 'class="ctx-warn"' not in normal
    assert 'stroke-dashoffset="0.0"' in over

    # 越界或缺失的输入也要能画，不抛异常
    clamped = context_ring_html({"ratio": 3.0, "percent": 300, "level": "什么"})
    assert 'stroke-dashoffset="0.0"' in clamped
    assert "#4c8dff" in clamped
    empty = context_ring_html({})
    assert "0%" in empty

    # 流式过程中先隐藏内部 id，收尾时再换成角标
    assert stream_text("毛利率[[fin:1]]下降") == "毛利率下降"


def test_server_helpers() -> None:
    command = server.build_command(8600)
    assert command[0] == sys.executable
    assert "streamlit" in command and str(server.WEBAPP) in command
    assert "--server.address=127.0.0.1" in command
    assert "--server.port=8600" in command
    assert "--server.headless=true" in command
    assert "--browser.gatherUsageStats=false" in command

    # 首选端口被占用时让系统换一个
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        busy_port = int(occupied.getsockname()[1])
        port = server.find_free_port(busy_port)
        assert port != busy_port, port

    # 空闲端口上没有服务，健康检查应为 False
    free_port = _closed_port()
    assert server.is_healthy(free_port, timeout=0.2) is False
    assert server.url_for(free_port) == f"http://127.0.0.1:{free_port}/"


class _FakeAnswer:
    text = "毛利率下降 [[fin:1]]\n\n## 结论\n继续观察 [[fin:2]]"
    citations = [
        Source("fin:1", "财报", "东财", "2026-06-30", "2026-10-25 10:00:00"),
        Source("fin:2", "公告", "东财", "2026-08-20", "2026-10-25 10:00:00"),
    ]
    trace = [{
        "round": 1,
        "name": "get_finance",
        "company": "605499",
        "as_of": "2026-06-30",
        "ok": True,
    }]
    issues = []


class _FakeAgent:
    def __init__(self) -> None:
        self.asked: list[str] = []
        self._history: list[dict] = []

    def ask(self, question: str):
        self.asked.append(question)
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": _FakeAnswer.text})
        return _FakeAnswer()

    def ask_stream(self, question: str, emit):
        self.asked.append(question)
        self._history.append({"role": "user", "content": question})
        # 先推一段草稿再取数：页面应清掉草稿，从正式答案重新流
        emit({"type": "text", "delta": "我先查一下。"})
        emit({"type": "discard"})
        for step in _FakeAnswer.trace:
            emit({"type": "tool", **step})
        for piece in ("毛利率下降 ", "[[fin:1]]", "\n\n## 结论\n继续观察 [[fin:2]]"):
            emit({"type": "text", "delta": piece})
        self._history.append({"role": "assistant", "content": _FakeAnswer.text})
        return _FakeAnswer()

    def context_usage(self, extra_tokens: int = 0) -> dict:
        # 按历史长度增长：提问前 5.0k，一问一答后 23.1k，页面要能当场刷新
        tokens = 5000 + 9050 * len(self._history) + extra_tokens
        share = tokens / 55000
        return {
            "tokens": tokens, "budget_tokens": 55000, "hard_tokens": 80000,
            "percent": round(share * 100, 1), "ratio": round(min(share, 1.0), 4),
            "level": "normal" if share < 0.8 else "warn",
        }

    def history_size(self) -> int:
        return len(self._history)

    def history_since(self, start: int) -> list[dict]:
        return [dict(message) for message in self._history[start:]]


def _app():
    return AppTest.from_file(str(server.WEBAPP), default_timeout=60)


def _button(at, label: str):
    for button in at.button:
        if button.label == label:
            return button
    raise AssertionError(f"没有找到按钮：{label}（{ [b.label for b in at.button] }）")


def _clear_store() -> None:
    store = ConversationStore()
    # 删除操作只能发生在临时库，绝不能碰用户的 data/assistant.db
    assert Path(store.path).resolve().parent == Path(_STATE_DIR).resolve(), store.path
    for item in store.recent(limit=100):
        store.delete(item.id)


def test_webapp_flow() -> None:
    if AppTest is None:
        return
    at = _app()
    at.run()
    assert not at.exception, list(at.exception)
    # 行情窗口选了什么都不影响页面：没有对话时不指定公司
    write_selection("605499", "东鹏饮料")
    at.run()
    assert at.session_state["active_code"] is None
    assert [title.value for title in at.title] == ["投资助手"]

    # 模拟已经在聊东鹏，再注入假模型（首次运行之后的注入不会被切公司清掉）
    at.session_state["active_code"] = "605499"
    at.session_state["active_name"] = "东鹏饮料"
    agent = _FakeAgent()
    at.session_state["agent"] = agent
    at.session_state["agent_code"] = "605499"
    at.chat_input[0].set_value("毛利率为什么下滑").run()
    assert not at.exception, list(at.exception)
    assert agent.asked == ["毛利率为什么下滑"], agent.asked
    # 标题就写明在聊哪家公司，正文不再另起一行“当前：…”
    assert [title.value for title in at.title] == ["投资助手 · 东鹏饮料"]

    messages = at.session_state["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["trace"][0]["name"] == "get_finance"

    markdown = "\n".join(item.value for item in at.markdown)
    assert (
        '毛利率下降 <sup class="cite" data-tip="财报 · 东财 · 2026-06-30">[1]</sup>'
        in markdown
    ), markdown
    assert (
        '继续观察 <sup class="cite" data-tip="公告 · 东财 · 2026-08-20">[2]</sup>'
        in markdown
    ), markdown
    assert "我先查一下" not in markdown, markdown
    assert "1. get_finance · 605499 · 2026-06-30 · 成功" in markdown, markdown
    captions = [item.value for item in at.caption]
    assert "[1] 财报 · 东财 · 2026-06-30" in captions, captions
    assert [item.label for item in at.expander] == ["研究过程：1 次取数"]
    # 侧栏的上下文占用环在同一轮里就刷新到回答后的占用，不停在提问前的 5.0k
    meters = [item.value for item in at.markdown if 'class="ctx-meter"' in item.value]
    assert len(meters) == 1, meters
    meter = meters[0]
    assert ">42%</text>" in meter, meter
    assert "23.1k / 55.0k" in meter, meter
    # 提问前的 9% 不能留在环里（占位符要真的被覆盖）
    assert ">9%</text>" not in meter, meter

    # 对话已落库：一问一答两条，原始消息留给续聊
    store = ConversationStore()
    saved = store.recent(primary_code="605499")
    assert len(saved) == 1, saved
    assert saved[0].title.startswith("东鹏饮料 · 毛利率为什么下滑")
    saved_messages = store.messages(saved[0].id)
    assert [message["role"] for message in saved_messages] == ["user", "assistant"]
    assert saved_messages[1]["raw"] == [
        {"role": "user", "content": "毛利率为什么下滑"},
        {"role": "assistant", "content": _FakeAnswer.text},
    ], saved_messages[1]["raw"]

    # 新对话只清空当前会话，记录留在最近列表
    _button(at, "新对话").click().run()
    assert not at.exception, list(at.exception)
    assert at.session_state["messages"] == []
    assert at.session_state["conversation_id"] is None
    assert any("新对话" in item.value for item in at.info)
    assert len(store.recent(primary_code="605499")) == 1


def test_webapp_resume_and_delete() -> None:
    if AppTest is None:
        return
    _clear_store()

    store = ConversationStore()
    conversation_id = store.create(
        build_title("贵州茅台", "渠道怎么样"), "600519", "贵州茅台"
    )
    store.append(conversation_id, "user", {"text": "渠道怎么样"})
    store.append(conversation_id, "assistant", {
        "text": "直营占比提升 [[fin:9]]",
        "sources": [Source("fin:9", "财报", "东财", "2026-06-30", "").to_dict()],
        "trace": 2,
        "issues": [],
        "raw": [],
    })
    # 另一家公司的对话也要留在左侧，不因为当前聊的是谁而消失
    other_id = store.create(build_title("紫金矿业", "分红怎么样"), "601899", "紫金矿业")
    store.append(other_id, "user", {"text": "分红怎么样"})

    at = _app()
    at.run()
    assert not at.exception, list(at.exception)
    assert at.session_state["messages"] == []

    # 最近列表在左侧，点标题即可恢复这段对话
    _button(at, "贵州茅台 · 渠道怎么样").click().run()
    assert not at.exception, list(at.exception)
    assert at.session_state["conversation_id"] == conversation_id
    assert [message["role"] for message in at.session_state["messages"]] == [
        "user",
        "assistant",
    ]
    markdown = "\n".join(item.value for item in at.markdown)
    assert (
        '直营占比提升 <sup class="cite" data-tip="财报 · 东财 · 2026-06-30">[1]</sup>'
        in markdown
    ), markdown
    # 旧数据只存了次数，不能因此报错
    assert any("研究过程：2 次取数" in item.value for item in at.caption)
    # 当前聊茅台，紫金的对话仍在左侧
    labels = [button.label for button in at.button]
    assert any(label.startswith("紫金矿业 · 分红怎么样") for label in labels), labels

    _button(at, "删除当前对话").click().run()
    assert not at.exception, list(at.exception)
    assert at.session_state["messages"] == []
    assert store.messages(conversation_id) == []
    assert any("已删除" in item.value for item in at.info)


def test_webapp_mentions_company() -> None:
    """对话提到哪家公司就聊哪家；没提就问清楚，不猜也不拦着输入。"""
    if AppTest is None:
        return
    _clear_store()

    from assistant import agent as agent_module

    at = _app()
    at.run()
    assert not at.exception, list(at.exception)
    # 还没聊公司：标题不带公司
    assert at.session_state["active_code"] is None
    assert [title.value for title in at.title] == ["投资助手"]
    assert any("带上公司名或代码" in item.value for item in at.info)
    assert not any(item.value.startswith("当前：") for item in at.caption)

    # 没提公司就提问：提示补上公司，不调用模型
    at.chat_input[0].set_value("毛利率为什么下滑？").run()
    assert not at.exception, list(at.exception)
    assert at.session_state["active_code"] is None
    assert any("没认出你问的是哪家公司" in item.value for item in at.warning)

    def _offline_client():
        raise RuntimeError("测试环境不调用模型")

    original = agent_module.build_client
    agent_module.build_client = _offline_client
    try:
        at.chat_input[0].set_value("东鹏饮料毛利率为什么下滑").run()
        assert not at.exception, list(at.exception)
        # 这一句里提到的公司就是本轮聊的公司，标题随之更新
        assert at.session_state["active_code"] == "605499"
        assert at.session_state["active_name"] == "东鹏饮料"
        assert [title.value for title in at.title] == ["投资助手 · 东鹏饮料"]
        assert any("助手不可用" in item.value for item in at.error)
        # 标题下面只有一条引导，不再有“当前：…”那行
        assert not any(item.value.startswith("当前：") for item in at.caption)
    finally:
        agent_module.build_client = original


def test_webapp_market_question() -> None:
    """大盘问题不需要公司：不提示补公司，开一段独立的大盘会话。"""
    if AppTest is None:
        return
    _clear_store()

    from assistant import agent as agent_module
    from assistant.tools import MARKET_CODE, MARKET_NAME

    at = _app()
    at.run()
    assert not at.exception, list(at.exception)

    def _offline_client():
        raise RuntimeError("测试环境不调用模型")

    original = agent_module.build_client
    agent_module.build_client = _offline_client
    try:
        at.chat_input[0].set_value("今天大盘怎么看 有什么操作建议").run()
        assert not at.exception, list(at.exception)
        # 没提公司也能提问：路由到大盘，而不是被“请补公司”拦住
        assert at.session_state["active_code"] == MARKET_CODE
        assert at.session_state["active_name"] == MARKET_NAME
        assert [title.value for title in at.title] == ["投资助手 · 大盘"]
        assert not any("没认出你问的是哪家公司" in item.value for item in at.warning)
        assert any("助手不可用" in item.value for item in at.error)
    finally:
        agent_module.build_client = original

    # 即使模型不可用，问题也留在大盘会话里
    store = ConversationStore()
    saved = store.recent(primary_code=MARKET_CODE)
    assert len(saved) == 1, saved
    assert saved[0].title.startswith("大盘 · 今天大盘怎么看")
    assert [message["role"] for message in store.messages(saved[0].id)] == [
        "user", "error",
    ]


def test_kill_on_close_job() -> None:
    """父进程被强杀（IDE 终止）时，Windows 也要把服务子进程一起带走。"""
    if os.name != "nt":
        return
    import ctypes
    import subprocess

    job = server._kill_on_close_job()
    assert job, "创建 Job Object 失败"

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert server._assign_to_job(job, child.pid), "把子进程放进 Job 失败"
        # 关闭句柄等价于父进程退出：kill-on-close 会立刻结束子进程
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(job))
        assert child.wait(timeout=15) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def main() -> None:
    test_selection_roundtrip()
    test_render_citations()
    test_server_helpers()
    test_kill_on_close_job()
    test_webapp_flow()
    test_webapp_resume_and_delete()
    test_webapp_mentions_company()
    test_webapp_market_question()
    test_context_ring()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
