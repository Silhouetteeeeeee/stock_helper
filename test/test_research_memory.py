from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.research_memory import MemoryError, ResearchMemoryStore


FACT = {
    "subject": "东鹏饮料",
    "fact_type": "盈利能力",
    "content": "2025 年营业收入 208.75 亿元，同比增长 31.80%",
    "metrics": [{"name": "营业收入", "value": 208.75, "unit": "亿元", "yoy": 31.8}],
    "report_period": "2025 年度",
    "published_at": "2026-03-31",
    "source_id": "finance:605499:2025-12-31",
    "source_label": "2025 年年度报告",
    "source_locator": "主要会计数据",
}


def fresh_store(folder: str) -> ResearchMemoryStore:
    return ResearchMemoryStore(Path(folder) / "research_memory.db")


def test_schema_and_fact_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        store.ensure_company("605499", "东鹏饮料")
        result = store.append("605499", "facts", FACT)
        assert result["created"] is True
        assert result["record"]["fact_id"] == "F001"
        assert result["record"]["verification_status"] == "已核验"
        assert result["counts"]["facts"] == 1

        read = store.read("605499", "facts")
        assert read["total"] == 1
        assert read["records"][0]["content"] == FACT["content"]
        assert read["company"]["name"] == "东鹏饮料"

        connection = sqlite3.connect(Path(folder) / "research_memory.db")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "idx_fact_company" in indexes
        connection.close()


def test_fact_dedupe_and_multi_metric_key() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        first = store.append("605499", "facts", FACT)
        duplicate = store.append("605499", "facts", dict(FACT))
        assert duplicate["created"] is False
        assert duplicate["record"]["fact_id"] == first["record"]["fact_id"]

        # 指标多一项 => 不同事实
        extra = dict(FACT)
        extra["metrics"] = FACT["metrics"] + [
            {"name": "归母净利润", "value": 44.15, "unit": "亿元", "yoy": 32.72}
        ]
        second = store.append("605499", "facts", extra)
        assert second["record"]["fact_id"] == "F002"

        # 定性事实：空白/全半角差异应归一化后去重
        qualitative = {
            "subject": "东鹏饮料", "fact_type": "战略", "report_period": "2025 年度",
            "source_id": "announcement:1",
            "content": "公司披露正在推进全国化布局",
        }
        store.append("605499", "facts", qualitative)
        again = dict(qualitative)
        again["content"] = "公司披露 正在推进全国化布局"
        result = store.append("605499", "facts", again)
        assert result["created"] is False

        # 指标事实：类型/报告期的空白和全半角差异也要归一化
        period_variant = dict(FACT)
        period_variant["fact_type"] = " 盈利能力 "
        period_variant["report_period"] = "2025年度"
        assert store.append("605499", "facts", period_variant)["created"] is False

        # 指标事实：数值的字符串/浮点写法不同也视为同一条
        value_variant = dict(FACT)
        value_variant["metrics"] = [
            {"name": " 营业收入 ", "value": "208.750", "unit": " 亿元 ", "yoy": 31.8}
        ]
        assert store.append("605499", "facts", value_variant)["created"] is False

        # 归一化不能把不同的数值合并
        different = dict(FACT)
        different["metrics"] = [
            {"name": "营业收入", "value": "208.76", "unit": "亿元", "yoy": 31.8}
        ]
        assert store.append("605499", "facts", different)["created"] is True


def test_fact_requires_source_and_period() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        for key in ("source_id", "report_period", "content"):
            broken = dict(FACT)
            broken.pop(key)
            try:
                store.append("605499", "facts", broken)
            except MemoryError:
                pass
            else:
                raise AssertionError(f"缺少 {key} 应该被拒绝")


