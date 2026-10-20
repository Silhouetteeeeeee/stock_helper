import os
import sqlite3
from typing import Any, Optional, Type, TypeVar, List

# ========= 路径配置（你原来的） =========
DB_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data"
)
os.makedirs(DB_DIR, exist_ok=True)  # 自动创建data文件夹，防止不存在报错
DB_PATH = os.path.join(DB_DIR, "stock.db")

# 全局连接（单线程使用！多线程不要全局conn）
conn = sqlite3.connect(DB_PATH)
# 开启Row工厂可选，也可以在查询函数里动态处理
# conn.row_factory = sqlite3.Row

def get_conn() -> sqlite3.Connection:
    return conn

# 泛型，用于model自动映射
T = TypeVar("T")


def execute(sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    """
    执行增删改SQL，返回cursor，需要手动commit
    :param sql: sql语句
    :param parameters: 参数元组
    """
    cur = get_conn().execute(sql, parameters)
    get_conn().commit()
    return cur


def query_one(sql: str, parameters: tuple[Any, ...] = (), cls: Optional[Type[T]] = None) -> Optional[T | sqlite3.Row]:
    """
    查询单条
    - 不传cls：返回sqlite3.Row
    - 传cls(dataclass/pydantic)：直接返回对象
    """
    get_conn().row_factory = sqlite3.Row
    cur = get_conn().execute(sql, parameters)
    row = cur.fetchone()
    if row is None:
        return None
    if cls is None:
        return row
    # 映射到目标类
    return cls(**dict(row))


def query_all(sql: str, parameters: tuple[Any, ...] = (), cls: Optional[Type[T]] = None) -> List[T | sqlite3.Row]:
    """
    查询多条，返回列表
    - 不传cls：List[sqlite3.Row]
    - 传cls：List[你的对象]
    """
    get_conn().row_factory = sqlite3.Row
    cur = get_conn().execute(sql, parameters)
    rows = cur.fetchall()
    if cls is None:
        return rows
    return [cls(**dict(r)) for r in rows]


def close_conn():
    """关闭连接，程序退出调用"""
    get_conn().close()
