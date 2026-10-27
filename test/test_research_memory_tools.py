from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.research_memory as memory_module
import assistant.tools as tools
from assistant.research_memory import ResearchMemoryStore


def test_memory_tools() -> None:
    with tempfile.TemporaryDirectory() as folder:
        original = memory_module._STORE
        memory_module._STORE = ResearchMemoryStore(Path(folder) / "memory.db")
        try:
            missing = tools.run_tool("list_research_memory", {}, default_company="605499")
            assert missing.ok and missing.data["archive"] is False
            assert "尚未建档" in missing.data["message"]

            appended = tools.run_tool("append_research_record", {
                "section": "facts",
                "record": {
                    "fact_type": "盈利能力",
                    "content": "2025 年营业收入 208.75 亿元",
                    "report_period": "2025 年度",
                    "source_id": "finance:605499:2025-12-31",
                    "source_locator": "主要会计数据",
                },
            }, default_company="605499")
            assert appended.ok
            assert appended.data["record"]["fact_id"] == "F001"
            assert appended.sources[0].id == "memory:605499:F001"

            # 显式 company 覆盖 default_company
            other = tools.run_tool("append_research_record", {
                "company": "600519",
                "section": "facts",
                "record": {
                    "fact_type": "盈利能力",
                    "content": "2025 年营业收入 1700 亿元",
                    "report_period": "2025 年度",
                    "source_id": "finance:600519:2025-12-31",
                },
            }, default_company="605499")
            assert other.ok and other.company == "600519"

            bad = tools.run_tool("append_research_record", {
                "section": "hypotheses",
                "record": {
                    "statement": "全国化", "supporting_fact_ids": ["F001"],
                    "confidence": "中", "verification_method": "看区域增速",
                    "status": "待验证",
                },
            }, default_company="605499")
            assert not bad.ok and "证伪" in (bad.error or "")

            hypothesis = tools.run_tool("append_research_record", {
                "section": "hypotheses",
                "record": {
                    "statement": "全国化继续推动收入增长",
                    "supporting_fact_ids": ["F001"],
                    "confidence": "中",
                    "verification_method": "观察分区域收入增速",
                    "falsification_condition": "区域增速连续两个季度放缓",
                    "status": "待验证",
                    "next_review_date": "2026-12-31",
                },
            }, default_company="605499")
            assert hypothesis.ok and hypothesis.data["record"]["hypothesis_id"] == "H001"

            conclusion = tools.run_tool("append_research_record", {
                "section": "conclusions",
                "record": {"title": "增长来源", "statement": "增长由核心单品驱动",
                           "based_on_fact_ids": ["F001"]},
            }, default_company="605499")
            assert conclusion.ok and conclusion.data["record"]["conclusion_id"] == "G001"

            read = tools.run_tool("read_research_memory", {
                "section": "facts", "record_id": "F001",
            }, default_company="605499")
            assert read.ok and read.data["total"] == 1
            assert read.sources[0].id == "memory:605499:F001"

            updated = tools.run_tool("update_research_record", {
                "section": "hypotheses", "record_id": "H001",
                "patch": {"status": "部分支持"},
            }, default_company="605499")
            assert updated.ok and updated.data["record"]["status"] == "部分支持"

            changed = tools.run_tool("record_conclusion_change", {
                "conclusion_id": "G001",
                "operation": "修正",
                "old_conclusion": "增长由核心单品驱动",
                "new_conclusion": "增长由核心单品与电解质饮料共同驱动",
                "trigger_event": "2025 年年报披露产品结构",
                "trigger_date": "2026-03-31",
                "evidence_ids": ["F001", "H001"],
                "reason": "电解质饮料已有规模收入",
                "impact": "按业务拆分增长预测",
            }, default_company="605499")
            assert changed.ok
            assert changed.data["change"]["change_id"] == "C001"
            assert changed.sources[0].id == "memory:605499:C001"

            listed = tools.run_tool("list_research_memory", {}, default_company="605499")
            assert listed.ok and listed.data["archive"] is True
            assert listed.data["counts"]["changes"] == 1
        finally:
            memory_module._STORE = original


def main() -> None:
    test_memory_tools()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
