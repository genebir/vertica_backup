###############################################################################
# File            : v_dump/data.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : 테이블 행을 Vertica COPY 호환 파이프 구분 .dat 파일로 직렬화.
###############################################################################

from typing import IO, Iterable, List

from v_dump.escape import format_copy_row
from v_dump.inspector import get_columns

_FETCH_SIZE = 10000


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _iter_rows(conn, schema: str, table: str, columns: List[str]) -> Iterable[tuple]:
    cols_sql = ', '.join(_quote_ident(c) for c in columns)
    sql = f'SELECT {cols_sql} FROM {_quote_ident(schema)}.{_quote_ident(table)}'
    with conn.cursor() as cur:
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(_FETCH_SIZE)
            if not rows:
                return
            for r in rows:
                yield r


def dump_table_data(conn, schema: str, table: str, out: IO) -> tuple:
    """
    한 테이블의 데이터를 out 에 순수 데이터(헤더/종결자 없음)로 기록.
    return: (적재 row 수, 컬럼 리스트)
    """
    columns = get_columns(conn, schema, table)
    if not columns:
        return 0, []

    count = 0
    for row in _iter_rows(conn, schema, table, columns):
        out.write(format_copy_row(row))
        count += 1

    return count, columns


def build_copy_statement(schema: str, table: str, columns: List[str], dat_filename: str) -> str:
    """
    .dat 을 다시 적재하는 COPY 문. vsql 에서 `\\i load.sql` 로 실행.
    FROM LOCAL 이라 vsql 클라이언트의 파일 경로 기준.
    """
    cols = ', '.join(_quote_ident(c) for c in columns)
    return (
        f"COPY {_quote_ident(schema)}.{_quote_ident(table)} ({cols}) "
        f"FROM LOCAL '{dat_filename}' "
        f"DELIMITER '|' NULL AS '\\N' ENCLOSED BY '' "
        f"ABORT ON ERROR DIRECT;\n"
    )
