# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/_worker.py
# Desc            : 병렬 덤프 워커 진입점. 각 프로세스가 자체 커넥션을 열어
#                   배정된 단위(통테이블 또는 샤드)를 파일로 기록한다.
#                   ProcessPoolExecutor 가 pickle 하므로 모듈 최상위 함수여야 한다.
###############################################################################

import os

from v_dump.connection import vertica_connection
from v_dump.data import dump_table_data, shard_where


def dump_unit(args: dict) -> dict:
    """args = {cfg, schema, table, columns, shard_index, shard_count, outpath}
    반환 = {table, shard_index, rows, error}
    """
    cfg = args['cfg']
    schema = args['schema']
    table = args['table']
    columns = args['columns']
    si = args['shard_index']
    sc = args['shard_count']
    outpath = args['outpath']

    where = shard_where(columns, sc, si) if si is not None else None
    try:
        with vertica_connection(cfg) as conn:
            with open(outpath, 'w', encoding='utf-8', newline='') as fh:
                rows, _ = dump_table_data(conn, schema, table, fh,
                                          columns=columns, where=where)
        return {'table': table, 'shard_index': si, 'rows': rows, 'error': None}
    except Exception as e:
        try:
            os.remove(outpath)
        except OSError:
            pass
        return {'table': table, 'shard_index': si, 'rows': 0,
                'error': str(e).splitlines()[0]}
