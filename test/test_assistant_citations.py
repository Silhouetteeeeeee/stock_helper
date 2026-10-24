"""引用校验：合法/未知 id、去重顺序、无引用却报数字。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.citations import validate_citations
from assistant.models import Source

FIN = Source(
    id="finance:605499:2026-06-30",
    label="财报", detail="东鹏饮料 2026-06-30 财务快照",
    as_of="2026-06-30", fetched_at="2026-10-24 15:57:57",
)
SEG = Source(
    id="segments:605499:2026-06-30:product",
    label="主营构成·产品", detail="东鹏饮料 2026-06-30 按产品",
    as_of="2026-06-30", fetched_at="2026-10-24 15:57:57",
)
REGISTRY = {FIN.id: FIN, SEG.id: SEG}


def test_valid_and_unknown() -> None:
    text = (
        "营收 124.4 亿[[finance:605499:2026-06-30]]，"
        "能量饮料占 71.8%[[segments:605499:2026-06-30:product]]。"
        "净利增长[[made-up-id]]。"
    )
    clean, used, issues = validate_citations(text, REGISTRY)
    assert "[[finance:605499:2026-06-30]]" in clean
    assert "[[segments:605499:2026-06-30:product]]" in clean
    assert "[[made-up-id]]" not in clean and "[?]" in clean
    assert [source.id for source in used] == [FIN.id, SEG.id]
    assert issues == [{"type": "unknown_citation", "id": "made-up-id"}], issues


def test_dedupe_order() -> None:
    text = "A[[" + SEG.id + "]] B"
    clean, used, issues = validate_citations(text, REGISTRY)
    assert len(used) == 1 and used[0].id == SEG.id
    assert issues == [], issues
    # 同一来源引用两次只登记一次
    twice = f"a[[{FIN.id}]] b[[{FIN.id}]]"
    _, used_twice, _ = validate_citations(twice, REGISTRY)
    assert [source.id for source in used_twice] == [FIN.id]


def test_uncited_numbers() -> None:
    clean, used, issues = validate_citations("营收 124.4 亿，净利 28.7 亿。", REGISTRY)
    assert used == []
    assert [issue["type"] for issue in issues] == ["uncited_numbers"], issues

    _, _, no_issue = validate_citations("公司卖能量饮料。", REGISTRY)
    assert no_issue == []


def test_plain_brackets_untouched() -> None:
    clean, used, issues = validate_citations("这是 [正常的] 方括号。", REGISTRY)
    assert clean == "这是 [正常的] 方括号。"
    assert used == [] and issues == []


def main() -> None:
    test_valid_and_unknown()
    test_dedupe_order()
    test_uncited_numbers()
    test_plain_brackets_untouched()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
