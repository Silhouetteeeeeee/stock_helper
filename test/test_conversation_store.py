"""对话持久化：会话/消息存取、最近列表与历史回放。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_STATE_DIR = tempfile.mkdtemp(prefix="assistant_store_")
os.environ["MARKET_ASSISTANT_DB"] = str(Path(_STATE_DIR) / "assistant.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.agent import ResearchAgent
from assistant.models import Source
from assistant.store import ConversationStore, build_title

LIMIT = 30


class FakeClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_build_title() -> None:
    assert build_title("东鹏饮料", "靠什么赚钱") == "东鹏饮料 · 靠什么赚钱"
    long_question = "为" * 40
    title = build_title("东鹏饮料", long_question)
    assert title == "东鹏饮料 · " + ("为" * LIMIT) + "…", title
    # 名字为空时退化为问题本身
    assert build_title("", "毛利率为什么下滑") == "毛利率为什么下滑"


def test_store_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = ConversationStore(Path(folder) / "conv.db")
        first = store.create(build_title("东鹏饮料", "靠什么赚钱"), "605499", "东鹏饮料")
        second = store.create(build_title("贵州茅台", "渠道"), "600519", "贵州茅台")
        assert first != second

        # 空库也能正常查
        assert store.messages(first) == []

        seq1 = store.append(first, "user", {"text": "靠什么赚钱"})
        seq2 = store.append(first, "assistant", {
            "text": "卖能量饮料",
            "sources": [],
            "trace": 1,
            "issues": [],
            "raw": [{"role": "user", "content": "靠什么赚钱"}],
        })
        assert (seq1, seq2) == (1, 2), (seq1, seq2)

        messages = store.messages(first)
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[1]["text"] == "卖能量饮料"
        assert messages[1]["trace"] == 1
        assert messages[1]["raw"][0]["content"] == "靠什么赚钱"

        recent = store.recent()
        assert [item.id for item in recent] == [first, second], [item.id for item in recent]
        assert recent[0].title == "东鹏饮料 · 靠什么赚钱"
        assert recent[0].primary_name == "东鹏饮料"
        assert recent[0].message_count == 2

        assert [item.id for item in store.recent(primary_code="600519")] == [second]
        assert [item.id for item in store.recent(query="茅台")] == [second]
        assert [item.id for item in store.recent(query="605499")] == [first]
        assert store.recent(query="不存在") == []

        # 追加消息会把会话顶到最近
        store.append(second, "user", {"text": "渠道"})
        assert [item.id for item in store.recent()][0] == second

        # 删除后消息一起清掉
        store.delete(second)
        assert store.messages(second) == []
        assert [item.id for item in store.recent()] == [first]


def test_backup_on_reopen() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "conv.db"
        store = ConversationStore(path)
        store.create(build_title("东鹏饮料", "靠什么赚钱"), "605499", "东鹏饮料")
        # 第一次创建时没有旧库可备份
        assert not (Path(folder) / "backups").exists()

        ConversationStore(path)
        backups = list((Path(folder) / "backups").glob("conv-*.db"))
        assert len(backups) == 1, backups
        assert backups[0].stat().st_size > 0

        # 同一天再打开不重复备份
        ConversationStore(path)
        assert len(list((Path(folder) / "backups").glob("conv-*.db"))) == 1


def test_agent_restore_rebuilds_registry() -> None:
    source = Source(
        "finance:605499:2026-06-30",
        "财报",
        "东鹏饮料 2026-06-30",
        "2026-06-30",
        "2026-10-24 15:57:57",
    )
    raw = [
        {"role": "user", "content": "靠什么赚钱"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "get_finance", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": json.dumps(
                {"ok": True, "sources": [source.to_dict()], "data": {}},
                ensure_ascii=False,
            ),
        },
        {"role": "assistant", "content": "卖饮料[[finance:605499:2026-06-30]]"},
        # 坏消息不能让回放失败
        {"role": "tool", "tool_call_id": "c2", "content": "not-json"},
    ]

    agent = ResearchAgent(client=object(), default_company="605499")
    agent.restore_history(raw)
    assert agent.history_size() == len(raw)

    # 续问引用旧来源仍能解析出 citation
    agent.client = FakeClient([response(content="对[[finance:605499:2026-06-30]]")])
    answer = agent.ask("继续")
    assert [item.id for item in answer.citations] == [source.id]
    assert agent.history_size() == len(raw) + 2

    # 回放的是一份拷贝，改原列表不影响助手历史
    size = agent.history_size()
    raw.clear()
    assert agent.history_size() == size


def main() -> None:
    test_build_title()
    test_store_roundtrip()
    test_backup_on_reopen()
    test_agent_restore_rebuilds_registry()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
