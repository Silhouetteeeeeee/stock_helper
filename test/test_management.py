"""管理层数据验证：分组、薪酬/持股口径、简历截断与本地缓存。

纯函数部分直接喂构造好的接口返回；缓存部分把数据库换到临时目录，
并替换 ``fetch_management``，不联网。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock.management as mg

CODE = "605499"
NAME = "东鹏饮料"

PERSON = {
    "name": "林木勤",
    "duty": "董事长,董事",
    "from": "2018-01-01",
    "to": "2029-10-09",
    "sex": "男",
    "brith_year": "1964",
    "age": "62岁",
    "record": "硕士",
    "salary": 5316100,
    "hold": 343422644.12,
    "directnum": 336435820.81,
    "indirectnum": 6986823.31,
    "rate": 46.7751,
    "directnum_rate": 45.8235,
    "indirectnum_rate": 0.9516,
    "report": "2026-10-10",
    "resume": "林木勤先生：中国国籍，无境外永久居留权，硕士学历。",
    "is_control": 1,
    "is_legal_person": 1,
}

PAYLOAD = {
    "status_code": 0,
    "status_msg": "",
    "data": {
        "family": {
            "family_name": "", "rate": "", "salary": "", "bonus": "",
            "directnum_rate": "", "indirectnum_rate": "", "list": [],
        },
        "directors": {
            "rate": 51.7624, "salary": 29975800, "bonus": 1434035461,
            "directnum_rate": 50.8084, "indirectnum_rate": 0.954,
            "list": [
                PERSON,
                {
                    "name": "张三", "duty": "董事", "from": "2025-04-02",
                    "to": "", "age": "70岁", "record": "本科",
                    "salary": 112500, "hold": "", "rate": "",
                    "report": "2026-10-10", "resume": "张三先生简历。",
                    "is_control": 0, "is_legal_person": 0,
                },
            ],
        },
        "manager": {
            "rate": 52.278, "salary": 20149800, "bonus": 1448593851,
            "directnum_rate": 51.324, "indirectnum_rate": 0.954,
            "list": [
                {
                    "name": "李四", "duty": "总经理", "from": "2019-04-08",
                    "to": "2029-10-09", "age": "56岁", "record": "硕士",
                    "salary": 4378400, "hold": 35314986.81,
                    "rate": 4.81, "report": "2026-10-10",
                    "resume": "李四先生简历。", "is_control": 0,
                    "is_legal_person": 0,
                },
            ],
        },
        "supervisors": {
            "rate": "", "salary": "", "bonus": "",
            "directnum_rate": "", "indirectnum_rate": "", "list": [],
        },
        "auditors": {
            "rate": "", "salary": 412500, "bonus": "",
            "directnum_rate": "", "indirectnum_rate": "",
            "list": [
                {
                    "name": "王五", "duty": "审计委员会主任委员",
                    "from": "2025-04-02", "to": "2029-10-09", "age": "70岁",
                    "record": "博士", "salary": 112500, "hold": "",
                    "rate": "", "report": "2026-10-10",
                    "resume": "王五先生简历。", "is_control": 0,
                    "is_legal_person": 0,
                },
            ],
        },
        "salary_year": "2025",
        "bonus_year": 2025,
    },
}


def test_payload() -> None:
    data = mg.management_from_payload(PAYLOAD, CODE)

    assert data["code"] == CODE, data
    assert data["as_of"] == "2026-10-10", data
    assert data["salary_year"] == "2025", data
    assert data["count"] == 4, data
    # 空分组不出现，家族持股为空时不展示
    assert list(data["groups"]) == ["directors", "manager", "auditors"], data["groups"]
    assert data["groups"]["directors"]["label"] == "董事会", data["groups"]
    assert data["groups"]["auditors"]["count"] == 1, data["groups"]

    directors = data["groups"]["directors"]
    assert directors["count"] == 2, directors
    assert directors["salary_total"] == 29975800, directors
    assert round(directors["hold_ratio"], 4) == 51.7624, directors

    leader = directors["members"][0]
    assert leader["name"] == "林木勤", leader
    assert leader["duties"] == ["董事长", "董事"], leader
    assert leader["tenure"] == "2018-01-01–2029-10-09", leader
    assert leader["salary"] == 5316100, leader
    assert round(leader["hold_ratio"], 4) == 46.7751, leader
    assert leader["is_control"] is True and leader["is_legal_person"] is True, leader
    assert leader["education"] == "硕士", leader

    # 没有离任日期的按"至今"处理
    assert directors["members"][1]["tenure"] == "2025-04-02 至今", directors


def test_errors() -> None:
    try:
        mg.management_from_payload(
            {"status_code": 1, "status_msg": "参数错误", "data": {}}, CODE
        )
    except mg.ManagementError as error:
        assert "参数错误" in str(error), error
    else:
        raise AssertionError("接口失败时应该抛 ManagementError")

    try:
        mg.management_from_payload({"status_code": 0, "data": None}, CODE)
    except mg.ManagementError:
        pass
    else:
        raise AssertionError("没有 data 时应该抛 ManagementError")

    empty = {"status_code": 0, "data": {
        key: {"list": []} for key in ("directors", "manager", "auditors")
    }}
    try:
        mg.management_from_payload(empty, CODE)
    except mg.ManagementError:
        pass
    else:
        raise AssertionError("所有分组都为空时应该抛 ManagementError")


def test_format() -> None:
    data = mg.management_from_payload(PAYLOAD, CODE)
    text = mg.format_management(data, code=CODE, name=NAME, as_of=data["as_of"])

    assert text.startswith(f"{NAME}({CODE}) · 管理层 2026-10-10"), text
    assert "董事会 2 人" in text and "高管 1 人" in text, text
    assert "林木勤（董事长、董事，2018-01-01–2029-10-09，62岁，薪酬 531.6万，持股 46.8%）" in text, text
    assert "家族" not in text, text


def test_cache() -> None:
    tmpdir = tempfile.mkdtemp(prefix="management_test_")
    mg.DB_PATH = os.path.join(tmpdir, "stock.db")

    calls = {"fetch": 0}

    def fake_fetch(code: str) -> dict:
        calls["fetch"] += 1
        return mg.management_from_payload(PAYLOAD, code)

    mg.fetch_management = fake_fetch  # type: ignore[assignment]

    first = mg.ensure_management(CODE)
    assert calls["fetch"] == 1, calls
    assert first["stale"] is False, first
    assert first["data"]["count"] == 4, first
    assert first["data"]["fetched_at"], first

    # 一周内不重复抓取
    second = mg.ensure_management(CODE)
    assert calls["fetch"] == 1, calls
    assert second["data"]["fetched_at"] == first["data"]["fetched_at"], second

    # force 强制刷新
    mg.ensure_management(CODE, force=True)
    assert calls["fetch"] == 2, calls

    # 缓存过期后重新抓取
    original_age = mg._age_hours
    mg._age_hours = lambda fetched_at: 999.0  # type: ignore[assignment]
    mg.ensure_management(CODE)
    assert calls["fetch"] == 3, calls
    mg._age_hours = original_age  # type: ignore[assignment]

    # 抓取失败但本地有缓存：返回旧数据并标记 stale
    def broken(code: str) -> dict:
        raise mg.ManagementError("接口超时")

    mg.fetch_management = broken  # type: ignore[assignment]
    stale = mg.ensure_management(CODE, force=True)
    assert stale["stale"] is True and stale["data"]["count"] == 4, stale

    # 没有缓存时只能报错
    try:
        mg.ensure_management("600000")
    except mg.ManagementError as error:
        assert "接口超时" in str(error), error
    else:
        raise AssertionError("无缓存且抓取失败时应该抛 ManagementError")

    # 空代码直接拒绝，不落到网络
    try:
        mg.ensure_management("")
    except mg.ManagementError:
        pass
    else:
        raise AssertionError("空代码应该抛 ManagementError")


def test_build() -> None:
    tmpdir = tempfile.mkdtemp(prefix="management_build_")
    mg.DB_PATH = os.path.join(tmpdir, "stock.db")

    mg.fetch_management = lambda code: mg.management_from_payload(PAYLOAD, code)  # type: ignore[assignment]

    class FakeStock:
        name = NAME

    original_stock = mg.get_stock
    mg.get_stock = lambda code: FakeStock()  # type: ignore[assignment]
    try:
        data = mg.build_management(CODE)
    finally:
        mg.get_stock = original_stock  # type: ignore[assignment]

    assert data["available"] is True, data
    assert data["name"] == NAME, data
    assert data["as_of"] == "2026-10-10", data
    assert data["count"] == 4, data
    assert data["summary"].startswith(f"{NAME}({CODE})"), data
    assert data["sources"] == {mg.SOURCE_LABEL: "2026-10-10"}, data


def main() -> None:
    test_payload()
    test_errors()
    test_format()
    test_cache()
    test_build()
    print("PASS")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
