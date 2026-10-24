"""命令行：输出排版、一次性模式、缺 key、交互命令。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.cli as cli
from assistant.agent import Answer
from assistant.models import Source

SOURCE = Source("finance:605499:2026-06-30", "财报", "东鹏饮料 2026-06-30",
                "2026-06-30", "2026-10-24 15:57:57")
ANSWER = Answer(
    text="能量饮料是主力[[finance:605499:2026-06-30]]。",
    citations=[SOURCE],
    trace=[{"round": 1, "name": "get_finance", "arguments": {},
            "ok": True, "company": "605499", "as_of": "2026-06-30",
            "error": None, "sources": [SOURCE.id]}],
    issues=[], rounds=2,
)


def test_format() -> None:
    text = cli.format_answer(ANSWER)
    assert "能量饮料是主力" in text
    assert "引用" in text and SOURCE.id in text
    assert "2026-10-24 15:57:57" in text
    assert "研究过程" in text and "get_finance" in text
    assert "第 1 轮" in text


def test_main_one_shot() -> None:
    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.default_company = None

        def use_company(self, company):
            self.default_company = company

        def ask(self, question):
            return ANSWER

    original = cli.ResearchAgent
    cli.ResearchAgent = FakeAgent  # type: ignore[assignment]
    lines = []
    try:
        code = cli.main(["605499", "它靠什么赚钱"], client=object(), out=lines.append)
    finally:
        cli.ResearchAgent = original  # type: ignore[assignment]
    assert code == 0
    joined = "\n".join(lines)
    assert "能量饮料是主力" in joined
    assert "引用" in joined


def test_missing_key() -> None:
    lines = []
    original = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        code = cli.main(["605499", "它靠什么赚钱"], client=None, err=lines.append)
    finally:
        if original is not None:
            os.environ["DEEPSEEK_API_KEY"] = original
    assert code == 2
    assert any("DEEPSEEK_API_KEY" in line for line in lines), lines


def test_load_env() -> None:
    import tempfile
    from pathlib import Path

    from assistant.env import load_env

    path = Path(tempfile.mkdtemp(prefix="assistant_env_")) / ".env"
    path.write_text(
        "# 注释\n\nFOO_TEST=bar\nQUOTED_TEST='带引号值'\nFOO_TEST=ignored\n",
        encoding="utf-8",
    )
    os.environ.pop("FOO_TEST", None)
    os.environ.pop("QUOTED_TEST", None)
    try:
        assert load_env(path) == 2
        assert os.environ["FOO_TEST"] == "bar"
        assert os.environ["QUOTED_TEST"] == "带引号值"
        # 已有环境变量不覆盖
        os.environ["FOO_TEST"] = "existing"
        assert load_env(path) == 0
        assert os.environ["FOO_TEST"] == "existing"
    finally:
        os.environ.pop("FOO_TEST", None)
        os.environ.pop("QUOTED_TEST", None)


def test_command() -> None:
    class FakeAgent:
        default_company = "605499"

        def use_company(self, company):
            self.default_company = company
            return company

    fake = FakeAgent()
    assert cli.handle_command(fake, ":use 600519") is True
    assert fake.default_company == "600519"
    assert cli.handle_command(fake, ":quit") is False
    assert cli.handle_command(fake, "问题") is True


def main() -> None:
    test_format()
    test_main_one_shot()
    test_missing_key()
    test_load_env()
    test_command()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
