"""公司概况验证：字段清洗、经营范围截断、缓存与降级。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant.company_profile as cp

CODE = "605499"
PAYLOAD = {
    "jbzl": [{
        "SECURITY_NAME_ABBR": "东鹏饮料",
        "EM2016": "食品饮料-饮料-软饮料",
        "ORG_PROFILE": "  东鹏饮料(集团)股份有限公司，专注为消费者提供高品质能量饮品。 ",
        "BUSINESS_SCOPE": "食品生产；饮料生产；" + "包装材料销售；" * 40,
        "EMP_NUM": 12376,
    }]
}


def test_payload() -> None:
    data = cp.profile_from_payload(PAYLOAD, CODE)
    assert data["code"] == CODE
    assert data["name"] == "东鹏饮料"
    assert data["industry"] == "食品饮料-饮料-软饮料"
    assert data["profile"].startswith("东鹏饮料(集团)")
    assert data["profile"].endswith("。")  # 首尾空白被去掉
    assert data["employees"] == 12376
    assert len(data["scope"]) <= cp.SCOPE_MAX_CHARS + 1
    assert data["scope"].endswith("…")

    try:
        cp.profile_from_payload({"jbzl": []}, CODE)
    except cp.ProfileError as error:
        assert "没有" in str(error)
    else:
        raise AssertionError("空数据应抛 ProfileError")


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="assistant_profile_")
    cp.DB_PATH = os.path.join(tmpdir, "stock.db")
    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return cp.profile_from_payload(PAYLOAD, code)

    cp.fetch_profile = fake_fetch  # type: ignore[assignment]

    first = cp.ensure_profile(CODE)
    assert calls["fetch"] == 1 and first["stale"] is False, (calls, first)
    assert first["data"]["name"] == "东鹏饮料"

    cp.ensure_profile(CODE)  # 缓存期内不再抓
    assert calls["fetch"] == 1, calls

    cp.ensure_profile(CODE, force=True)
    assert calls["fetch"] == 2, calls

    def broken(code: str) -> dict:
        raise cp.ProfileError("接口超时")

    cp.fetch_profile = broken  # type: ignore[assignment]
    stale = cp.ensure_profile(CODE, force=True)
    assert stale["stale"] is True and stale["data"]["name"] == "东鹏饮料", stale

    try:
        cp.ensure_profile("600000")
    except cp.ProfileError as error:
        assert "接口超时" in str(error)
    else:
        raise AssertionError("无缓存且抓取失败时应抛 ProfileError")


def test_build() -> None:
    original = cp.ensure_profile
    row = cp.profile_from_payload(PAYLOAD, CODE)
    row["fetched_at"] = "2026-10-24 09:31:02"
    cp.ensure_profile = lambda code, **kwargs: {"data": row, "stale": False}  # type: ignore[assignment]
    try:
        profile = cp.build_profile(CODE)
    finally:
        cp.ensure_profile = original  # type: ignore[assignment]
    assert profile["available"] is True
    assert profile["code"] == CODE and profile["name"] == "东鹏饮料"
    assert profile["sources"]["东方财富 F10 公司概况"]
    # 引用里的抓取时间要来自真实抓取，不能由 as_of 拼一个出来
    assert profile["fetched_at"] == "2026-10-24 09:31:02", profile


def main() -> None:
    test_payload()
    test_cache()
    test_build()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
