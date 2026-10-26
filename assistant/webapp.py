"""投资助手 Web 页面（Streamlit）。

独立进程运行，由行情窗口的 AI 按钮启动。页面不跟随行情窗口：聊哪家公司
只看对话里提到了谁，左边列出全部历史对话。模型与工具复用 ``assistant``
包，API key 只留在服务端，浏览器拿不到。

启动：``python -m streamlit run assistant/webapp.py``
"""

from __future__ import annotations

import time

import streamlit as st

from assistant.agent import ResearchAgent, build_client, estimate_tokens
from assistant.env import load_env
from assistant.render import (
    citation_html,
    context_ring_html,
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

STORE = ConversationStore()

st.set_page_config(
    page_title="投资助手",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="expanded",
)

load_env()

st.markdown(
    """
    <style>
    .block-container { max-width: 860px; padding-top: 2.2rem; }
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
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 会话状态
# ---------------------------------------------------------------------------
def _init_state() -> None:
    for key, default in (
        ("messages", []),
        ("agent", None),
        ("agent_code", None),
        ("active_code", None),
        ("active_name", ""),
        ("conversation_id", None),
        ("notice", ""),
    ):
        if key not in st.session_state:
            st.session_state[key] = default


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.agent = None
    st.session_state.agent_code = None
    st.session_state.conversation_id = None
    st.session_state.agent_error = ""


def _switch_company(code: str, name: str) -> None:
    """切换讨论的公司：换公司就开新对话，避免上下文串台。"""
    current = st.session_state.get("active_code")
    if code == current:
        if name:
            st.session_state.active_name = name
        return
    st.session_state.active_code = code
    st.session_state.active_name = name or code
    _reset_conversation()
    if current:
        st.session_state.notice = f"已切换到 {name or code}（{code}），开始新对话。"


def _switch_market() -> None:
    """大盘问题不绑定任何公司；换到市场话题就开新对话。"""
    if st.session_state.get("active_code") == MARKET_CODE:
        return
    st.session_state.active_code = MARKET_CODE
    st.session_state.active_name = MARKET_NAME
    _reset_conversation()
    st.session_state.notice = "已切换到大盘，开始新对话。"


def _open_conversation(item) -> None:
    """从最近列表恢复一段对话；下一轮提问时再回放原始消息。"""
    st.session_state.active_code = item.primary_code or None
    st.session_state.active_name = item.primary_name or item.primary_code
    st.session_state.messages = STORE.messages(item.id)
    st.session_state.conversation_id = item.id
    st.session_state.agent = None
    st.session_state.agent_code = None
    st.session_state.notice = f"已打开对话：{item.title}"
    st.rerun()


def _ensure_conversation(code: str, name: str, question: str) -> int:
    conversation_id = st.session_state.get("conversation_id")
    if conversation_id:
        return int(conversation_id)
    conversation_id = STORE.create(build_title(name, question), code, name)
    st.session_state.conversation_id = conversation_id
    return conversation_id


def _raw_history(conversation_id: int) -> list[dict]:
    raw: list[dict] = []
    for message in STORE.messages(conversation_id):
        if message.get("role") == "assistant":
            raw.extend(message.get("raw") or [])
    return raw


def _agent_for(code: str):
    agent = st.session_state.get("agent")
    if agent is not None and st.session_state.get("agent_code") == code:
        return agent
    try:
        # 大盘会话不设默认公司，避免公司工具把 MARKET 当成股票代码
        default_company = None if code == MARKET_CODE else code
        agent = ResearchAgent(build_client(), default_company=default_company)
        conversation_id = st.session_state.get("conversation_id")
        if conversation_id:
            agent.restore_history(_raw_history(conversation_id))
    except Exception as error:  # noqa: BLE001 - 缺 key 等要显示在页面上
        st.session_state.agent_error = str(error) or "助手初始化失败"
        return None
    st.session_state.agent = agent
    st.session_state.agent_code = code
    st.session_state.agent_error = ""
    return agent


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


def _render_context_meter(agent, slot, extra_tokens: int = 0) -> None:
    """把占用环画进侧栏占位符；助手还没建起来或统计失败时清空。"""
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
    slot.markdown(context_ring_html(usage), unsafe_allow_html=True)


def _render_history() -> None:
    for message in st.session_state.messages:
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


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
_init_state()

# 输入框固定在页面底部，位置不受调用顺序影响：先读出来，
# 这一句提到哪家公司，标题和本轮对话就跟到哪家。
prompt = st.chat_input("问一句，如：紫金矿业靠什么赚钱？")
mentioned = find_company_in_text(prompt) if prompt else None
current_code = st.session_state.get("active_code")
if mentioned:
    # 提到了公司就以公司为准，即使句子里还有“大盘”“走势”之类的词
    _switch_company(*mentioned)
elif prompt and is_market_question(prompt):
    # 已经在聊某家公司时，“它的行业轮动”问的还是这家，不该被关键词带去新会话
    keeps_company = (
        current_code not in (None, MARKET_CODE) and refers_to_company(prompt)
    )
    if not keeps_company:
        _switch_market()

active_code = st.session_state.get("active_code")
active_name = st.session_state.get("active_name") or active_code

with st.sidebar:
    if st.button("新对话", use_container_width=True):
        _reset_conversation()
        st.session_state.notice = "已开始新对话，之前的记录在下方列表中。"
        st.rerun()
    if st.session_state.get("conversation_id") and st.button(
        "删除当前对话", use_container_width=True
    ):
        STORE.delete(int(st.session_state.conversation_id))
        _reset_conversation()
        st.session_state.notice = "已删除当前对话。"
        st.rerun()

    # 会话一旦存在就提前建好助手，占用环才能显示当前上下文；尚未提问时为空。
    # 环画在占位符里，回答过程中可以在同一个位置刷新，不必等下一次 rerun。
    meter_slot = st.empty()
    _render_context_meter(
        _agent_for(active_code) if active_code else None, meter_slot
    )

    st.caption("回答里的 [n] 对应下方来源。数据来自本地已入库的财报、公告、调研与行情。")

    st.divider()
    st.markdown("### 最近对话")
    query = st.text_input(
        "搜索对话", placeholder="公司、代码或标题", key="conversation_query"
    ).strip()
    # 不按当前公司过滤：所有对话都留在左侧，换公司、开新对话都不会让旧记录消失
    recent = STORE.recent(query=query, limit=RECENT_LIMIT)
    if not recent:
        st.caption("还没有保存的对话。")
    for item in recent:
        current = " · 当前" if item.id == st.session_state.get("conversation_id") else ""
        if st.button(
            f"{item.title}{current}", key=f"open_{item.id}", use_container_width=True
        ):
            _open_conversation(item)

st.title(f"投资助手 · {active_name}" if active_name else "投资助手")
if st.session_state.get("notice"):
    st.info(st.session_state.notice)
    st.session_state.notice = ""

if not st.session_state.messages:
    st.caption(WELCOME)

_render_history()

if not active_code:
    st.info(
        "在问题里带上公司名或代码即可，例如「紫金矿业靠什么赚钱」；"
        "也可以直接问「今天大盘怎么看」。"
    )

if prompt and not active_code:
    st.warning("没认出你问的是哪家公司，请在问题里带上公司名或六位代码。")

if prompt and active_code:
    conversation_id = _ensure_conversation(
        active_code, active_name or active_code, prompt
    )
    st.session_state.messages.append({"role": "user", "text": prompt})
    STORE.append(conversation_id, "user", {"text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    agent = _agent_for(active_code)
    if agent is None:
        error = st.session_state.get("agent_error") or "缺少模型配置"
        text = f"助手不可用：{error}"
        st.session_state.messages.append({"role": "error", "text": text})
        STORE.append(conversation_id, "error", {"text": text})
        with st.chat_message("assistant"):
            st.error(text)
    else:
        start = agent.history_size()
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
                            agent, meter_slot, estimate_tokens(streamed["text"])
                        )
                elif kind == "discard":
                    # 这一轮其实是取数，先把草稿清掉
                    streamed["text"] = ""
                    placeholder.markdown("_正在取数…_")
                    _render_context_meter(agent, meter_slot)
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
                    _render_context_meter(agent, meter_slot)

            try:
                answer = agent.ask_stream(prompt, emit)
            except Exception as error:  # noqa: BLE001 - 失败要留在对话里
                placeholder.empty()
                steps.empty()
                text = f"研究失败：{error}"
                st.error(text)
                st.session_state.messages.append({"role": "error", "text": text})
                STORE.append(conversation_id, "error", {"text": text})
                _render_context_meter(agent, meter_slot)
            else:
                message = _answer_message(answer)
                message["raw"] = agent.history_since(start)
                steps.empty()
                with placeholder.container():
                    _render_assistant(message)
                st.session_state.messages.append(message)
                STORE.append(conversation_id, "assistant", message)
                # 收尾再画一次：去掉流式草稿的临时增量，显示落库后的真实占用
                _render_context_meter(agent, meter_slot)
