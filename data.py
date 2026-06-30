# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/data.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : 테이블 행을 Vertica COPY 호환 파이프 구분 .dat 파일로 직렬화.
###############################################################################

import gzip
from typing import IO, Iterable, List

from v_dump.escape import format_copy_row
from v_dump.inspector import get_columns

_FETCH_SIZE = 20000   # 서버 round-trip 감소 (메모리와의 균형)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def dat_name(schema: str, table: str, compress: bool = False) -> str:
    """.dat 파일명. 압축이면 .dat.gz."""
    return f"{schema}.{table}.dat" + ('.gz' if compress else '')


def open_dat_writer(path: str, compress: bool = False):
    """.dat 쓰기 핸들. 압축이면 gzip 텍스트 모드(쓰는 줄은 동일).
    gzip 멤버는 이어붙여도 유효하므로 샤드 파일을 그대로 concat 할 수 있다.
    """
    if compress:
        return gzip.open(path, 'wt', encoding='utf-8', newline='')
    return open(path, 'w', encoding='utf-8', newline='')


def shard_where(columns: List[str], shard_count: int, shard_index: int) -> str:
    """거대 테이블을 N조각으로 무겹침·전수 분할하는 WHERE 절.
    HASH 는 결정적이라 세션이 달라도 조각이 서로소이면서 전체를 덮는다.
    """
    cols = ', '.join(_quote_ident(c) for c in columns)
    return f'MOD(HASH({cols}), {shard_count}) = {shard_index}'


def _iter_rows(conn, schema: str, table: str, columns: List[str],
               where: str = None) -> Iterable[tuple]:
    cols_sql = ', '.join(_quote_ident(c) for c in columns)
    sql = f'SELECT {cols_sql} FROM {_quote_ident(schema)}.{_quote_ident(table)}'
    if where:
        sql += f' WHERE {where}'
    with conn.cursor() as cur:
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(_FETCH_SIZE)
            if not rows:
                return
            for r in rows:
                yield r


def dump_table_data(conn, schema: str, table: str, out: IO, on_progress=None,
                    columns: List[str] = None, where: str = None) -> tuple:
    """
    한 테이블(또는 샤드)의 데이터를 out 에 순수 데이터로 기록.
    columns 를 주면 메타조회를 생략(병렬 워커가 부모에서 받은 컬럼 재사용).
    where 를 주면 그 조건의 행만(샤딩).
    on_progress(written) 가 주어지면 배치마다 누적 행수로 호출.
    return: (적재 row 수, 컬럼 리스트)
    """
    if columns is None:
        columns = get_columns(conn, schema, table)
    if not columns:
        return 0, []

    count = 0
    buf = []
    append = buf.append
    for row in _iter_rows(conn, schema, table, columns, where):
        append(format_copy_row(row))
        count += 1
        if len(buf) >= _FETCH_SIZE:
            out.write(''.join(buf))
            buf.clear()
            if on_progress is not None:
                on_progress(count)
    if buf:
        out.write(''.join(buf))
    if on_progress is not None:
        on_progress(count)
    return count, columns


def build_copy_statement(schema: str, table: str, columns: List[str], dat_filename: str,
                         compress: bool = False) -> str:
    """
    .dat 을 다시 적재하는 COPY 문. vsql 에서 `\\i load.sql` 로 실행.
    FROM LOCAL 이라 vsql 클라이언트의 파일 경로 기준.
    compress 면 GZIP 필터 추가 → Vertica 가 압축 파일을 직접 읽는다(별도 해제 불필요).
    """
    cols = ', '.join(_quote_ident(c) for c in columns)
    gz = 'GZIP ' if compress else ''
    return (
        f"COPY {_quote_ident(schema)}.{_quote_ident(table)} ({cols}) "
        f"FROM LOCAL '{dat_filename}' {gz}"
        f"DELIMITER '|' NULL AS '\\N' ENCLOSED BY '' "
        f"ABORT ON ERROR DIRECT;\n"
    )
