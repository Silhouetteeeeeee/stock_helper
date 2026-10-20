from common.db_utils import query_all
from stock.stock import Stock

QUERY_SQL = """
    select code, name
    from stock
"""


def get_all_stocks() -> list[Stock]:
    return query_all(QUERY_SQL, cls=Stock)
