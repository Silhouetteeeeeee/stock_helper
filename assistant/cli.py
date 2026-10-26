"""命令行入口：一次性问答与交互模式。

    python -m assistant 605499 "它靠什么赚钱"
    python -m assistant 605499            # 交互模式
    python -m assistant                   # 交互模式，先用 :use 指定公司

正文原样打印；引用表与研究过程附在后面，便于核验。
"""

from __future__ import annotations

import json
import sys

from .agent import Answer, AssistantError, ResearchAgent, build_client


def format_answer(answer: Answer) -> str:
    """把回答排成三段：正文、引用表、研究过程。"""
    lines: list[str] = []
    if answer.text:
        lines.append(answer.text)
    elif any(issue.get("type") == "rounds_exhausted" for issue in answer.issues):
        lines.append("研究轮次用尽，没有得到最终回答。")

    if answer.citations:
        lines.append("")
        lines.append("引用：")
        for index, source in enumerate(answer.citations, 1):
            parts = [
                f"[{index}] {source.id}",
                source.label,
                source.detail,
                f"数据日期 {source.as_of}",
            ]
            if source.fetched_at:
                parts.append(f"抓取于 {source.fetched_at}")
            lines.append(" · ".join(parts))

    if answer.trace:
        lines.append("")
        lines.append("研究过程：")
        for step in answer.trace:
            arguments = json.dumps(step.get("arguments") or {}, ensure_ascii=False)
            if step.get("cached"):
                status = "复用缓存"
            else:
                status = "ok" if step.get("ok") else "失败"
            line = f"第 {step.get('round')} 轮 {step.get('name')}({arguments}) → {status}"
            if step.get("company"):
                line += f" · 公司 {step['company']}"
            if step.get("as_of"):
                line += f" · 数据 {step['as_of']}"
            if step.get("error"):
                line += f" · {step['error']}"
            lines.append(line)

    if answer.issues:
        lines.append("")
        lines.append("提示：")
        for issue in answer.issues:
            lines.append(f"- {_issue_text(issue)}")

    return "\n".join(lines)


def handle_command(agent: ResearchAgent, line: str, out=print) -> bool:
    """处理交互命令；返回 False 表示退出。

    不是命令的输入原样返回 True，交给调用方当问题处理。
    """
    text = (line or "").strip()
    if not text:
        return True
    if text in (":q", ":quit", ":exit"):
        return False
    if text == ":help":
        out(":use <代码或简称> 切换默认公司；:quit 退出")
        return True
    if text.startswith(":use"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            out("用法：:use 股票代码或简称")
            return True
        code = agent.use_company(parts[1].strip())
        out(f"默认公司已切换到 {code}")
        return True
    return True


def main(
    argv: list[str] | None = None,
    client=None,
    out=print,
    err=print,
) -> int:
    """运行命令行；返回进程退出码。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    company = argv[0] if argv else None
    question = " ".join(argv[1:]).strip()

    if client is None:
        try:
            client = build_client()
        except AssistantError as error:
            err(str(error))
            return 2

    agent = ResearchAgent(client, default_company=None)
    if company:
        try:
            agent.use_company(company)
        except AssistantError as error:
            err(str(error))
            return 2

    if company and question:
        try:
            answer = agent.ask(question)
        except AssistantError as error:
            err(str(error))
            return 1
        out(format_answer(answer))
        return 0

    return _interactive(agent, out=out, err=err)


def _interactive(agent: ResearchAgent, out=print, err=print) -> int:
    out("输入问题；:use <代码或简称> 切换默认公司，:quit 退出。")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0
        if not line:
            continue
        try:
            keep_going = handle_command(agent, line, out=out)
        except AssistantError as error:
            err(str(error))
            continue
        if not keep_going:
            return 0
        try:
            answer = agent.ask(line)
        except AssistantError as error:
            # 交互模式只放弃当前问题，会话继续
            err(str(error))
            continue
        out(format_answer(answer))


def _issue_text(issue: dict) -> str:
    kind = issue.get("type")
    if kind == "unknown_citation":
        return f"引用了不存在的来源：{issue.get('id')}"
    return issue.get("message") or kind or "未知提示"
