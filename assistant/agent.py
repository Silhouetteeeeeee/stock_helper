"""ReAct 循环：模型推理、调工具、看结果，直到能给出带引用的回答。

模型的 thinking 只用于调试；展示给用户的"研究过程"是工具步骤——
调了什么、参数、是否成功、数据日期。引用表由工具结果登记，模型只能
引用已登记的 id。
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass

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
        for message in self._history:
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            for item in payload.get("sources") or []:
                if isinstance(item, dict) and item.get("id"):
                    self._registry[str(item["id"])] = _source_from_dict(item)

    def ask(self, question: str) -> Answer:
        """问一个问题，跑完整个循环后返回结构化回答。

        本轮失败时回滚这一轮的消息：交互模式放弃当前问题后，历史里不能
        留下一条没有回答的 user 消息，否则模型会把两个问题混在一起。
        """
        start = len(self._history)
        self._history.append({"role": "user", "content": question})
        trace: list[dict] = []
        issues: list[dict] = []
        text = ""
        rounds = 0

        try:
            while rounds < self.max_rounds:
                messages = [self._system_message()] + self._history
                response = self._create(messages)
                rounds += 1
                message = response.choices[0].message
                content = getattr(message, "content", None) or ""
                tool_calls = list(getattr(message, "tool_calls", None) or [])
                if not tool_calls:
                    text = content
                    self._history.append({"role": "assistant", "content": text})
                    break

                self._history.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                })
                for call in tool_calls:
                    name = call.function.name
                    arguments = _parse_arguments(call.function.arguments)
                    try:
                        result = run_tool(name, arguments, self.default_company)
                    except Exception as error:  # noqa: BLE001 - 每个 id 都必须有回执
                        result = ToolResult(
                            ok=False, error=f"工具执行失败：{error}"
                        )
                    for source in result.sources:
                        self._registry[source.id] = source
                    self._history.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result.to_message(),
                    })
                    trace.append({
                        "round": rounds,
                        "name": name,
                        "arguments": arguments,
                        "ok": result.ok,
                        "company": result.company,
                        "as_of": result.as_of,
                        "error": result.error,
                        "sources": [source.id for source in result.sources],
                    })
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

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _system_message(self) -> dict:
        company = self.default_company or "尚未指定"
        return {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n当前默认公司：{company}",
        }

    def _create(self, messages: list[dict]):
        """调用模型；临时故障重试一次，其余错误直接放弃。"""
        last_error: Exception | None = None
        for _ in range(2):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=schemas(),
                    tool_choice="auto",
                    temperature=TEMPERATURE,
                    extra_body={"thinking": {"type": "enabled"}},
                )
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
