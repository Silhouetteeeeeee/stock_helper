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

WELCOME = "选一只股票后提问，例如「它靠什么赚钱」「毛利率为什么下滑」。回答会标注数据来源。"
MAX_ISSUES = 2

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
        ("notice", ""),
    ):
        if key not in st.session_state:
            st.session_state[key] = default


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.agent = None
    st.session_state.agent_code = None
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


def _agent_for(code: str):
    agent = st.session_state.get("agent")
    if agent is not None and st.session_state.get("agent_code") == code:
        return agent
    try:
        agent = ResearchAgent(build_client(), default_company=code)
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
def _render_assistant(message: dict) -> None:
    sources = message.get("sources") or []
    body = cite_text(message.get("text", ""), sources)
    st.markdown(body or "_（没有返回正文）_")
    lines = source_lines(sources)
    if lines:
        st.caption("来源：")
        for line in lines:
            st.caption(line)
    steps = int(message.get("trace") or 0)
    if steps:
        st.caption(f"研究过程：{steps} 次取数")
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
        "trace": len(getattr(answer, "trace", None) or []),
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

    if st.button("清空对话", use_container_width=True):
        _reset_conversation()
        st.session_state.notice = "对话已清空。"
        st.rerun()

    st.divider()
    st.caption("回答里的 [n] 对应下方来源。数据来自本地已入库的财报、公告、调研与行情。")

if override:
    _switch_company(override, override)
elif state_stock:
    _switch_company(state_stock, state_name)

active_code = st.session_state.get("active_code")
active_name = st.session_state.get("active_name") or active_code

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
    agent = _agent_for(active_code)
    if agent is None:
        error = st.session_state.get("agent_error") or "缺少模型配置"
        text = f"助手不可用：{error}"
        st.session_state.messages.append({"role": "error", "text": text})
        with st.chat_message("assistant"):
            st.error(text)
    else:
        st.session_state.messages.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("研究中…会调用多个数据工具，通常需要 10–30 秒"):
                try:
                    answer = agent.ask(prompt)
                except Exception as error:  # noqa: BLE001 - 失败要留在对话里
                    text = f"研究失败：{error}"
                    st.error(text)
                    st.session_state.messages.append({"role": "error", "text": text})
                else:
                    message = _answer_message(answer)
                    _render_assistant(message)
                    st.session_state.messages.append(message)
