"""投资助手 Web 页面（Streamlit）。

独立进程运行，由行情窗口的 AI 按钮启动。页面不跟随行情窗口：聊哪家公司
只看对话里提到了谁。默认是一整栏；把顶部会话条里的历史对话长按拖到
「右栏」，页面才分成左右两栏，各自独立对话。模型与工具复用 ``assistant``
包，API key 只留在服务端，浏览器拿不到。

启动：``python -m streamlit run assistant/webapp.py``
"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from assistant.agent import ResearchAgent, build_client, estimate_tokens
from assistant.env import load_env
from assistant.panes import assign_panes, new_pane, open_in_left, pane_ids
from assistant.render import (
    citation_html,
    context_meter_style,
    source_lines,
    stream_text,
)
from assistant.store import ConversationStore, build_title
from assistant.tools import (
    MARKET_CODE,
    MARKET_NAME,
    find_company_in_text,
    is_market_question,
    refers_to_company,
)

WELCOME = "直接提问，提到哪家公司就聊哪家，例如「紫金矿业靠什么赚钱」「东鹏饮料毛利率为什么下滑」；也可以问「今天大盘怎么看」。回答会标注数据来源。"
MAX_ISSUES = 2
RECENT_LIMIT = 50
STREAM_INTERVAL = 0.08
METER_INTERVAL = 0.4
SINGLE_WIDTH = 860
SPLIT_WIDTH = 1280
BAR_DIR = Path(__file__).resolve().parent / "conversation_bar"

STORE = ConversationStore()
conversation_bar = components.declare_component(
    "assistant_conversation_bar", path=str(BAR_DIR)
)

st.set_page_config(
    page_title="投资助手",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_env()

# 引用角标与正文的样式；宽度另外按单栏/双栏注入
CITATION_CSS = """
<style>
[data-testid="stChatMessage"] { border-radius: 10px; }

