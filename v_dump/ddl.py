# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/ddl.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : EXPORT_OBJECTS() 를 통해 테이블/스키마 DDL 추출.
###############################################################################

import re


def _export(conn, scope: str) -> str:
    """
    EXPORT_OBJECTS('', '<scope>') 를 호출해 DDL 문자열을 받는다.
    scope 예: 'MY_SCHEMA.TB_SAMPLE' (테이블) / 'MY_SCHEMA' (스키마 전체).
    """
    sql = "SELECT EXPORT_OBJECTS('', %s, FALSE)"
    with conn.cursor() as cur:
        cur.execute(sql, (scope,))
        row = cur.fetchone()
    if not row or not row[0]:
        return ''
    return row[0]


def export_table_ddl(conn, schema: str, table: str) -> str:
    return _export(conn, f'{schema}.{table}')


def export_schema_ddl(conn, schema: str) -> str:
    return _export(conn, schema)


def export_objects_ddl(conn, scope: str) -> str:
    """scope 를 그대로 EXPORT_OBJECTS 에 전달.
    scope 예: 'SCH'(스키마) / 'SCH.T'(테이블) / 'SCH.T1,SCH.T2'(다중 테이블)."""
    return _export(conn, scope)


# EXPORT_OBJECTS 가 뱉는 'CREATE ...' 를 재실행 안전(멱등) 형태로 바꾼다.
# 줄 시작(^, MULTILINE) 기준이라 최상위 문만 건드리고, 프로시저 본문 내부는 거의 안 건드린다.
_IDEMPOTENT_SUBS = [
    (re.compile(r'^CREATE SCHEMA ',     re.MULTILINE), 'CREATE SCHEMA IF NOT EXISTS '),
    (re.compile(r'^CREATE TABLE ',      re.MULTILINE), 'CREATE TABLE IF NOT EXISTS '),
    (re.compile(r'^CREATE SEQUENCE ',   re.MULTILINE), 'CREATE SEQUENCE IF NOT EXISTS '),
    (re.compile(r'^CREATE PROJECTION ', re.MULTILINE), 'CREATE PROJECTION IF NOT EXISTS '),
    (re.compile(r'^CREATE PROCEDURE ',  re.MULTILINE), 'CREATE OR REPLACE PROCEDURE '),
    (re.compile(r'^CREATE VIEW ',       re.MULTILINE), 'CREATE OR REPLACE VIEW '),
]


def make_idempotent(ddl: str) -> str:
    """CREATE 문을 IF NOT EXISTS / OR REPLACE 형태로 바꿔 재실행해도 에러가 안 나게 한다."""
    if not ddl:
        return ddl
    for pat, rep in _IDEMPOTENT_SUBS:
        ddl = pat.sub(rep, ddl)
    return ddl


# 세그먼테이션 끝의 'OFFSET n' — 버디 배치 시작 위치라 원본 노드 수에 묶인다.
# 'ALL NODES' 뒤의 이것만 떼면 'SEGMENTED BY ... ALL NODES' 만 남아, 대상 클러스터가
# 자기 K-safety 에 맞춰 세그먼트·버디를 자동 배치한다(정렬순서·인코딩 설계는 보존).
_SEG_OFFSET = re.compile(r'(\bALL NODES)\s+OFFSET\s+\d+(\s*;)', re.IGNORECASE)
# 설계 K-safety 지시. 원본 클러스터의 노드 수에 묶이므로 이식 시 제거해 대상 DB 설정을 따르게 한다.
_KSAFE_LINE = re.compile(
    r'^SELECT MARK_DESIGN_KSAFE\([^)]*\);[ \t]*$\n?', re.MULTILINE)
# 특정 노드에 핀된 프로젝션(ALL NODES 가 아니라 노드명 나열). 대상에 그 노드명이 없으면 깨진다.
_PINNED_NODES = re.compile(r'\bNODES\s+(v_\w+?_node\d+)', re.IGNORECASE)


def rehome_projections(ddl: str):
    """프로젝션 정의는 유지하되, 노드 수에 묶인 부분만 떼어 이식 가능하게 만든다.

    - 'SEGMENTED BY ... ALL NODES OFFSET n' → 'SEGMENTED BY ... ALL NODES'
      (대상 클러스터가 자기 토폴로지/K-safety 에 맞춰 세그먼트·버디를 자동 배치)
    - 'SELECT MARK_DESIGN_KSAFE(n);' 제거 (대상 DB 의 K-safety 설정을 따름)

    정렬순서(ORDER BY)·컬럼 인코딩·세그먼테이션 식은 그대로라 성능 설계가 보존된다.
    이로써 노드 수가 다른 클러스터(예: 1노드→3노드)로 복원해도 3586 이 나지 않는다.

    반환 : (변환된 ddl, pinned)
           pinned = 여전히 특정 노드명에 핀돼 자동 재배치가 불가한 노드명 목록
                    (비어있지 않으면 호출측에서 경고).
    """
    if not ddl:
        return ddl, []
    # ALL NODES 가 아닌, 노드명 나열형 프로젝션은 OFFSET 제거로도 이식이 안 된다 → 감지만.
    pinned = sorted(set(_PINNED_NODES.findall(ddl)))
    ddl = _SEG_OFFSET.sub(r'\1\2', ddl)
    ddl = _KSAFE_LINE.sub('', ddl)
    # KSAFE 줄 제거로 생긴 연속 빈 줄 정리.
    ddl = re.sub(r'\n{3,}', '\n\n', ddl)
    return ddl, pinned
