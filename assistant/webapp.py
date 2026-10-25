"""投资助手 Web 页面（Streamlit）。

独立进程运行，由行情窗口的 AI 按钮启动。当前标的不走网络接口，
而是读 ``data/assistant_state.json``：行情窗口每次切换标的都会更新，
页面在每次交互时重新读取。模型与工具复用 ``assistant`` 包，API key
只留在服务端，浏览器拿不到。

启动：``python -m streamlit run assistant/webapp.py``
"""

from __future__ import annotations

import streamlit as st

from assistant.agent import ResearchAgent, build_client
from assistant.env import load_env
from assistant.render import cite_text, source_lines
from assistant.selection import read_selection
from assistant.store import ConversationStore, build_title

WELCOME = "选一只股票后提问，例如「它靠什么赚钱」「毛利率为什么下滑」。回答会标注数据来源。"
MAX_ISSUES = 2
RECENT_LIMIT = 10

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
        ("window_code", None),
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
        agent = ResearchAgent(build_client(), default_company=code)
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
    body = cite_text(message.get("text", ""), sources)
    st.markdown(body or "_（没有返回正文）_")
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
selection = read_selection() or {}
state_stock = "" if selection.get("is_index") else (selection.get("code") or "")
state_name = (selection.get("name") or "").strip() or state_stock

with st.sidebar:
    st.markdown("### 当前标的")
    override = st.text_input(
        "股票代码",
        placeholder="例如 600519（留空跟随行情窗口）",
        max_chars=6,
        key="code_override",
    ).strip()
    if override and not (len(override) == 6 and override.isdigit()):
        st.warning("股票代码应为 6 位数字，本次忽略该输入。")
        override = ""

    if state_stock:
        st.caption(f"行情窗口：{state_name}（{state_stock}）")
    elif selection.get("is_index"):
        st.caption(f"行情窗口选中的是指数（{selection.get('name') or '--'}），没有财报数据")
    else:
        st.caption("行情窗口还没有选中股票")

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

    st.caption("回答里的 [n] 对应下方来源。数据来自本地已入库的财报、公告、调研与行情。")

# 只有行情窗口真的换了标的才切换公司；打开历史对话不会被下一次 rerun 冲掉
window_code = override or state_stock
window_name = override or state_name
if window_code != st.session_state.get("window_code"):
    st.session_state.window_code = window_code
    if window_code:
        _switch_company(window_code, window_name)

active_code = st.session_state.get("active_code")
active_name = st.session_state.get("active_name") or active_code

with st.sidebar:
    st.divider()
    st.markdown("### 最近对话")
    query = st.text_input(
        "搜索对话", placeholder="公司、代码或标题", key="conversation_query"
    ).strip()
    recent = STORE.recent(primary_code=active_code or None, query=query, limit=RECENT_LIMIT)
    if not recent:
        st.caption("还没有保存的对话。")
    for item in recent:
        current = " · 当前" if item.id == st.session_state.get("conversation_id") else ""
        if st.button(
            f"{item.title}{current}", key=f"open_{item.id}", use_container_width=True
        ):
            _open_conversation(item)

st.title("投资助手")
if active_code:
    st.caption(f"当前：{active_name}（{active_code}）")
if selection.get("is_index") and not override and active_code:
    st.warning("行情窗口当前选中的是指数，页面仍停留在上一只股票；选中股票或在上方输入代码即可切换。")
if st.session_state.get("notice"):
    st.info(st.session_state.notice)
    st.session_state.notice = ""

if not st.session_state.messages:
    st.caption(WELCOME)

_render_history()

if not active_code:
    st.info("先在行情窗口选中一只股票，或在左侧输入股票代码。")

prompt = st.chat_input("问一句，如：毛利率为什么下滑？", disabled=not active_code)
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
            with st.spinner("研究中…会调用多个数据工具，通常需要 10–30 秒"):
                try:
                    answer = agent.ask(prompt)
                except Exception as error:  # noqa: BLE001 - 失败要留在对话里
                    text = f"研究失败：{error}"
                    st.error(text)
                    st.session_state.messages.append({"role": "error", "text": text})
                    STORE.append(conversation_id, "error", {"text": text})
                else:
                    message = _answer_message(answer)
                    message["raw"] = agent.history_since(start)
                    _render_assistant(message)
                    st.session_state.messages.append(message)
                    STORE.append(conversation_id, "assistant", message)
