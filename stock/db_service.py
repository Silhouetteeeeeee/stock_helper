from common.db_utils import query_all, query_one
from common.stock import Stock

QUERY_SQL_STOCK = """
    select code, name
    from stock
"""

QUERY_SQL_STOCK_BY_NAME= """
    select code, name
    from stock where name = ?
"""


def get_all_stocks() -> list[Stock]:
    return query_all(QUERY_SQL_STOCK, cls=Stock)

def get_stock_by_name(name: str) -> Stock:
    return query_one(QUERY_SQL_STOCK_BY_NAME, cls=Stock, parameters=(name,))