def test_fact_rejects_oversize_record() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        too_long = dict(FACT, content="长" * 24000)
        try:
            store.append("605499", "facts", too_long)
        except MemoryError:
            pass
        else:
            raise AssertionError("单字段超过 20KB 应被拒绝")

        many_metrics = dict(FACT)
        many_metrics["metrics"] = [
            {"name": f"指标{index}", "value": index, "unit": "个"} for index in range(2500)
        ]
        try:
            store.append("605499", "facts", many_metrics)
        except MemoryError:
            pass
        else:
            raise AssertionError("单记录超过 50KB 应被拒绝")


def test_ids_are_per_company() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        assert store.append("605499", "facts", FACT)["record"]["fact_id"] == "F001"
        assert store.append("600519", "facts", dict(FACT, subject="贵州茅台"))["record"]["fact_id"] == "F001"
        assert len(store.companies()) == 2
        assert store.company("605499")["counts"]["facts"] == 1


def test_concurrent_appends_keep_all_records() -> None:
    import threading

    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        errors = []

        def work(index: int) -> None:
            for round_index in range(10):
                record = dict(FACT)
                record["metrics"] = [{"name": f"指标{index}-{round_index}", "value": index, "unit": "个"}]
                try:
                    store.append("605499", "facts", record)
                except Exception as error:  # noqa: BLE001
                    errors.append(error)

        threads = [threading.Thread(target=work, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert store.company("605499")["counts"]["facts"] == 40


HYPOTHESIS = {
    "statement": "全国市场扩张能够继续推动收入增长",
    "supporting_fact_ids": ["F001"],
    "assumptions": "渠道扩张不显著推高销售费用",
    "confidence": "中",
    "verification_method": "观察分区域收入增速、渠道覆盖和销售费用率",
    "falsification_condition": "新增区域增长持续放缓，且销售费用增长明显超过收入",
    "status": "待验证",
    "next_review_date": "2026-12-31",
}

QUESTION = {
    "question": "经营现金流为什么落后于净利润增长？",
    "importance": "高",
    "known_facts": ["F001"],
    "missing_evidence": "应收、存货、应付与合同负债的完整拆解",
    "next_action": "查现金流量表、资产负债表及相关附注",
    "resolution_criteria": "能用财务数据解释主要差异",
    "related_hypothesis_ids": [],
    "status": "待处理",
}


def test_hypothesis_requires_falsification_and_fact_refs() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        store.append("605499", "facts", FACT)
        result = store.append("605499", "hypotheses", HYPOTHESIS)
        assert result["record"]["hypothesis_id"] == "H001"

        broken = dict(HYPOTHESIS)
        broken.pop("falsification_condition")
        try:
            store.append("605499", "hypotheses", broken)
        except MemoryError:
            pass
        else:
            raise AssertionError("缺少证伪条件应被拒绝")

        broken = dict(HYPOTHESIS, supporting_fact_ids=["F999"])
        try:
            store.append("605499", "hypotheses", broken)
        except MemoryError:
            pass
        else:
            raise AssertionError("引用不存在的事实应被拒绝")


def test_question_and_baseline_conclusion() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        store.append("605499", "facts", FACT)
        store.append("605499", "hypotheses", HYPOTHESIS)
        question = store.append("605499", "questions", QUESTION)
        assert question["record"]["question_id"] == "Q001"

        conclusion = store.append("605499", "conclusions", {
            "title": "增长来源",
            "statement": "增长由核心单品与电解质饮料共同驱动",
            "confidence": "中",
            "based_on_fact_ids": ["F001"],
            "based_on_hypothesis_ids": ["H001"],
        })
        assert conclusion["record"]["conclusion_id"] == "G001"
        assert conclusion["record"]["status"] == "有效"
        assert store.company("605499")["counts"]["conclusions"] == 1

        page = store.read("605499", "facts", limit=1, offset=0)
        assert page["total"] == 1 and len(page["records"]) == 1
        page = store.read("605499", "facts", limit=1, offset=1)
        assert page["total"] == 1 and page["records"] == []
        assert store.read("605499", "questions", status="待处理")["total"] == 1
        assert store.read("605499", "questions", status="已解决")["total"] == 0


def test_update_allowlists() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        store.append("605499", "facts", FACT)
        store.append("605499", "hypotheses", HYPOTHESIS)
        store.append("605499", "questions", QUESTION)
        store.append("605499", "conclusions", {"title": "增长来源", "statement": "由核心单品驱动"})

        updated = store.update("605499", "hypotheses", "H001", {"status": "部分支持"})
        assert updated["record"]["status"] == "部分支持"
        updated = store.update("605499", "facts", "F001", {"verification_status": "待核验"})
        assert updated["record"]["verification_status"] == "待核验"
        updated = store.update("605499", "questions", "Q001", {"status": "调查中"})
        assert updated["record"]["status"] == "调查中"

        for section, record_id in (
            ("hypotheses", "H001"),
            ("questions", "Q001"),
            ("conclusions", "G001"),
            ("changes", "C001"),
        ):
            try:
                store.update("605499", section, record_id, {"status": "有效"})
            except MemoryError:
                pass
            else:
                raise AssertionError(f"{section} 不应该允许更新结论或变化字段")

        try:
            store.append("605499", "changes", {})
        except MemoryError:
            pass
        else:
            raise AssertionError("append 不应该能写 changes")


def baseline(store: ResearchMemoryStore) -> None:
    store.append("605499", "facts", FACT)
    store.append("605499", "hypotheses", HYPOTHESIS)
    store.append("605499", "conclusions", {
        "title": "增长来源", "statement": "增长主要依赖核心单品",
        "based_on_fact_ids": ["F001"],
    })


def change_payload(**overrides) -> dict:
    payload = {
        "conclusion_id": "G001",
        "operation": "修正",
        "old_conclusion": "增长主要依赖核心单品",
        "new_conclusion": "增长由核心单品与电解质饮料共同驱动",
        "trigger_event": "2025 年年报披露产品结构",
        "trigger_date": "2026-03-31",
        "evidence_ids": ["F001"],
        "reason": "电解质饮料已形成一定规模的收入贡献",
        "impact": "增长预测按业务拆分",
        "review_status": "待后续财报验证",
    }
    payload.update(overrides)
    return payload


def test_record_change_operations() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        baseline(store)

        assert store.company("605499")["counts"]["changes"] == 0
        revised = store.record_change("605499", change_payload())
        assert revised["change"]["change_id"] == "C001"
        assert revised["change"]["from_conclusion_id"] == "G001"
        assert revised["change"]["to_conclusion_id"] == "G001"
        assert revised["to"]["statement"] == "增长由核心单品与电解质饮料共同驱动"
        assert revised["to"]["last_change_id"] == "C001"
        assert "F001" in revised["to"]["based_on_fact_ids"]
        assert revised["counts"]["changes"] == 1
        assert store.company("605499")["last_reviewed_at"]

        maintained = store.record_change("605499", change_payload(
            operation="维持",
            old_conclusion=revised["to"]["statement"],
            new_conclusion=revised["to"]["statement"],
            trigger_event="2026 年三季报复核",
            trigger_date="2026-10-27",
        ))
        assert maintained["to"]["statement"] == revised["to"]["statement"]
        assert maintained["change"]["operation"] == "维持"

        overturned = store.record_change("605499", change_payload(
            operation="推翻",
            old_conclusion=maintained["to"]["statement"],
            new_conclusion="增长由单一新品驱动",
            new_title="增长来源（修正）",
            trigger_event="新品收入占比超过 60%",
            trigger_date="2027-03-31",
        ))
        assert overturned["change"]["from_conclusion_id"] == "G001"
        assert overturned["change"]["to_conclusion_id"] == "G002"
        assert overturned["from"]["status"] == "已推翻"
        assert overturned["to"]["status"] == "有效"
        assert overturned["to"]["statement"] == "增长由单一新品驱动"


def test_record_change_validation() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        baseline(store)
        bad = [
            change_payload(operation="维持", new_conclusion="不一样"),
            change_payload(evidence_ids=[]),
            change_payload(evidence_ids=["F999"]),
            change_payload(conclusion_id="G999"),
        ]
        for payload in bad:
            try:
                store.record_change("605499", payload)
            except MemoryError:
                pass
            else:
                raise AssertionError(f"应拒绝：{payload}")


def test_changes_are_append_only() -> None:
    import sqlite3

    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        baseline(store)
        store.record_change("605499", change_payload())
        connection = sqlite3.connect(Path(folder) / "research_memory.db")
        for sql in (
            "UPDATE memory_change SET reason = 'x'",
            "DELETE FROM memory_change",
        ):
            try:
                connection.execute(sql)
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError(f"触发器没有阻止：{sql}")
        connection.close()


def test_change_rejects_overturned_conclusion() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        baseline(store)
        overturned = store.record_change("605499", change_payload(
            operation="推翻",
            new_conclusion="增长由单一新品驱动",
            new_title="增长来源（修正）",
            trigger_event="新品收入占比超过 60%",
        ))
        assert overturned["from"]["status"] == "已推翻"
        assert overturned["to"]["conclusion_id"] == "G002"

        for operation in ("补充", "修正", "维持", "推翻"):
            payload = change_payload(
                operation=operation,
                new_conclusion="另一个说法",
                new_title="增长来源",
                trigger_event="复核旧结论",
            )
            if operation == "维持":
                payload["new_conclusion"] = payload["old_conclusion"]
            try:
                store.record_change("605499", payload)
            except MemoryError as error:
                assert "已推翻" in str(error)
            else:
                raise AssertionError(f"已推翻的结论不允许 {operation}")

        archived = store.read("605499", "conclusions", "G001")["records"][0]
        assert archived["statement"] == "增长主要依赖核心单品"
        active = store.read("605499", "conclusions", "G002")["records"][0]
        assert active["statement"] == "增长由单一新品驱动"


def test_last_reviewed_only_moves_on_change() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        baseline(store)
        before = store.company("605499")["last_reviewed_at"]
        assert before is None
        store.append("605499", "facts", dict(FACT, metrics=[
            {"name": "归母净利润", "value": 44.15, "unit": "亿元"}
        ]))
        store.update("605499", "facts", "F001", {"verification_status": "待核验"})
        assert store.company("605499")["last_reviewed_at"] is None
        store.record_change("605499", change_payload())
        assert store.company("605499")["last_reviewed_at"]


def test_brief_order_and_cap() -> None:
    with tempfile.TemporaryDirectory() as folder:
        store = fresh_store(folder)
        store.ensure_company("605499", "东鹏饮料")
        store.append("605499", "facts", FACT)
        for index in range(7):
            store.append("605499", "conclusions", {
                "title": f"结论{index}", "statement": f"判断{index} " + "长" * 80,
            })
        brief = store.brief("605499")
        assert brief.startswith("研究档案：605499 东鹏饮料")
        assert "G007" in brief
        assert "G001" not in brief  # 最多 5 条活跃结论
        assert "另有 2 条" in brief
        assert "明细用 read_research_memory 读取" in brief
        assert "事实 1" in brief
        assert len(brief) <= 1200
        assert store.brief("600519") == "尚无研究档案"
        assert store.company("600519") is None  # 摘要不自动建档


def main() -> None:
    test_schema_and_fact_roundtrip()
    test_fact_dedupe_and_multi_metric_key()
    test_fact_requires_source_and_period()
    test_fact_rejects_oversize_record()
    test_ids_are_per_company()
    test_concurrent_appends_keep_all_records()
    test_hypothesis_requires_falsification_and_fact_refs()
    test_question_and_baseline_conclusion()
    test_update_allowlists()
    test_record_change_operations()
    test_record_change_validation()
    test_changes_are_append_only()
    test_change_rejects_overturned_conclusion()
    test_last_reviewed_only_moves_on_change()
    test_brief_order_and_cap()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