sup.cite {
    color: #7fb0ff;
    font-weight: 700;
    cursor: help;
    position: relative;
    margin-left: 1px;
}
sup.cite::after {
    content: attr(data-tip);
    position: absolute;
    left: 50%;
    bottom: 1.5em;
    transform: translateX(-50%);
    width: max-content;
    max-width: 320px;
    white-space: normal;
    background: #262c3a;
    color: #f2f4f8;
    font-size: 0.75rem;
    font-weight: 400;
    line-height: 1.4;
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.12s ease;
    z-index: 1000;
}
sup.cite:hover::after { opacity: 1; }
</style>
"""


# ---------------------------------------------------------------------------
# 会话状态
# ---------------------------------------------------------------------------
def _init_state() -> None:
    if "panes" not in st.session_state:
        st.session_state.panes = {"left": new_pane(), "right": None}
    if "bar_nonce" not in st.session_state:
        st.session_state.bar_nonce = None


def _left() -> dict:
    return st.session_state.panes["left"]


def _right():
    return st.session_state.panes["right"]


def _is_split() -> bool:
    return st.session_state.panes["right"] is not None


def _page_style(width: int) -> str:
    """单栏保持窄阅读宽度，双栏时把容器放宽；底部的输入框跟着对齐。"""
    return (
        "<style>"
        f".block-container {{ max-width: {width}px; margin: 0 auto; "
        "padding-top: 2.2rem; }"
        f'[data-testid="stBottomBlockContainer"] {{ max-width: {width}px; '
        "margin: 0 auto; }"
        "</style>"
    )


def _reset_pane(pane: dict) -> None:
    pane["messages"] = []
    pane["agent"] = None
    pane["agent_code"] = None
    pane["conversation_id"] = None
    pane["agent_error"] = ""
    pane["pending"] = None


def _switch_company(pane: dict, code: str, name: str) -> None:
    """切换讨论的公司：换公司就开新对话，避免上下文串台。"""
    current = pane.get("code")
    if code == current:
        if name:
            pane["name"] = name
        return
    pane["code"] = code
    pane["name"] = name or code
    _reset_pane(pane)
    if current:
        pane["notice"] = f"已切换到 {name or code}（{code}），开始新对话。"


def _switch_market(pane: dict) -> None:
    """大盘问题不绑定任何公司；换到市场话题就开新对话。"""
    if pane.get("code") == MARKET_CODE:
        return
    pane["code"] = MARKET_CODE
    pane["name"] = MARKET_NAME
    _reset_pane(pane)
    pane["notice"] = "已切换到大盘，开始新对话。"


def _route_pane(pane: dict, prompt: str) -> bool:
    """按问题决定这一栏聊哪家公司；返回是否已经能提问。"""
    mentioned = find_company_in_text(prompt)
    current = pane.get("code")
    if mentioned:
        # 提到了公司就以公司为准，即使句子里还有“大盘”“走势”之类的词
        _switch_company(pane, *mentioned)
    elif is_market_question(prompt):
        # 已经在聊某家公司时，“它的行业轮动”问的还是这家，不该被关键词带去新会话
        keeps_company = (
            current not in (None, MARKET_CODE) and refers_to_company(prompt)
        )
        if not keeps_company:
            _switch_market(pane)
    return bool(pane.get("code"))


def _raw_history(pane: dict) -> list[dict]:
    conversation_id = pane.get("conversation_id")
    if not conversation_id:
        return []
    raw: list[dict] = []
    for message in STORE.messages(int(conversation_id)):
        if message.get("role") == "assistant":
            raw.extend(message.get("raw") or [])
    return raw


def _agent_for(pane: dict):
    """每一栏各自持有助手，左右两边的上下文互不影响。"""
    code = pane.get("code")
    if not code:
        return None
    agent = pane.get("agent")
    if agent is not None and pane.get("agent_code") == code:
        return agent
    try:
        # 大盘会话不设默认公司，避免公司工具把 MARKET 当成股票代码
        default_company = None if code == MARKET_CODE else code
        agent = ResearchAgent(build_client(), default_company=default_company)
        conversation_id = pane.get("conversation_id")
        if conversation_id:
            agent.restore_history(_raw_history(pane))
    except Exception as error:  # noqa: BLE001 - 缺 key 等要显示在页面上
        pane["agent_error"] = str(error) or "助手初始化失败"
        return None
    pane["agent"] = agent
    pane["agent_code"] = code
    pane["agent_error"] = ""
    return agent


def _ensure_conversation(pane: dict, question: str) -> int:
    conversation_id = pane.get("conversation_id")
    if conversation_id:
        return int(conversation_id)
    name = pane.get("name") or pane.get("code") or ""
    conversation_id = STORE.create(
        build_title(name, question), pane.get("code") or "", name
    )
    pane["conversation_id"] = conversation_id
    return conversation_id


def _append(pane: dict, role: str, payload: dict) -> None:
    conversation_id = pane.get("conversation_id")
    if conversation_id:
        STORE.append(int(conversation_id), role, payload)


def _start_question(pane: dict, question: str) -> None:
    """把提问写进这一栏（也落库），后面的回答再追加。"""
    _ensure_conversation(pane, question)
    pane["messages"].append({"role": "user", "text": question})
    _append(pane, "user", {"text": question})


# ---------------------------------------------------------------------------
# 顶部会话条
# ---------------------------------------------------------------------------
def _bar_conversations() -> list[dict]:
    """会话条只传需要的字段；搜索在组件里本地过滤，不打回 Streamlit。"""
    return [
        {
            "id": item.id,
            "title": item.title,
            "name": item.primary_name or item.primary_code or "",
            "code": item.primary_code or "",
        }
        for item in STORE.recent(limit=RECENT_LIMIT)
    ]


def _apply_bar_value(value) -> bool:
    """处理会话条的点击/拖放；返回是否需要重跑页面。"""
    if not isinstance(value, dict):
        return False
    nonce = value.get("nonce")
    if not nonce or nonce == st.session_state.get("bar_nonce"):
        return False
    st.session_state.bar_nonce = nonce

    action = value.get("action")
    known = {item.id: item for item in STORE.recent(limit=RECENT_LIMIT)}
    panes = st.session_state.panes
    if action == "open":
        try:
            item = known.get(int(value.get("id")))
        except (TypeError, ValueError):
            item = None
        if item is None:
            return False
        st.session_state.panes = open_in_left(panes, item, STORE.messages)
        return True
    if action == "assign":
        st.session_state.panes = assign_panes(
            panes, value.get("panes") or {}, known, STORE.messages
        )
        return True
    return False


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _trace_line(step: dict) -> str:
    """一轮取数压成一行：第几轮、工具、公司、数据日期、结果。"""
    name = step.get("name") or "未知工具"
    company = step.get("company") or "--"
    as_of = step.get("as_of") or "无日期"
    outcome = "成功" if step.get("ok") else f"失败：{step.get('error') or '未知错误'}"
    return f"{step.get('round') or '-'}. {name} · {company} · {as_of} · {outcome}"


def _render_assistant(message: dict) -> None:
    sources = message.get("sources") or []
    body = citation_html(message.get("text", ""), sources)
    st.markdown(body or "_（没有返回正文）_", unsafe_allow_html=True)
    lines = source_lines(sources)
    if lines:
        st.caption("来源：")
        for line in lines:
            st.caption(line)

    # 新消息存完整步骤；旧消息只有次数，退化成一行说明
    trace = message.get("trace")
    steps = list(trace) if isinstance(trace, list) else []
    count = len(steps) if steps else int(trace or 0)
    if count:
        label = f"研究过程：{count} 次取数"
        if steps:
            with st.expander(label):
                for step in steps:
                    st.markdown(_trace_line(step))
        else:
            st.caption(label)
    for issue in message.get("issues") or []:
        st.caption(f"提示：{issue}")


def _render_context_meter(
    agent, slot, extra_tokens: int = 0, container: str = ""
) -> None:
    """把占用环的样式写进占位符，环贴在发送按钮旁；助手不存在时清空。"""
    if slot is None:
        return
    getter = getattr(agent, "context_usage", None)
    usage = None
    if callable(getter):
        try:
            usage = getter(extra_tokens)
        except Exception:  # noqa: BLE001 - 占用显示失败不影响对话
            usage = None
    if not usage:
        slot.empty()
        return
    # 覆盖同一个占位符：一问一答过程中环会跟着涨，不会停在提问前的数值
    style = context_meter_style(usage, container=container)
    slot.markdown(style, unsafe_allow_html=True)


def _render_history(pane: dict) -> None:
    for message in pane["messages"]:
        role = message.get("role")
        text = message.get("text", "")
        if role == "user":
            with st.chat_message("user"):
                st.markdown(text)
        elif role == "error":
            with st.chat_message("assistant"):
                st.error(text)
        else:
            with st.chat_message("assistant"):
                _render_assistant(message)


def _answer_message(answer) -> dict:
    sources = [
        source.to_dict() if hasattr(source, "to_dict") else dict(source)
        for source in (getattr(answer, "citations", None) or [])
    ]
    issues = [
        str(issue.get("message") or issue.get("type") or "")
        for issue in (getattr(answer, "issues", None) or [])[:MAX_ISSUES]
    ]
    return {
        "role": "assistant",
        "text": getattr(answer, "text", "") or "",
        "sources": sources,
        "trace": list(getattr(answer, "trace", None) or []),
        "issues": [issue for issue in issues if issue],
    }


def _answer(
    pane: dict, question: str, meter_slot, answer_slot, container: str = ""
) -> None:
    """取数并流式回答；正文直接画在输入框上方的占位符里。"""
    agent = _agent_for(pane)
    if agent is None:
        text = f"助手不可用：{pane.get('agent_error') or '缺少模型配置'}"
        pane["messages"].append({"role": "error", "text": text})
        _append(pane, "error", {"text": text})
        with answer_slot.container():
            with st.chat_message("assistant"):
                st.error(text)
        _render_context_meter(None, meter_slot)
        return

    start = agent.history_size()
    failed = ""
    answer_slot.empty()
    with answer_slot.container():
        with st.chat_message("assistant"):
            placeholder = st.empty()
            steps = st.empty()
            streamed = {"text": "", "last": 0.0, "last_meter": 0.0}

            def emit(event: dict) -> None:
                kind = event.get("type")
                if kind == "text":
                    streamed["text"] += event.get("delta") or ""
                    now = time.monotonic()
                    if now - streamed["last"] >= STREAM_INTERVAL:
                        streamed["last"] = now
                        placeholder.markdown(
                            stream_text(streamed["text"]) + " ▌"
                        )
                    # 草稿的 token 也计入上下文，环跟着回答往下走
                    if now - streamed["last_meter"] >= METER_INTERVAL:
                        streamed["last_meter"] = now
                        _render_context_meter(
                            agent,
                            meter_slot,
                            estimate_tokens(streamed["text"]),
                            container,
                        )
                elif kind == "discard":
                    # 这一轮其实是取数，先把草稿清掉
                    streamed["text"] = ""
                    placeholder.markdown("_正在取数…_")
                    _render_context_meter(agent, meter_slot, container=container)
                elif kind == "tool":
                    if event.get("cached"):
                        outcome = "复用缓存"
                    else:
                        outcome = "成功" if event.get("ok") else "失败"
                    steps.caption(
                        f"已取数：{event.get('name')} · "
                        f"{event.get('company') or '--'} · {outcome}"
                    )
                    # 取数结果已经进了历史，占用会跳一截
                    _render_context_meter(agent, meter_slot, container=container)

            try:
                answer = agent.ask_stream(question, emit)
            except Exception as error:  # noqa: BLE001 - 失败要留在对话里
                failed = f"研究失败：{error}"

    if failed:
        answer_slot.empty()
        with answer_slot.container():
            with st.chat_message("assistant"):
                st.error(failed)
        pane["messages"].append({"role": "error", "text": failed})
        _append(pane, "error", {"text": failed})
        _render_context_meter(agent, meter_slot, container=container)
        return

    message = _answer_message(answer)
    message["raw"] = agent.history_since(start)
    answer_slot.empty()
    with answer_slot.container():
        with st.chat_message("assistant"):
            _render_assistant(message)
    pane["messages"].append(message)
    _append(pane, "assistant", message)
    # 收尾再画一次：去掉流式草稿的临时增量，显示落库后的真实占用
    _render_context_meter(agent, meter_slot, container=container)


def _pane_header(pane: dict, side: str) -> None:
    label = "左栏" if side == "left" else "右栏"
    name = pane.get("name") or "新对话"
    if side == "left":
        st.markdown(f"**{label} · {name}**")
        return
    head, close = st.columns([4, 1])
    head.markdown(f"**{label} · {name}**")
    if close.button("关闭", key="close_right", help="收起右栏，回到一整栏"):
        st.session_state.panes["right"] = None
        st.rerun()


def _render_single(pane: dict, prompt: str | None, warning: bool) -> None:
    if pane.get("notice"):
        st.info(pane["notice"])
        pane["notice"] = ""

    if not pane.get("messages"):
        st.caption(WELCOME)

    _render_history(pane)

    answer_slot = st.empty()
    meter_slot = st.empty()
    _render_context_meter(_agent_for(pane), meter_slot)

    if warning:
        st.warning("没认出你问的是哪家公司，请在问题里带上公司名或六位代码。")
    if not pane.get("code"):
        st.info(
            "在问题里带上公司名或代码即可，例如「紫金矿业靠什么赚钱」；"
            "也可以直接问「今天大盘怎么看」。"
        )
    if prompt:
        _answer(pane, prompt, meter_slot, answer_slot)


def _render_pane(
    pane: dict, side: str, question: str = "", warning: str = ""
) -> None:
    """双栏模式下的一栏；上一轮提交的问题已在页面顶部完成路由。"""
    _pane_header(pane, side)
    if pane.get("notice"):
        st.info(pane["notice"])
        pane["notice"] = ""

    if not pane.get("messages"):
        st.caption(WELCOME)
    _render_history(pane)

    answer_slot = st.empty()
    meter_slot = st.empty()
    _render_context_meter(
        _agent_for(pane), meter_slot, container=f".st-key-pane_{side}"
    )
    if warning:
        st.warning(warning)
    if not pane.get("code") and not question:
        st.info("问一句就会自动认出公司；也可以直接问「今天大盘怎么看」。")

    new_prompt = st.chat_input("问一句…", key=f"chat_{side}")
    if new_prompt:
        pane["pending"] = new_prompt
        st.rerun()

    if question:
        _answer(
            pane,
            question,
            meter_slot,
            answer_slot,
            container=f".st-key-pane_{side}",
        )


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
_init_state()

# 双栏时上一轮提交的问题先路由、落库，标题和会话条才能当场换到新公司；
# 回答留到对应那一栏渲染时再流式输出。
questions: dict[str, str] = {}
warnings: dict[str, str] = {}
if _is_split():
    for side in ("left", "right"):
        pane = st.session_state.panes.get(side)
        if not pane or not pane.get("pending"):
            continue
        question = pane["pending"]
        pane["pending"] = None
        if _route_pane(pane, question):
            _start_question(pane, question)
            questions[side] = question
        else:
            warnings[side] = (
                "没认出你问的是哪家公司，请在问题里带上公司名或六位代码。"
            )

width = SPLIT_WIDTH if _is_split() else SINGLE_WIDTH
st.markdown(_page_style(width), unsafe_allow_html=True)
st.markdown(CITATION_CSS, unsafe_allow_html=True)

# 输入框固定在页面底部，位置不受调用顺序影响：先读出来，
# 这一句提到哪家公司，标题和本轮对话就跟到哪家。双栏时输入框在各自栏内。
prompt = None
warning = False
if not _is_split():
    prompt = st.chat_input("问一句，如：紫金矿业靠什么赚钱？", key="chat_left")
    if prompt:
        if _route_pane(_left(), prompt):
            _start_question(_left(), prompt)
        else:
            warning = True

left = _left()
right = _right()
names = [
    pane.get("name") or pane.get("code")
    for pane in (left, right)
    if pane and (pane.get("name") or pane.get("code"))
]
heading = "投资助手" if not names else "投资助手 · " + " | ".join(names)
st.title(heading)

bar_value = conversation_bar(
    conversations=_bar_conversations(),
    panes=pane_ids(st.session_state.panes),
    labels={
        "left": left.get("name") or "",
        "right": (right or {}).get("name") or "",
    },
    default=None,
    key="conversation_bar",
)
if _apply_bar_value(bar_value):
    st.rerun()

with st.sidebar:
    if st.button("新对话", use_container_width=True):
        pane = new_pane()
        pane["notice"] = "已开始新对话，之前的记录在顶部会话条里。"
        st.session_state.panes["left"] = pane
        st.rerun()
    left_id = left.get("conversation_id")
    if left_id and st.button("删除当前对话", use_container_width=True):
        STORE.delete(int(left_id))
        pane = new_pane()
        pane["notice"] = "已删除当前对话。"
        st.session_state.panes["left"] = pane
        st.rerun()

    st.caption(
        "回答里的 [n] 对应下方来源。数据来自本地已入库的财报、公告、调研与行情。"
    )
    st.divider()
    st.markdown("### 最近对话")
    st.caption("在页面顶部：点击打开到左栏；长按拖到右栏可分屏。")

if _is_split():
    left_col, right_col = st.columns(2, gap="large")
    with left_col:
        with st.container(key="pane_left"):
            _render_pane(
                st.session_state.panes["left"],
                "left",
                questions.get("left", ""),
                warnings.get("left", ""),
            )
    with right_col:
        with st.container(key="pane_right"):
            _render_pane(
                st.session_state.panes["right"],
                "right",
                questions.get("right", ""),
                warnings.get("right", ""),
            )
else:
    _render_single(st.session_state.panes["left"], prompt, warning)
