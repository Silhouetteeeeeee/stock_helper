"""原生页面接口：来源详情侧栏的依据数据。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.api as api
from assistant import research_memory as api_memory
from assistant.store import ConversationStore

SOURCE = {
    "id": "fin:601899:2026-06-30",
    "label": "财报",
    "detail": "东财",
    "as_of": "2026-06-30",
    "fetched_at": "2026-10-27 10:00:00",
    "url": "",
}


def tool_message(call_id: str, data: dict) -> dict:
    payload = {"ok": True, "sources": [SOURCE], "data": data}
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def request(cid: int, source_id: str):
    return SimpleNamespace(
        path_params={"cid": cid},
        query_params={"source_id": source_id},
    )


def test_evidence() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = ConversationStore(Path(folder) / "assistant.db")
        cid = store.create("紫金矿业 · 靠什么赚钱", "601899", "紫金矿业")
        store.append(cid, "user", {"text": "靠什么赚钱"})
        store.append(cid, "assistant", {
            "role": "assistant",
            "text": "第一轮回答",
            "sources": [SOURCE],
            "trace": [],
            "issues": [],
            "raw": [
                tool_message("call_1", {"revenue_yi": 2834.53, "gross_margin": 20.4}),
                # 非 dict 的坏数据不能把查找打断
                {"role": "tool", "content": "not json"},
            ],
        })
        # 同一来源第二次取数：以后一次的完整结果为准
        store.append(cid, "assistant", {
            "role": "assistant",
            "text": "第二轮回答",
            "sources": [SOURCE],
            "trace": [],
            "issues": [],
            "raw": [
                tool_message("call_2", {"revenue_yi": 2900.0, "gross_margin": 21.0}),
                {"role": "tool", "content": json.dumps(
                    {"ok": True, "sources": [{"id": "other:1"}], "data": {}},
                    ensure_ascii=False,
                )},
            ],
        })

        original = api.STORE
        api.STORE = store
        try:
            evidence = api.evidence_for(cid, SOURCE["id"])
            assert evidence is not None
            assert evidence["data"]["revenue_yi"] == 2900.0
            assert evidence["sources"][0]["label"] == "财报"
            assert api.evidence_for(cid, "missing") is None

            response = asyncio.run(api.source_evidence(request(cid, SOURCE["id"])))
            payload = json.loads(response.body)
            assert payload["source_id"] == SOURCE["id"]
            assert payload["evidence"]["data"]["gross_margin"] == 21.0

            response = asyncio.run(api.source_evidence(request(cid, "")))
            assert response.status_code == 400
            response = asyncio.run(api.source_evidence(request(999, SOURCE["id"])))
            assert response.status_code == 404
        finally:
            api.STORE = original


def test_research_endpoints() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = api_memory.ResearchMemoryStore(Path(folder) / "memory.db")
        store.ensure_company("605499", "东鹏饮料")
        store.append("605499", "facts", {
            "fact_type": "盈利能力",
            "content": "2025 年营业收入 208.75 亿元",
            "report_period": "2025 年度",
            "source_id": "finance:605499:2025-12-31",
        })
        store.append("605499", "conclusions", {
            "title": "增长来源", "statement": "增长由核心单品驱动",
            "based_on_fact_ids": ["F001"],
        })
        original = api.MEMORY
        api.MEMORY = store
        try:
            response = asyncio.run(api.research_companies(SimpleNamespace(query_params={})))
            payload = json.loads(response.body)
            assert payload["companies"][0]["code"] == "605499"
            assert payload["companies"][0]["name"] == "东鹏饮料"
            assert payload["companies"][0]["counts"]["facts"] == 1

            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "605499"}, query_params={},
            )))
            detail = json.loads(response.body)
            assert detail["facts"][0]["fact_id"] == "F001"
            assert detail["conclusions"][0]["conclusion_id"] == "G001"
            assert detail["changes"] == [] and detail["hypotheses"] == [] and detail["questions"] == []
            assert "last_reviewed_at" in detail["company"]
            assert "updated_at" in detail["company"]

            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "605499"}, query_params={"section": "facts", "limit": "1"},
            )))
            filtered = json.loads(response.body)
            assert filtered["section"] == "facts" and filtered["total"] == 1

            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "605499"}, query_params={"status": "有效"},
            )))
            filtered_detail = json.loads(response.body)
            assert filtered_detail["conclusions"][0]["conclusion_id"] == "G001"
            assert filtered_detail["facts"] == []

            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "605499"}, query_params={"section": "nope"},
            )))
            assert response.status_code == 400
            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "999999"}, query_params={},
            )))
            assert response.status_code == 404
            response = asyncio.run(api.research_company(SimpleNamespace(
                path_params={"code": "abc"}, query_params={},
            )))
            assert response.status_code == 404

            paths = {route.path for route in api.app.routes if hasattr(route, "path")}
            assert "/api/research/companies" in paths
            assert "/api/research/companies/{code}" in paths
            for route in api.app.routes:
                if getattr(route, "path", "").startswith("/api/research/companies"):
                    assert "POST" not in getattr(route, "methods", set())
        finally:
            api.MEMORY = original


def main() -> None:
    test_evidence()
    test_research_endpoints()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
