"""研究助手的共享数据结构：可引用的数据来源与工具统一结果。"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """一条可引用的数据切片。

    ``as_of`` 是数据对应的报告期或日期，``fetched_at`` 是抓取时间；
    同一条来源重复登记时按 ``id`` 幂等。
    """

    id: str
    label: str
    detail: str
    as_of: str
    fetched_at: str
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "url": self.url,
        }


@dataclass(frozen=True)
class ToolResult:
    """工具调用的统一结果；失败也返回它，不向模型抛异常。"""

    ok: bool
    company: str | None = None
    as_of: str | None = None
    sources: tuple[Source, ...] = ()
    data: dict | None = None
    error: str | None = None

    def to_message(self) -> str:
        """压成给模型看的 JSON；``None`` 字段不写，sources 总是列表。"""
        message: dict = {
            "ok": self.ok,
            "sources": [source.to_dict() for source in self.sources],
        }
        for key in ("company", "as_of", "data", "error"):
            value = getattr(self, key)
            if value is not None:
                message[key] = value
        return json.dumps(message, ensure_ascii=False)
