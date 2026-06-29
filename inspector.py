# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/inspector.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : v_catalog 메타데이터로 스키마/테이블/컬럼을 조회한다.
###############################################################################

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str

    @property
    def qualified(self) -> str:
        return f'"{self.schema}"."{self.name}"'


def list_schemas(conn) -> List[str]:
    sql = """
        SELECT schema_name
          FROM v_catalog.schemata
         WHERE is_system_schema = FALSE
         ORDER BY schema_name
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]


def list_tables_in_schema(conn, schema: str) -> List[TableRef]:
    """일반 테이블만 (뷰/임시/외부/플렉스 제외).
    외부 테이블(table_definition 이 채워진 것)은 데이터가 Vertica 밖이라 SELECT 불가.
    """
    sql = """
        SELECT table_schema, table_name
          FROM v_catalog.tables
         WHERE table_schema = %s
           AND is_temp_table = FALSE
           AND is_system_table = FALSE
           AND is_flextable = FALSE
           AND is_external_iceberg_table = FALSE
           AND (table_definition IS NULL OR table_definition = '')
         ORDER BY table_name
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema,))
        return [TableRef(schema=r[0], name=r[1]) for r in cur.fetchall()]


def is_external_table(conn, schema: str, table: str) -> bool:
    sql = """
        SELECT COALESCE(NULLIF(table_definition, ''), '') <> ''
               OR is_flextable
               OR is_external_iceberg_table
          FROM v_catalog.tables
         WHERE table_schema = %s AND table_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema, table))
        row = cur.fetchone()
        return bool(row and row[0])


def table_exists(conn, schema: str, table: str) -> bool:
    sql = """
        SELECT 1
          FROM v_catalog.tables
         WHERE table_schema = %s AND table_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema, table))
        return cur.fetchone() is not None


def schema_exists(conn, schema: str) -> bool:
    sql = """
        SELECT 1 FROM v_catalog.schemata WHERE schema_name = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema,))
        return cur.fetchone() is not None


def estimate_rows(conn, schema: str) -> dict:
    """스키마 각 테이블의 추정 행수 {table_name: rows}. (COUNT 스캔 아님, 카탈로그)
    프로젝션 단위 합 → 같은 테이블의 버디 프로젝션은 MAX 로 골라 중복 계수를 피한다.
    실패하면 빈 dict (플래너는 추정 없으면 보수적으로 동작).
    """
    sql = """
        SELECT anchor_table_name, MAX(prj_rows)
          FROM (
            SELECT anchor_table_name, projection_name, SUM(row_count) AS prj_rows
              FROM v_monitor.projection_storage
             WHERE projection_schema = %s
             GROUP BY anchor_table_name, projection_name
          ) p
         GROUP BY anchor_table_name
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (schema,))
            return {r[0]: int(r[1] or 0) for r in cur.fetchall()}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}


def count_rows(conn, schema: str, table: str) -> int:
    """진행률 총계용 행 수. (식별자는 파라미터 바인딩이 안 되므로 직접 인용)"""
    def q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'
    sql = f'SELECT COUNT(*) FROM {q(schema)}.{q(table)}'
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


def list_procedures(conn, schema: str) -> List[tuple]:
    """스키마의 (저장)프로시저 목록. (이름, 인자문자열) 튜플 리스트.
    인자문자열은 EXPORT_OBJECTS 시그니처로 그대로 쓰인다 ('NAME type, NAME type, ...').
    """
    sql = """
        SELECT procedure_name, procedure_arguments
          FROM v_catalog.user_procedures
         WHERE schema_name = %s
         ORDER BY procedure_name
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema,))
        return [(r[0], r[1] or '') for r in cur.fetchall()]


def get_columns(conn, schema: str, table: str) -> List[str]:
    """SELECT 시 컬럼 순서 고정용. ordinal_position 정렬."""
    sql = """
        SELECT column_name
          FROM v_catalog.columns
         WHERE table_schema = %s AND table_name = %s
         ORDER BY ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(sql, (schema, table))
        return [r[0] for r in cur.fetchall()]
