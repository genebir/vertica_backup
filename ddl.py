# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Gibseung Yeom)
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
