"""8 个 skill 的真实问答回归（手动运行，不进入默认测试）。

每个 skill 两个问题，只做确定性硬校验：

- 未知引用：回答里出现 ``[?]``，或 issues 里有 ``unknown_citation``；
- 跨公司引用：引用 id 里的公司代码不在该题的允许范围；
- skill 路由：期望的 skill 是否通过 ``load_skill`` 加载了。

不做语义评分，也不判断"回答好不好"。结果写到 ``tmp/eval/<时间戳>/``，
其中 ``report.md`` 是汇总表，``answers/`` 下是每题的回答与来源。

用法::

    python test/eval_skills.py
    python test/eval_skills.py --only moat,valuation
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from assistant.agent import AssistantError, ResearchAgent, build_client
from assistant.audit import audit_answer
from assistant.env import load_env

# (skill, 默认公司, 允许引用的公司, 问题)
QUESTIONS = [
    ("business_model", "601899", ["601899"], "紫金矿业靠什么赚钱？"),
    ("business_model", "605499", ["605499"], "东鹏饮料靠什么赚钱？"),
    ("moat", "601899", ["601899"], "紫金矿业的竞争优势是什么？能持续多久？"),
    ("moat", "605499", ["605499", "600519"], "东鹏饮料的护城河跟贵州茅台比怎么样？"),
    ("operating_history", "601899", ["601899"], "紫金矿业近几年经营质量怎么样？"),
    ("operating_history", "605499", ["605499"], "东鹏饮料上市以来经营质量有什么变化？"),
    ("financial_safety", "601899", ["601899"], "紫金矿业的财务安全吗？"),
    ("financial_safety", "605499", ["605499"], "东鹏饮料的负债和现金流安全吗？"),
    ("growth_outlook", "601899", ["601899"], "紫金矿业未来增长靠什么？"),
    ("growth_outlook", "605499", ["605499"], "东鹏饮料未来两年增长靠什么？"),
    ("management", "601899", ["601899"], "紫金矿业的管理层和股东回报怎么样？"),
    ("management", "605499", ["605499"], "东鹏饮料的股东结构和股东回报怎么样？"),
    ("valuation", "601899", ["601899"], "紫金矿业现在估值贵不贵？"),
    ("valuation", "605499", ["605499"], "东鹏饮料的估值处在什么分位？"),
    ("margin_of_safety", "601899", ["601899"], "紫金矿业现在有安全边际吗？"),
    ("margin_of_safety", "605499", ["605499"], "东鹏饮料现在的价格有安全边际吗？"),
]


def _safe_error(error: Exception) -> str:
    """异常信息里不能带出 API key。"""
    secret = os.environ.get("DEEPSEEK_API_KEY", "")
    text = f"{type(error).__name__}: {error}"
    return text.replace(secret, "***") if secret else text


def _cell(text: str, limit: int = 22) -> str:
    text = (text or "").replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _answer_file(folder: Path, index: int, skill: str, question: str, row: dict) -> None:
    lines = [
        f"# {index}. {skill}：{question}",
        "",
        f"- 默认公司：{row['company']}",
        f"- 允许引用：{', '.join(row['allowed'])}",
        f"- 判定：{'通过' if row['audit']['ok'] else '失败'}",
        f"- 载入 skill：{', '.join(row['audit']['skills_loaded']) or '无'}",
        f"- 引用数：{row['audit']['citations']}；未知引用：{len(row['audit']['unknown_citations'])}；"
        f"跨公司：{len(row['audit']['cross_company'])}",
        f"- 耗时：{row['seconds']:.1f}s；轮次：{row['rounds']}",
        "",
        "## 回答",
        "",
        row["text"] or "（空）",
        "",
        "## 来源",
        "",
    ]
    for source in row["sources"]:
        lines.append(
            f"- `{source.get('id')}` · {source.get('label')} · "
            f"{source.get('detail')} · {source.get('as_of')}"
        )
    lines.extend(["", "## 研究过程", ""])
    for step in row["trace"]:
        lines.append(
            f"- {step.get('round')}. {step.get('name')} · "
            f"{step.get('company') or '--'} · {step.get('as_of') or '无日期'} · "
            f"{'成功' if step.get('ok') else '失败'}"
        )
    if row["audit"]["unknown_citations"]:
        lines.extend(["", "## 未知引用", ""])
        lines.extend(f"- `{bad}`" for bad in row["audit"]["unknown_citations"])
    (folder / "answers").mkdir(parents=True, exist_ok=True)
    path = folder / "answers" / f"{index:02d}-{skill}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_report(folder: Path, rows: list[dict], model: str) -> Path:
    passed = sum(1 for row in rows if row["audit"]["ok"])
    unknown = sum(len(row["audit"]["unknown_citations"]) for row in rows)
    cross = sum(len(row["audit"]["cross_company"]) for row in rows)
    missing = sum(len(row["audit"]["skills_missing"]) for row in rows)
    lines = [
        f"# Skill 回归报告 {rows and rows[0]['started'] or ''}",
        "",
        f"- 模型：`{model}`",
        f"- 题目：{len(rows)}；硬校验通过：{passed}；失败：{len(rows) - passed}",
        f"- 未知引用 id：{unknown}；跨公司引用：{cross}；缺失 skill：{missing}",
        "- 本报告只检查引用与路由，不评价回答的语义正确性。",
        "",
        "| # | skill | 问题 | 期望 | 载入 | 额外 | 引用 | 未知 | 跨公司 | 判定 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        audit = row["audit"]
        lines.append(
            f"| {row['index']} | {row['skill']} | {_cell(row['question'])} | "
            f"{', '.join(audit['skills_expected'])} | "
            f"{', '.join(audit['skills_loaded']) or '—'} | "
            f"{', '.join(audit['skills_extra']) or '—'} | "
            f"{audit['citations']} | {len(audit['unknown_citations'])} | "
            f"{len(audit['cross_company'])} | "
            f"{'通过' if audit['ok'] else '失败'} |"
        )
    failures = [row for row in rows if not row["audit"]["ok"]]
    if failures:
        lines.extend(["", "## 失败明细", ""])
        for row in failures:
            reasons = []
            if row["audit"]["unknown_citations"]:
                reasons.append("未知引用：" + "、".join(row["audit"]["unknown_citations"]))
            if row["audit"]["cross_company"]:
                reasons.append("跨公司引用：" + "、".join(row["audit"]["cross_company"]))
            if row["audit"]["skills_missing"]:
                reasons.append("缺少 skill：" + "、".join(row["audit"]["skills_missing"]))
            if row.get("error"):
                reasons.append("执行失败：" + row["error"])
            lines.append(f"- {row['index']}. {row['skill']}：{'；'.join(reasons)}")
    path = folder / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="8 个 skill 的真实问答回归")
    parser.add_argument(
        "--only", default="",
        help="只跑指定 skill，逗号分隔，例如 moat,valuation",
    )
    args = parser.parse_args()
    only = {name.strip() for name in args.only.split(",") if name.strip()}

    load_env()
    try:
        client = build_client()
    except AssistantError as error:
        print(f"无法开始：{error}", flush=True)
        return 2

    probe = ResearchAgent(client)
    model = probe.model
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = ROOT / "tmp" / "eval" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "results.jsonl").write_text("", encoding="utf-8")

    selected = [item for item in QUESTIONS if not only or item[0] in only]
    rows: list[dict] = []
    for index, (skill, company, allowed, question) in enumerate(selected, 1):
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{index}/{len(selected)}] {skill} · {question}", flush=True)
        row = {
            "index": index, "skill": skill, "company": company,
            "allowed": allowed, "question": question, "started": started,
            "text": "", "sources": [], "trace": [], "issues": [], "rounds": 0,
            "seconds": 0.0, "error": "",
        }
        begin = time.monotonic()
        try:
            agent = ResearchAgent(client, default_company=company)
            answer = agent.ask(question)
            row.update({
                "text": answer.text,
                "sources": [source.to_dict() for source in answer.citations],
                "trace": answer.trace,
                "issues": answer.issues,
                "rounds": answer.rounds,
            })
        except Exception as error:  # noqa: BLE001 - 单题失败不影响其余题目
            row["error"] = _safe_error(error)
        row["seconds"] = time.monotonic() - begin
        row["audit"] = audit_answer(
            row, expected_skills=[skill], allowed_companies=allowed
        )
        rows.append(row)

        with (folder / "results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({k: v for k, v in row.items() if k != "text"},
                                    ensure_ascii=False) + "\n")
        _answer_file(folder, index, skill, question, row)

        audit = row["audit"]
        print(
            f"    载入={audit['skills_loaded'] or '无'} 额外={audit['skills_extra'] or '无'} "
            f"引用={audit['citations']} 未知={len(audit['unknown_citations'])} "
            f"跨公司={len(audit['cross_company'])} "
            f"{'OK' if audit['ok'] else 'FAIL'} ({row['seconds']:.1f}s)",
            flush=True,
        )
        if row["error"]:
            print(f"    错误：{row['error']}", flush=True)

    report = _write_report(folder, rows, model)
    passed = sum(1 for row in rows if row["audit"]["ok"])
    print(f"\n硬校验通过 {passed}/{len(rows)}；报告：{report}", flush=True)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
