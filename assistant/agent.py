"""ReAct 循环：模型推理、调工具、看结果，直到能给出带引用的回答。

模型的 thinking 只用于调试；展示给用户的"研究过程"是工具步骤——
调了什么、参数、是否成功、数据日期。引用表由工具结果登记，模型只能
引用已登记的 id。
"""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass, replace

from openai import (
    APIConnectionError,
    APITimeoutError,
    DefaultHttpxClient,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from .citations import validate_citations
from .models import Source, ToolResult
from .prompts import SYSTEM_PROMPT
from .research_memory import default_store
from .tools import run_tool, schemas

DEFAULT_MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
MAX_ROUNDS = 6
TEMPERATURE = 0.3
TIMEOUT = 60.0
# 只重试网络/超时/5xx/限流这类临时故障；鉴权、参数错误重试也没用
RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
# 不参与会话缓存：skill/公司解析是元操作；估值、公告、业绩预告随行情和
# 披露变化，长会话里缓存住会拿到过期数据。
UNCACHED_TOOLS = frozenset({
    "load_skill",
    "resolve_company",
    "get_valuation",
    "get_announcements",
    "get_earnings_forecasts",
    "get_market_overview",
    "get_news_events",
    "get_sector_rotation",
    "screen_candidates",
    "build_strategy_plan",
    "save_daily_judgment",
    "review_research_reports",
    "list_research_memory",
    "read_research_memory",
    "append_research_record",
    "update_research_record",
    "record_conclusion_change",
})

# 上下文预算：按 DeepSeek 128K 留出 thinking、回答和本轮继续取数的空间。
# 估算用字符近似，宁可高估；可用环境变量覆盖。
CONTEXT_SOFT_TOKENS = int(os.environ.get("ASSISTANT_CONTEXT_SOFT_TOKENS", "55000"))
CONTEXT_HARD_TOKENS = int(os.environ.get("ASSISTANT_CONTEXT_HARD_TOKENS", "80000"))
KEEP_RECENT_TURNS = 2          # 压缩历史时保护的最近问答轮数
KEEP_RECENT_TOOL_ROUNDS = 2    # 轮内裁剪时保留的最近取数组数
SUMMARY_MAX_CHARS = 400
SUMMARY_INPUT_CHARS = 30_000
# 判据和公司解析是后续轮次还要反复用的元信息，轮内不裁
PROTECTED_TOOLS = frozenset({"load_skill", "resolve_company"})
SUMMARY_PROMPT = f"""你是股票研究对话的压缩器。把下面的历史问答压缩成不超过 {SUMMARY_MAX_CHARS} 字的中文摘要，供后续继续对话使用。

必须保留：提到的公司和六位代码、用户问过什么、得出的结论、关键数字及其后面的引用 id（例如 [[finance:601899:2026-06-30]]）、还没有解决的问题。
不要编造新信息，不要给投资建议，不要分点以外的任何标题。只输出摘要正文。"""


class AssistantError(Exception):
    """助手操作失败，消息可直接展示给用户。"""


@dataclass
class Answer:
    text: str
    citations: list[Source]
    trace: list[dict]
    issues: list[dict]
    rounds: int


def build_client() -> OpenAI:
    """按环境变量建 DeepSeek 客户端；缺 key 时给出可操作的提示。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise AssistantError("请先设置环境变量 DEEPSEEK_API_KEY")
    return OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=TIMEOUT,
        # SDK 自己默认重试两次，会和 _create 的重试叠成好几轮
        max_retries=0,
        # 和东财接口一样直连，绕开本机系统代理
        http_client=DefaultHttpxClient(trust_env=False),
    )


class ResearchAgent:
    """一次会话的助手：保存多轮消息、默认公司与引用注册表。"""

    def __init__(
        self,
        client,
        default_company: str | None = None,
        model: str = DEFAULT_MODEL,
        max_rounds: int = MAX_ROUNDS,
        memory=None,
    ) -> None:
        self.client = client
        self.default_company = default_company
        self.model = model
        self.max_rounds = max_rounds
        self._memory = memory
        self._history: list[dict] = []
        self._registry: dict[str, Source] = {}
        # 会话级取数缓存：同一公司、同一参数只保留一份完整结果，重复调用不再联网
        self._tool_cache: dict[tuple[str, str, str], ToolResult] = {}
        # tool_call_id -> 工具名与参数；用来给历史里的 tool 消息找回缓存 key
        self._calls: dict[str, dict] = {}
        # 缓存 key -> 还带着完整 data 的 tool 消息；重复调用时把旧的换成 stub
        self._tool_messages: dict[tuple[str, str, str], list[dict]] = {}
        # 较早轮次的摘要；只在内存里，重开对话时按完整历史重新压缩
        self._summary = ""

    def use_company(self, company: str) -> str:
        """把默认公司切到 ``company``，返回解析出的六位代码。"""
        result = run_tool("resolve_company", {"name_or_code": company}, None)
        if not result.ok:
            raise AssistantError(result.error or f"无法识别公司：{company}")
        self.default_company = result.data["code"]
        return self.default_company

    def history_size(self) -> int:
        """当前原始消息条数，用来截取一轮新增的消息。"""
        return len(self._history)

    def history_since(self, start: int) -> list[dict]:
        """取 ``start`` 之后的原始消息副本，供持久化保存。"""
        return copy.deepcopy(self._history[start:])

    def restore_history(self, messages: list[dict]) -> None:
        """回放已保存的原始消息，重建引用注册表和取数缓存。

        注册表不单独存：工具结果本来就带着完整来源，扫一遍就能恢复，
        续聊时模型引用上一轮的来源依然能解析成 citation。缓存也一样，
        回放出来的完整结果顺手播种，重开对话后第一次再调同一工具直接复用。
        """
        self._history = copy.deepcopy(list(messages))
        self._registry = {}
        self._tool_cache = {}
        self._summary = ""
        self._reindex()
        for message in self._history:
            if message.get("role") == "tool":
                for source in _payload_sources(_payload(message)):
                    self._registry[source.id] = source

        for (name, _arguments, _company), items in self._tool_messages.items():
            if name in UNCACHED_TOOLS:
                continue
            payload = _payload(items[-1])
            self._tool_cache[(name, _arguments, _company)] = ToolResult(
                ok=True,
                company=payload.get("company"),
                as_of=payload.get("as_of"),
                sources=tuple(_payload_sources(payload)),
                data=payload.get("data"),
            )

    def _reindex(self) -> None:
        """按当前历史重建 call_id 索引与「还带完整 data 的 tool 消息」索引。"""
        self._calls = {}
        self._tool_messages = {}
        for message in self._history:
            role = message.get("role")
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    call_id = call.get("id")
                    function = call.get("function") or {}
                    if call_id and function.get("name"):
                        self._calls[str(call_id)] = function
                continue
            if role != "tool":
                continue
            function = self._calls.get(str(message.get("tool_call_id") or ""))
            if function is None:
                continue
            payload = _payload(message)
            if not payload.get("ok") or payload.get("data") is None:
                continue
            name = str(function.get("name") or "")
            if not name:
                continue
            arguments = _parse_arguments(function.get("arguments"))
            key = self._cache_key(name, arguments, payload.get("company"))
            self._tool_messages.setdefault(key, []).append(message)

    def _run_tool(self, name: str, arguments: dict) -> ToolResult:
        """执行一个取数工具；同一会话里相同公司、相同参数的结果直接复用。

        命中时返回完整 data（``cached`` 只表示这次没有联网）：调用方紧接着
        会把上下文里上一次的同参数结果换成 stub，保证一份数据只留最新的一份。
        失败结果不缓存，下次调用仍会重新取数。
        """
        if name in UNCACHED_TOOLS:
            return run_tool(name, arguments, self.default_company)
        if not isinstance(arguments, dict):
            arguments = {}
        key = self._cache_key(name, arguments)
        cached = self._tool_cache.get(key)
        if cached is not None:
            return replace(cached, cached=True)
        result = run_tool(name, arguments, self.default_company)
        if result.ok:
            self._tool_cache[key] = result
            normalized = self._cache_key(name, arguments, result.company)
            if normalized != key:
                self._tool_cache[normalized] = result
        return result

    def _cache_key(
        self, name: str, arguments: dict, company: str | None = None
    ) -> tuple[str, str, str]:
        """缓存 key：工具名 + 去掉 company 的参数 + 公司。

        公司优先用显式传入的解析结果，其次参数里的值，最后默认公司。
        历史消息用 payload 里的 company 重建 key，避免回放时对不上。
        """
        arguments = arguments if isinstance(arguments, dict) else {}
        resolved = str(company or arguments.get("company") or self.default_company or "")
        return (name, _canonical_arguments(arguments), resolved)

    def _remember_tool_message(
        self, name: str, arguments: dict, result: ToolResult, message: dict
    ) -> None:
        """登记最新一份完整结果，并把此前同参数的结果换成 stub。

        这样上下文里每个 (工具, 参数, 公司) 只保留一份 data；旧的和新的
        共享同一组 sources，引用不受影响。
        """
        key = self._cache_key(name, arguments, result.company)
        for old in self._tool_messages.get(key, ()):
            if old is not message:
                _stub_tool_message(old, superseded=True)
        self._tool_messages.setdefault(key, []).append(message)

    def _register_calls(self, tool_calls: list[dict]) -> None:
        for call in tool_calls:
            call_id = call.get("id")
            function = call.get("function") or {}
            if call_id and function.get("name"):
                self._calls[str(call_id)] = function

    # ------------------------------------------------------------------
    # 上下文管理
    # ------------------------------------------------------------------
    def context_usage(self, extra_tokens: int = 0) -> dict:
        """当前会话的上下文占用，供页面画占用环。

        分母用压缩阈值（软预算）：超过它，下一次提问会先丢弃较早轮次的
        工具取数结果。``percent`` 如实反映超额，``ratio`` 截到 1，只用于
        画环，不参与任何判断。``extra_tokens`` 用来把正在流式输出的草稿
        也算进去，环才能在回答过程中跟着涨。
        """
        budget = max(1, CONTEXT_SOFT_TOKENS)
        tokens = self._estimate() + max(0, int(extra_tokens or 0))
        share = tokens / budget
        if share >= 1:
            level = "over"
        elif share >= 0.8:
            level = "warn"
        else:
            level = "normal"
        return {
            "tokens": tokens,
            "budget_tokens": CONTEXT_SOFT_TOKENS,
            "hard_tokens": CONTEXT_HARD_TOKENS,
            "percent": round(share * 100, 1),
            "ratio": round(min(share, 1.0), 4),
            "level": level,
        }

    def _estimate(self, question: str = "") -> int:
        """粗估下一次请求的输入 token；工具 schema 也要算进去。"""
        parts = [self._system_text(), json.dumps(schemas(), ensure_ascii=False)]
        parts.extend(_message_text(message) for message in self._history)
        if question:
            parts.append(question)
        tokens = sum(estimate_tokens(part) for part in parts)
        return tokens + 4 * (len(self._history) + (2 if question else 1))

    def _prepare_context(self, question: str) -> list[dict]:
        """提问前压缩历史：先删旧轮次的工具链，仍超预算再做摘要。

        工具链是上下文里的大头（一次基本面分析 60–100k 字符），而且历史
        轮次的最终回答已经落库、引用注册表也在内存里，整组删掉不影响
        引用解析。摘要只在删完工具链仍然超硬阈值时才会调用模型。
        """
        issues: list[dict] = []
        if self._estimate(question) <= CONTEXT_SOFT_TOKENS:
            return issues
        self._drop_historical_tool_groups(question)

        tries = 0
        while self._estimate(question) > CONTEXT_HARD_TOKENS and tries < 4:
            tries += 1
            turns = self._summary_candidates()
            if not turns:
                break
            chunk = _take_summary_chunk(self._history, turns, SUMMARY_INPUT_CHARS)
            if not chunk:
                break
            summary = ""
            try:
                summary = self._summarize(chunk)
            except Exception as error:  # noqa: BLE001 - 压缩失败不能挡住回答
                issues.append({
                    "type": "context_summary_failed",
                    "message": f"较早对话摘要失败，已直接省略：{error}",
                })
            # 摘要失败也照样删：确定性压缩总比把上下文顶爆好
            self._remove_turns(chunk)
            if summary:
                self._summary = summary
        return issues

    def _drop_historical_tool_groups(self, question: str) -> None:
        """从最旧的轮次开始整组删除工具调用，直到回到软阈值以内。

        只删「assistant(tool_calls) + 它名下的全部 tool 消息」，配对完整，
        剩下的历史仍然是合法的 user/assistant 序列。用户问题和最终回答都
        保留；最新一轮的取数结果排在最后才动，够用就停。
        """
        groups = self._tool_groups(0, len(self._history))
        if not groups:
            return
        total = self._estimate(question)
        drop: set[int] = set()
        freed = 0
        for group_start, group_end in groups:
            if total - freed <= CONTEXT_SOFT_TOKENS:
                break
            freed += sum(
                estimate_tokens(_message_text(message))
                for message in self._history[group_start:group_end]
            )
            drop.update(range(group_start, group_end))
        if not drop:
            return
        self._history = [
            message for index, message in enumerate(self._history)
            if index not in drop
        ]
        self._reindex()

    def _protected_turn_start(self) -> int:
        """最近 KEEP_RECENT_TURNS 轮问答的起点；不足这么多轮时保护全部。"""
        users = [
            index for index, message in enumerate(self._history)
            if message.get("role") == "user"
        ]
        if len(users) <= KEEP_RECENT_TURNS:
            return 0
        return users[-KEEP_RECENT_TURNS]

    def _tool_groups(self, start: int, end: int) -> list[tuple[int, int]]:
        """找出 ``[start, end)`` 内的工具调用组：assistant + 紧随的 tool 消息。"""
        groups: list[tuple[int, int]] = []
        index = start
        while index < end:
            message = self._history[index]
            if message.get("role") == "assistant" and message.get("tool_calls"):
                stop = index + 1
                while stop < end and self._history[stop].get("role") == "tool":
                    stop += 1
                groups.append((index, stop))
                index = stop
            else:
                index += 1
        return groups

    def _summary_candidates(self) -> list[tuple[int, int]]:
        """可被摘要的旧问答轮次（不含最近保护的几轮）。"""
        protected = self._protected_turn_start()
        if protected <= 0:
            return []
        users = [
            index for index, message in enumerate(self._history)
            if index < protected and message.get("role") == "user"
        ]
        turns: list[tuple[int, int]] = []
        for position, start in enumerate(users):
            end = users[position + 1] if position + 1 < len(users) else protected
            turns.append((start, end))
        return turns

    def _remove_turns(self, turns: list[tuple[int, int]]) -> None:
        drop: set[int] = set()
        for start, end in turns:
            drop.update(range(start, end))
        if not drop:
            return
        self._history = [
            message for index, message in enumerate(self._history)
            if index not in drop
        ]
        self._reindex()

    def _summarize(self, turns: list[tuple[int, int]]) -> str:
        """把旧问答交给模型压缩；不含工具结果，只保留用户问题和最终回答。"""
        lines: list[str] = []
        if self._summary:
            lines.append("此前摘要：\n" + self._summary)
        for start, end in turns:
            for message in self._history[start:end]:
                role = message.get("role")
                content = (message.get("content") or "").strip()
                if not content:
                    continue
                # 单条回答也可能很长；截断后仍然足够概括结论
                if len(content) > 8_000:
                    content = content[:8_000] + "…（后文省略）"
                if role == "user":
                    lines.append("用户问：" + content)
                elif role == "assistant":
                    lines.append("回答：" + content)
        if not lines:
            return ""
        transcript = "\n\n".join(lines)
        if len(transcript) > SUMMARY_INPUT_CHARS:
            transcript = transcript[:SUMMARY_INPUT_CHARS] + "\n…（后文省略）"
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0.0,
            max_tokens=800,
        )
        text = (getattr(response.choices[0].message, "content", None) or "").strip()
        if not text:
            raise AssistantError("模型没有返回摘要")
        return text

    def _current_turn_start(self) -> int:
        for index in range(len(self._history) - 1, -1, -1):
            if self._history[index].get("role") == "user":
                return index
        return -1

    def _trim_current_context(self) -> None:
        """轮内裁剪：当前问题自己撑爆预算时，stub 掉较早几轮的取数结果。

        当前问题的消息还没落库，删掉会丢 sources，所以只把 data 换成 stub；
        最新一轮永远保留，先保最近两组，还不够再放宽到一组。
        """
        if self._estimate() <= CONTEXT_HARD_TOKENS:
            return
        start = self._current_turn_start()
        groups = self._tool_groups(start + 1, len(self._history))
        if len(groups) < 2:
            return

        total = self._estimate()
        for keep in (KEEP_RECENT_TOOL_ROUNDS, 1):
            if len(groups) <= keep:
                continue
            boundary = groups[-keep][0]
            for group_start, group_end in groups:
                if group_start >= boundary:
                    break
                for message in self._history[group_start:group_end]:
                    if message.get("role") != "tool":
                        continue
                    function = self._calls.get(
                        str(message.get("tool_call_id") or "")
                    ) or {}
                    if function.get("name") in PROTECTED_TOOLS:
                        continue
                    before = estimate_tokens(_message_text(message))
                    if not _stub_tool_message(message, superseded=False):
                        continue
                    total -= before - estimate_tokens(_message_text(message))
                    if total <= CONTEXT_HARD_TOKENS:
                        return

    def ask(self, question: str) -> Answer:
        """问一个问题，跑完整个循环后返回结构化回答。

        本轮失败时回滚这一轮的消息：交互模式放弃当前问题后，历史里不能
        留下一条没有回答的 user 消息，否则模型会把两个问题混在一起。
        """
        return self._converse(question, self._complete_blocking, None)

    def ask_stream(self, question: str, emit) -> Answer:
        """同 ``ask``，但把回答片段实时交给 ``emit``。

        事件：``{"type": "text", "delta"}`` 是回答正文；工具轮次结束会发
        ``{"type": "discard"}``，页面据此清掉这一轮先推的草稿；
        ``{"type": "tool", ...}`` 是每次取数完成的进度。
        """
        return self._converse(question, self._complete_streaming, emit)

    def _converse(self, question, complete, emit) -> Answer:
        issues: list[dict] = []
        # 提问前先把历史压回预算内：删旧轮次的工具链，必要时再摘要
        issues.extend(self._prepare_context(question))
        start = len(self._history)
        self._history.append({"role": "user", "content": question})
        notify = emit or (lambda event: None)
        trace: list[dict] = []
        text = ""
        rounds = 0

        try:
            while rounds < self.max_rounds:
                # 单次基本面分析自己就可能把上下文顶到上限，轮内也要裁
                self._trim_current_context()
                messages = [self._system_message()] + self._history
                reply = complete(messages, notify)
                rounds += 1
                content = reply.get("content") or ""
                tool_calls = list(reply.get("tool_calls") or [])
                entry: dict = {"role": "assistant", "content": content}
                if not tool_calls:
                    text = content
                    self._history.append(entry)
                    break

                # 这一轮其实是取数：已经推给页面的草稿需要清掉
                notify({"type": "discard"})
                entry["tool_calls"] = tool_calls
                self._history.append(entry)
                self._register_calls(tool_calls)
                for call in tool_calls:
                    name = call["function"].get("name") or "未知工具"
                    arguments = _parse_arguments(call["function"].get("arguments"))
                    started = time.monotonic()
                    try:
                        result = self._run_tool(name, arguments)
                    except Exception as error:  # noqa: BLE001 - 每个 id 都必须有回执
                        result = ToolResult(
                            ok=False, error=f"工具执行失败：{error}"
                        )
                    # 耗时跟着步骤一起落库；旧记录没有这个字段，页面按 0 显示
                    duration_ms = max(0, int((time.monotonic() - started) * 1000))
                    for source in result.sources:
                        self._registry[source.id] = source
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result.to_message(),
                    }
                    self._history.append(tool_message)
                    if result.ok and result.data is not None:
                        self._remember_tool_message(
                            name, arguments, result, tool_message
                        )
                    step = {
                        "round": rounds,
                        "name": name,
                        "arguments": arguments,
                        "ok": result.ok,
                        "company": result.company,
                        "as_of": result.as_of,
                        "error": result.error,
                        "sources": [source.id for source in result.sources],
                        "cached": result.cached,
                        "duration_ms": duration_ms,
                    }
                    trace.append(step)
                    notify({"type": "tool", **step})
            else:
                issues.append({
                    "type": "rounds_exhausted",
                    "message": f"研究轮次用尽（{self.max_rounds} 轮）",
                })
        except Exception:
            del self._history[start:]
            raise

        clean, citations, citation_issues = validate_citations(text, self._registry)
        issues.extend(citation_issues)
        return Answer(
            text=clean,
            citations=citations,
            trace=trace,
            issues=issues,
            rounds=rounds,
        )

    def _complete_blocking(self, messages, emit) -> dict:
        response = self._create(messages)
        message = response.choices[0].message
        return {
            "content": getattr(message, "content", None) or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in list(getattr(message, "tool_calls", None) or [])
            ],
        }

    def _complete_streaming(self, messages, emit) -> dict:
        """把流式 chunk 拼回一条普通 assistant 消息，工具参数分片也要接上。"""
        stream = self._create(messages, stream=True)
        parts: list[str] = []
        calls: dict[int, dict] = {}
        for chunk in stream:
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    parts.append(content)
                    emit({"type": "text", "delta": content})
                for call in getattr(delta, "tool_calls", None) or []:
                    index = getattr(call, "index", 0) or 0
                    slot = calls.setdefault(index, {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if getattr(call, "id", None):
                        slot["id"] = call.id
                    function = getattr(call, "function", None)
                    if function is None:
                        continue
                    if getattr(function, "name", None):
                        slot["function"]["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["function"]["arguments"] += function.arguments
        return {
            "content": "".join(parts),
            "tool_calls": [calls[index] for index in sorted(calls)],
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _memory_brief(self) -> str:
        """默认公司是六位代码时取研究档案摘要；读不到就当没有。"""
        code = str(self.default_company or "")
        if len(code) != 6 or not code.isdigit():
            return ""
        store = self._memory or default_store()
        try:
            return store.brief(code)
        except Exception:  # noqa: BLE001 - 档案读失败不能拖垮对话
            return ""

    def _system_text(self) -> str:
        company = self.default_company or "未指定（大盘/市场问题不需要公司）"
        text = f"{SYSTEM_PROMPT}\n\n当前默认公司：{company}"
        brief = self._memory_brief()
        if brief:
            text += f"\n\n{brief}"
        if self._summary:
            text += f"\n\n此前对话摘要（较早轮次已省略）：\n{self._summary}"
        return text

    def _system_message(self) -> dict:
        return {"role": "system", "content": self._system_text()}

    def _create(self, messages: list[dict], stream: bool = False):
        """调用模型；临时故障重试一次，其余错误直接放弃。"""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": schemas(),
            "tool_choice": "auto",
            "temperature": TEMPERATURE,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
        if stream:
            kwargs["stream"] = True
        last_error: Exception | None = None
        for _ in range(2):
            try:
                return self.client.chat.completions.create(**kwargs)
            except RETRYABLE as error:
                last_error = error
            except Exception as error:  # noqa: BLE001 - 鉴权/参数错误重试也没用
                raise AssistantError(f"模型接口失败：{error}") from error
        raise AssistantError(f"模型接口失败：{last_error}")


def _source_from_dict(item: dict) -> Source:
    """容忍缺字段的旧数据，构造 Source 时只取认识的键。"""
    return Source(
        id=str(item.get("id", "")),
        label=str(item.get("label", "")),
        detail=str(item.get("detail", "")),
        as_of=str(item.get("as_of", "")),
        fetched_at=str(item.get("fetched_at", "")),
        url=str(item.get("url", "")),
    )


def _parse_arguments(raw) -> dict:
    """工具参数是 JSON 字符串；坏数据按空参数处理，让工具自己报缺参。"""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _canonical_arguments(arguments: dict) -> str:
    """缓存 key 里的参数串：公司已单独入 key，其余参数按字典序序列化。"""
    return json.dumps(
        {key: value for key, value in arguments.items() if key != "company"},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def estimate_tokens(text: str) -> int:
    """按字符类别粗估 token 数：中文 0.7、其余 0.3，宁可高估。"""
    if not text:
        return 0
    cjk = 0
    for char in text:
        code = ord(char)
        if 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            cjk += 1
    other = len(text) - cjk
    return int(cjk * 0.7 + other * 0.3) + 1


def _message_text(message: dict) -> str:
    """一条消息会占用的文本：content + 工具名与参数。"""
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(str(function.get("name") or ""))
        arguments = function.get("arguments")
        parts.append(
            arguments if isinstance(arguments, str)
            else json.dumps(arguments or {}, ensure_ascii=False)
        )
    return "".join(parts)


def _payload(message: dict) -> dict:
    try:
        payload = json.loads(message.get("content") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_sources(payload: dict) -> list[Source]:
    return [
        _source_from_dict(item)
        for item in payload.get("sources") or []
        if isinstance(item, dict) and item.get("id")
    ]


def _stub_tool_message(message: dict, *, superseded: bool) -> bool:
    """把一条带 data 的 tool 消息压成只留来源的 stub，返回是否改动。

    保留 ``tool_call_id`` 配对和 sources id：引用仍能解析；data 是上下文
    体积的大头，换掉以后同一参数在历史里只留最新的一份。
    """
    payload = _payload(message)
    if payload.get("data") is None:
        return False
    stub: dict = {
        "ok": payload.get("ok", True),
        "sources": [
            {
                key: item.get(key, "")
                for key in ("id", "label", "detail", "as_of")
            }
            for item in payload.get("sources") or []
            if isinstance(item, dict) and item.get("id")
        ],
    }
    for key in ("company", "as_of"):
        if payload.get(key) is not None:
            stub[key] = payload[key]
    if superseded:
        stub["superseded"] = True
        stub["note"] = "同参数结果已由后续调用取代，完整数据见后文"
    else:
        stub["omitted"] = True
        stub["note"] = "完整数据已从上下文移除，需要精确数值请重新调用本工具"
    message["content"] = json.dumps(stub, ensure_ascii=False)
    return True


def _take_summary_chunk(
    history: list[dict], turns: list[tuple[int, int]], limit_chars: int
) -> list[tuple[int, int]]:
    """按字符预算取最旧的几轮做摘要；一次至少取一轮。"""
    chunk: list[tuple[int, int]] = []
    used = 0
    for turn in turns:
        size = sum(
            len(_message_text(message))
            for message in history[turn[0]:turn[1]]
        )
        if chunk and used + size > limit_chars:
            break
        chunk.append(turn)
        used += size
    return chunk
