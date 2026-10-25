"""行情窗口 → Web 助手的当前标的桥。

两个进程（Qt 悬浮窗与 Streamlit 服务）不共享内存，用一个很小的 JSON
文件传递“现在选中的是谁”。写入是原子替换，读不到或文件损坏时按
“没有选中股票”处理，页面照常可用。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "assistant_state.json"

# 测试与多实例场景可以用环境变量指向临时文件
STATE_PATH = Path(os.environ.get("MARKET_ASSISTANT_STATE", DEFAULT_PATH))


def state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else STATE_PATH


def write_selection(
    code: str | None,
    name: str = "",
    is_index: bool = False,
    path: str | Path | None = None,
) -> None:
    """原子写入当前标的；指数也记下来，页面据此提示“没有财报数据”。"""
    target = state_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "code": code or None,
        "name": name or "",
        "is_index": bool(is_index),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    handle, temp = tempfile.mkstemp(
        dir=target.parent, prefix=".assistant_state_", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def read_selection(path: str | Path | None = None) -> dict | None:
    """读取当前标的；缺失、损坏或字段不对时返回 ``None``。"""
    target = state_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    code = raw.get("code")
    if code is not None and not isinstance(code, str):
        return None
    return {
        "code": (code or "").strip() or None,
        "name": str(raw.get("name") or "").strip(),
        "is_index": bool(raw.get("is_index")),
        "updated_at": str(raw.get("updated_at") or ""),
    }
