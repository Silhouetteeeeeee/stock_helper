"""读取项目根的 .env：只补环境变量，不覆盖已有值。"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: str | Path | None = None) -> int:
    """把 ``.env`` 里的键值写进 ``os.environ``，返回实际写入条数。"""
    path = Path(path) if path is not None else DEFAULT_PATH
    if not path.exists():
        return 0

    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        count += 1
    return count
