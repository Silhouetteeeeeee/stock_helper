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


def main() -> None:
    test_evidence()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
