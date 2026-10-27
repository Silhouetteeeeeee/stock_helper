"""原生前端的 HTTP 服务：Starlette + SSE，页面文件在 ``frontend/``。

和 Streamlit 页面（``assistant/webapp.py``）共用 ``assistant`` 包的会话、
路由和工具，只是把渲染层换成了原生页面。浏览器只拿得到回答和来源，
API key 始终留在服务端。

启动：``python -m assistant.api``（默认 127.0.0.1:8502）。
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from assistant.agent import ResearchAgent, build_client, estimate_tokens
from assistant.env import load_env
from assistant.research_memory import MemoryError, SECTIONS, default_store
from assistant.store import ConversationStore, build_title
from assistant.tools import (
    MARKET_CODE,
    MARKET_NAME,
    find_company_in_text,
    is_market_question,
    refers_to_company,
)

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
RECENT_LIMIT = 50
METER_INTERVAL = 0.4

load_env()
STORE = ConversationStore()
MEMORY = default_store()
AGENTS: dict[int, ResearchAgent] = {}
AGENT_CODE: dict[int, str | None] = {}


def meta_for(cid: int | None):
    if not cid:
        return None
    for item in STORE.recent(limit=200):
        if item.id == cid:
            return item
    return None


def raw_history(cid: int) -> list[dict]:
    raw: list[dict] = []
    for message in STORE.messages(cid):
        if message.get("role") == "assistant":
            raw.extend(message.get("raw") or [])
    return raw


def agent_for(cid: int, code: str | None) -> ResearchAgent:
    cached = AGENTS.get(cid)
    if cached is not None and AGENT_CODE.get(cid) == code:
        return cached
    default_company = None if code == MARKET_CODE else code
    agent = ResearchAgent(build_client(), default_company=default_company)
    agent.restore_history(raw_history(cid))
    AGENTS[cid] = agent
    AGENT_CODE[cid] = code
    return agent


def route_question(code, name, question):
    """和 webapp 一致：提公司就以公司为准；大盘问题不绑公司。"""
    mentioned = find_company_in_text(question)
    if mentioned:
        new_code, new_name = mentioned
        return new_code, new_name or new_code, new_code != code
    if is_market_question(question):
        keeps = code not in (None, MARKET_CODE) and refers_to_company(question)
        if not keeps:
            return MARKET_CODE, MARKET_NAME, code != MARKET_CODE
    return code, name, False


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_answer(cid: int | None, question: str) -> StreamingResponse:
    events: queue.Queue = queue.Queue()
    holder = {"cid": cid}

    def work() -> None:
        current = holder["cid"]
        try:
            meta = meta_for(current)
            code = meta.primary_code if meta else None
            name = (meta.primary_name or meta.primary_code) if meta else ""
            new_code, new_name, reset = route_question(code, name, question)
            notice = ""
            if reset:
                current = None
                if code:
                    notice = f"已切换到 {new_name}（{new_code}），开始新对话。"
            if not new_code:
                events.put({"type": "warning", "text": "没认出你问的是哪家公司，请在问题里带上公司名或六位代码。"})
                return
            if current is None:
                current = STORE.create(build_title(new_name, question), new_code, new_name)
                holder["cid"] = current
            events.put({
                "type": "state",
                "conversation_id": current,
                "code": new_code,
                "name": new_name,
                "notice": notice,
            })
            STORE.append(current, "user", {"text": question})
            agent = agent_for(current, new_code)
            start = agent.history_size()
            events.put({"type": "meter", "usage": agent.context_usage()})

            draft = {"text": "", "last_meter": 0.0}

            def emit(event: dict) -> None:
                kind = event.get("type")
                if kind == "text":
                    delta = event.get("delta") or ""
                    draft["text"] += delta
                    events.put({"type": "text", "delta": delta})
                    now = time.monotonic()
                    if now - draft["last_meter"] >= METER_INTERVAL:
                        draft["last_meter"] = now
                        try:
                            usage = agent.context_usage(estimate_tokens(draft["text"]))
                        except Exception:  # noqa: BLE001 - 占用显示失败不影响回答
                            usage = None
                        if usage:
                            events.put({"type": "meter", "usage": usage})
                elif kind == "discard":
                    draft["text"] = ""
                    events.put({"type": "discard"})
                    events.put({"type": "meter", "usage": agent.context_usage()})
                elif kind == "tool":
                    events.put({
                        "type": "tool",
                        "name": event.get("name"),
                        "company": event.get("company"),
                        "ok": bool(event.get("ok")),
                        "cached": bool(event.get("cached")),
                        "duration_ms": int(event.get("duration_ms") or 0),
                    })
                    events.put({"type": "meter", "usage": agent.context_usage()})

            try:
                answer = agent.ask_stream(question, emit)
            except Exception as error:  # noqa: BLE001 - 失败留在对话里
                text = f"研究失败：{error}"
                STORE.append(current, "error", {"text": text})
                events.put({"type": "error", "text": text, "conversation_id": current})
                return

            sources = [
                source.to_dict() if hasattr(source, "to_dict") else dict(source)
                for source in (answer.citations or [])
            ]
            issues = [
                str(issue.get("message") or issue.get("type") or "")
                for issue in (answer.issues or [])
            ][:2]
            message = {
                "role": "assistant",
                "text": answer.text or "",
                "sources": sources,
                "trace": list(answer.trace or []),
                "issues": [issue for issue in issues if issue],
            }
            persisted = dict(message)
            persisted["raw"] = agent.history_since(start)
            STORE.append(current, "assistant", persisted)
            events.put({
                "type": "done",
                "conversation_id": current,
                "title": build_title(new_name, question),
                "message": message,
                "usage": agent.context_usage(),
            })
        except Exception as error:  # noqa: BLE001 - 启动失败等
            events.put({
                "type": "error",
                "text": f"助手不可用：{error}",
                "conversation_id": holder["cid"],
            })
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def generate():
        while True:
            event = events.get()
            if event is None:
                break
            yield sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def health(request):
    return Response("ok")


async def conversations(request):
    query = request.query_params.get("query", "")
    items = STORE.recent(query=query, limit=RECENT_LIMIT)
    return JSONResponse([
        {
            "id": item.id,
            "title": item.title,
            "name": item.primary_name or item.primary_code or "",
            "code": item.primary_code or "",
            "count": item.message_count,
            "updated": item.updated_at,
        }
        for item in items
    ])


def evidence_for(cid: int, source_id: str) -> dict | None:
    """在已保存的原始工具消息里找回这条来源对应的取数结果。

    工具消息本身就带着完整 data 和 sources，回放时按来源 id 扫一遍即可；
    同一来源被重复取过时取最后一次（前面的可能已被替换成缓存 stub）。
    """
    found = None
    for message in STORE.messages(cid):
        if message.get("role") != "assistant":
            continue
        for item in message.get("raw") or []:
            if item.get("role") != "tool":
                continue
            try:
                content = json.loads(item.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(content, dict):
                continue
            sources = content.get("sources") or []
            if any(
                isinstance(source, dict) and str(source.get("id") or "") == source_id
                for source in sources
            ):
                found = content
    return found


def clean_message(message: dict) -> dict:
    role = message.get("role")
    out = {"role": role, "text": message.get("text", "")}
    if role == "assistant":
        out["sources"] = message.get("sources") or []
        trace = message.get("trace")
        if isinstance(trace, list):
            out["trace"] = trace
            out["trace_count"] = len(trace)
        else:
            out["trace"] = []
            out["trace_count"] = int(trace or 0)
        out["issues"] = message.get("issues") or []
    return out


async def conversation(request):
    cid = int(request.path_params["cid"])
    meta = meta_for(cid)
    if meta is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # 打开历史对话时也把上下文占用带上，占用环不用等下一次提问才出现
    try:
        usage = agent_for(cid, meta.primary_code).context_usage()
    except Exception:  # noqa: BLE001 - 占用算不出来不影响读对话
        usage = None
    return JSONResponse({
        "id": cid,
        "title": meta.title,
        "code": meta.primary_code or "",
        "name": meta.primary_name or meta.primary_code or "",
        "messages": [clean_message(message) for message in STORE.messages(cid)],
        "usage": usage,
    })


async def source_evidence(request):
    """来源详情侧栏的「依据」：这条来源对应的工具返回的完整数据。"""
    cid = int(request.path_params["cid"])
    source_id = (request.query_params.get("source_id") or "").strip()
    if not source_id:
        return JSONResponse({"error": "missing source_id"}, status_code=400)
    if meta_for(cid) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "source_id": source_id,
        "evidence": evidence_for(cid, source_id),
    })


async def delete_conversation(request):
    cid = int(request.path_params["cid"])
    STORE.delete(cid)
    AGENTS.pop(cid, None)
    AGENT_CODE.pop(cid, None)
    return JSONResponse({"ok": True})


async def ask(request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "bad request"}, status_code=400)
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "empty question"}, status_code=400)
    raw_cid = body.get("conversation_id")
    try:
        cid = int(raw_cid) if raw_cid else None
    except (TypeError, ValueError):
        cid = None
    return stream_answer(cid, question)


async def research_companies(request):
    query = request.query_params.get("query", "")
    return JSONResponse({"companies": MEMORY.companies(query=query)})


async def research_company(request):
    code = str(request.path_params.get("code") or "")
    if not re.fullmatch(r"\d{6}", code):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        limit = int(request.query_params.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 500))
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    status = request.query_params.get("status", "")
    section = request.query_params.get("section", "")
    if section:
        if section not in SECTIONS:
            return JSONResponse({"error": "unknown section"}, status_code=400)
        try:
            payload = MEMORY.read(
                code, section, status=status, limit=limit, offset=offset,
            )
        except MemoryError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(payload)
    detail = MEMORY.detail(
        code, status=status,
        limit=limit if "limit" in request.query_params else None,
    )
    if detail is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(detail)


app = Starlette(routes=[
    Route("/health", health),
    Route("/api/conversations", conversations),
    Route("/api/conversations/{cid:int}", conversation),
    Route("/api/conversations/{cid:int}/evidence", source_evidence),
    Route("/api/conversations/{cid:int}/delete", delete_conversation, methods=["POST"]),
    Route("/api/ask", ask, methods=["POST"]),
    Route("/api/research/companies", research_companies),
    Route("/api/research/companies/{code}", research_company),
    # 页面与静态资源放最后：前面的 API 路由先匹配，剩下的都交给 frontend/
    Mount("/", app=StaticFiles(directory=FRONTEND, html=True)),
])


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="投资助手原生前端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8502)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
