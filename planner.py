# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/planner.py
# Desc            : 워크로드를 보고 덤프 전략을 동적으로 결정한다.
#                   - 자잘하면 순차
#                   - 테이블 많으면 테이블 단위 병렬
#                   - 거대 테이블은 행 단위 샤딩으로 병렬
###############################################################################

import os

_MAX_WORKERS_CAP = 4          # 병렬 상한 (min(코어, 4))
_BIG_TABLE_ROWS = 2_000_000   # 이 이상이면 샤딩 후보
_SHARD_ROWS = 1_000_000       # 샤드 1개 목표 행수
_SEQ_TOTAL_ROWS = 500_000     # 전체 추정행수가 이보다 작으면 순차


def resolve_workers() -> int:
    """V_DUMP_JOBS: 'auto'(기본) → min(코어, 4),  정수 → 그 값(>=1)."""
    v = os.environ.get('V_DUMP_JOBS', 'auto').strip().lower()
    cores = os.cpu_count() or 1
    if v in ('', 'auto'):
        return max(1, min(cores, _MAX_WORKERS_CAP))
    try:
        return max(1, int(v))
    except ValueError:
        return max(1, min(cores, _MAX_WORKERS_CAP))


def _unit(schema, table, shard_index, shard_count):
    return {'schema': schema, 'table': table,
            'shard_index': shard_index, 'shard_count': shard_count}


def plan(targets, est: dict, workers: int):
    """
    targets : [TableRef, ...]
    est     : {table_name: 추정행수}  (없으면 0 → 보수적으로 순차)
    workers : 병렬 상한
    반환    : (parallel: bool, units: [unit])
              unit = {schema, table, shard_index(None=통테이블), shard_count}
    """
    n = len(targets)
    total = sum(est.get(t.name, 0) for t in targets)
    biggest = max((est.get(t.name, 0) for t in targets), default=0)

    # 병렬이 의미 없는 경우 → 순차 (오버헤드/세션 절약)
    if workers <= 1 or total < _SEQ_TOTAL_ROWS or (n == 1 and biggest < _BIG_TABLE_ROWS):
        return False, [_unit(t.schema, t.name, None, 1) for t in targets]

    # 병렬: 거대 테이블은 샤딩, 나머지는 통테이블로 풀에 분배
    units = []
    for t in targets:
        rows = est.get(t.name, 0)
        if rows >= _BIG_TABLE_ROWS:
            shards = min(workers, max(2, rows // _SHARD_ROWS))
            units += [_unit(t.schema, t.name, k, shards) for k in range(shards)]
        else:
            units.append(_unit(t.schema, t.name, None, 1))
    return True, units
