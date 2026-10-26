"""ReAct 循环：模型推理、调工具、看结果，直到能给出带引用的回答。

模型的 thinking 只用于调试；展示给用户的"研究过程"是工具步骤——
调了什么、参数、是否成功、数据日期。引用表由工具结果登记，模型只能
引用已登记的 id。
"""

from __future__ import annotations

import copy
import json
import os
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
})


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
    ) -> None:
        self.client = client
        self.default_company = default_company
        self.model = model
        self.max_rounds = max_rounds
        self._history: list[dict] = []
        self._registry: dict[str, Source] = {}
        # 会话级取数缓存：同一公司、同一参数只保留一份完整结果，重复调用不再重发 data
        self._tool_cache: dict[tuple[str, str, str], ToolResult] = {}

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
        """回放已保存的原始消息，并从工具结果里重建引用注册表。

        注册表不单独存：工具结果本来就带着完整来源，扫一遍就能恢复，
        续聊时模型引用上一轮的来源依然能解析成 citation。
        """
        self._history = copy.deepcopy(list(messages))
        self._registry = {}
        self._tool_cache = {}
        calls: dict[str, dict] = {}
        for message in self._history:
            role = message.get("role")
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    call_id = call.get("id")
                    function = call.get("function") or {}
                    if call_id and function.get("name"):
                        calls[str(call_id)] = function
                continue
            if role != "tool":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            sources = [
                _source_from_dict(item)
                for item in payload.get("sources") or []
                if isinstance(item, dict) and item.get("id")
            ]
            for source in sources:
                self._registry[source.id] = source

            # 用回放出来的完整结果给缓存播种：重开对话后第一次再调同一工具
            # 直接复用，不必重新取数、也不会再往历史里塞一份重复的 data。
            function = calls.get(str(message.get("tool_call_id") or ""))
            if function is None or payload.get("cached"):
                continue
            name = str(function.get("name") or "")
            if not name or name in UNCACHED_TOOLS:
                continue
            if not payload.get("ok") or payload.get("data") is None:
                continue
            arguments = _parse_arguments(function.get("arguments"))
            self._tool_cache[self._cache_key(name, arguments)] = ToolResult(
                ok=True,
                company=payload.get("company"),
                as_of=payload.get("as_of"),
                sources=tuple(sources),
                data=payload.get("data"),
            )

    def _run_tool(self, name: str, arguments: dict) -> ToolResult:
        """执行一个取数工具；同一会话里相同公司、相同参数的结果直接复用。

        命中时只把 sources 与 ``cached`` 标记交回模型，完整 data 仍留在
        之前的历史消息里，避免同一份财报 JSON 在上下文里反复堆积。
        失败结果不缓存，下次调用仍会重新取数。
        """
        if name in UNCACHED_TOOLS:
            return run_tool(name, arguments, self.default_company)
        if not isinstance(arguments, dict):
            arguments = {}
        key = self._cache_key(name, arguments)
        cached = self._tool_cache.get(key)
        if cached is not None:
            return replace(cached, data=None, cached=True)
        result = run_tool(name, arguments, self.default_company)
        if result.ok:
            self._tool_cache[key] = result
        return result

    def _cache_key(self, name: str, arguments: dict) -> tuple[str, str, str]:
        """缓存 key：工具名 + 去掉 company 的参数 + 公司。

        公司取参数里的显式值，没有就用默认公司；两次调用只要这两处一致
        就视为同一份数据。
        """
        arguments = arguments if isinstance(arguments, dict) else {}
        company = str(arguments.get("company") or self.default_company or "")
        return (name, _canonical_arguments(arguments), company)

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
        start = len(self._history)
        self._history.append({"role": "user", "content": question})
        notify = emit or (lambda event: None)
        trace: list[dict] = []
        issues: list[dict] = []
        text = ""
        rounds = 0

        try:
            while rounds < self.max_rounds:
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
                for call in tool_calls:
                    name = call["function"].get("name") or "未知工具"
                    arguments = _parse_arguments(call["function"].get("arguments"))
                    try:
                        result = self._run_tool(name, arguments)
                    except Exception as error:  # noqa: BLE001 - 每个 id 都必须有回执
                        result = ToolResult(
                            ok=False, error=f"工具执行失败：{error}"
                        )
                    for source in result.sources:
                        self._registry[source.id] = source
                    self._history.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result.to_message(),
                    })
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
    def _system_message(self) -> dict:
        company = self.default_company or "尚未指定"
        return {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n当前默认公司：{company}",
        }

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
