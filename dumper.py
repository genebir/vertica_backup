# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/dumper.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : 덤프 오케스트레이터 (디렉토리 출력).
#                   <outdir>/
#                     schema.ddl.sql       # EXPORT_OBJECTS 결과 (선택)
#                     load.sql             # 각 .dat 을 다시 적재하는 COPY 문
#                     <schema>.<table>.dat # 파이프 구분 데이터 파일
###############################################################################

import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from v_dump.config import ConnectionConfig
from v_dump.connection import vertica_connection
from v_dump.data import build_copy_statement, dat_name, dump_table_data, open_dat_writer
from v_dump.ddl import export_objects_ddl, make_idempotent
from v_dump.inspector import (
    TableRef,
    count_rows,
    estimate_rows,
    get_columns,
    list_procedures,
    list_tables_in_schema,
    schema_exists,
    table_exists,
)
from v_dump.planner import plan, resolve_workers
from v_dump.progress import Progress, progress_enabled


@dataclass
class DumpOptions:
    schema: str
    tables: Optional[List[str]] = None   # None/[] 이면 스키마 전체
    schema_only: bool = False        # DDL 만 (.dat 생성 안 함)
    data_only: bool = False          # 데이터만 (DDL/load.sql 생성 안 함)
    include_procedures: bool = True  # 테이블 단위 백업 시, 이름에 테이블명이 포함된 프로시저 DDL 도 추출
    compress: bool = False           # .dat 을 gzip(.dat.gz)으로 — 전송/보관 용량↓ (COPY GZIP)


class VerticaDumper:
    def __init__(self, cfg: ConnectionConfig):
        self.cfg = cfg
        self._proc_cache = {}   # schema → [(proc_name, args), ...]  (덤프 1회 내 재사용)

    def dump(self, opts: DumpOptions, outdir: str) -> dict:
        """단일 작업. 자체 커넥션을 연다."""
        with vertica_connection(self.cfg) as conn:
            return self._dump_with_conn(conn, opts, outdir)

    def dump_jobs(self, jobs: List[tuple]) -> List[tuple]:
        """여러 작업을 커넥션 하나로 처리한다.

        jobs : [(label, outdir, DumpOptions), ...]
        반환 : [(label, outdir, stats|None, error|None), ...]  (입력 순서 유지)

        한 작업이 실패해도 롤백 후 다음 작업을 계속 진행한다.
        """
        results = []
        with vertica_connection(self.cfg) as conn:
            for label, outdir, opts in jobs:
                try:
                    stats = self._dump_with_conn(conn, opts, outdir)
                    results.append((label, outdir, stats, None))
                except Exception as e:
                    # 실패가 세션을 aborted 상태로 남겨 다음 작업까지 망치지 않도록 롤백.
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    results.append((label, outdir, None, e))
        return results

    def _dump_with_conn(self, conn, opts: DumpOptions, outdir: str) -> dict:
        """열린 커넥션으로 한 작업(한 outdir)을 덤프한다."""
        if opts.schema_only and opts.data_only:
            raise ValueError("--schema-only 와 --data-only 는 동시에 사용할 수 없습니다.")

        os.makedirs(outdir, exist_ok=True)

        targets = self._resolve_targets(conn, opts)

        ddl_path = None
        proc_info = {'included': [], 'skipped': []}
        if not opts.data_only:
            ddl_path, proc_info = self._write_ddl(conn, outdir, opts)

        stats = {
            'tables': len(targets),
            'rows': 0,
            'files': [],
            'failed': [],   # [(qualified_name, reason)]
            'procedures': proc_info,   # {'included': [..], 'skipped': [(name, reason)]}
        }
        copy_statements: List[str] = []

        if not opts.schema_only:
            # 컬럼 일괄 조회 (COPY 문 + 병렬 워커에서 재사용)
            columns_map = {t.name: get_columns(conn, t.schema, t.name) for t in targets}
            workers = resolve_workers()
            est = estimate_rows(conn, opts.schema) if workers > 1 else {}
            parallel, units = plan(targets, est, workers)
            if parallel:
                self._dump_parallel(opts, outdir, targets, columns_map, units,
                                    workers, stats, copy_statements)
            else:
                self._dump_sequential(conn, opts, outdir, targets, columns_map,
                                     stats, copy_statements)

        load_path = None
        if not opts.data_only and copy_statements:
            load_path = self._write_load_script(outdir, copy_statements)

        self._write_manifest(outdir, opts, stats, ddl_path, load_path, proc_info)
        return stats

    # ---------- 데이터 덤프: 순차 / 병렬 ----------

    def _dump_sequential(self, conn, opts, outdir, targets, columns_map,
                         stats, copy_statements):
        """순차 덤프 (테이블별 라이브 진행률 바)."""
        n_targets = len(targets)
        prog_on = progress_enabled()
        for i, t in enumerate(targets, 1):
            dname = dat_name(t.schema, t.name, opts.compress)
            dat_path = os.path.join(outdir, dname)
            cols = columns_map[t.name]
            total = 0
            if prog_on:
                try:
                    total = count_rows(conn, t.schema, t.name)
                except Exception:
                    total = 0
            prog = Progress(f"[{i}/{n_targets}] {t.schema}.{t.name}", total, prog_on)
            try:
                with open_dat_writer(dat_path, opts.compress) as fh:
                    n, _ = dump_table_data(conn, t.schema, t.name, fh,
                                           on_progress=prog.update, columns=cols)
                prog.done(n)
            except Exception as e:
                prog.abort()
                try:
                    os.remove(dat_path)
                except OSError:
                    pass
                stats['failed'].append((f"{t.schema}.{t.name}", str(e).splitlines()[0]))
                print(f"[v_dump] WARN skip {t.schema}.{t.name}: {str(e).splitlines()[0]}",
                      file=sys.stderr)
                continue
            stats['rows'] += n
            stats['files'].append((dname, n))
            if cols:
                copy_statements.append(
                    build_copy_statement(t.schema, t.name, cols, dname, compress=opts.compress))

    def _dump_parallel(self, opts, outdir, targets, columns_map, units,
                       workers, stats, copy_statements):
        """멀티프로세스 병렬 덤프. 거대 테이블은 샤드 파일로 받아 합친다."""
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor, as_completed
        from v_dump._worker import dump_unit

        # 명시적 fork 컨텍스트: 부모 상태를 그대로 상속해 모듈 재임포트가 없다.
        # (Python 3.14 의 기본 forkserver/spawn 은 main 모듈을 재임포트하려다
        #  python -m / -c 실행에서 깨진다. 워커는 각자 새 커넥션을 연다.)
        try:
            mp_ctx = mp.get_context('fork')
        except ValueError:
            mp_ctx = mp.get_context()   # fork 불가 환경 대비(이론상 Linux 전용이라 발생X)

        jobs = []
        for u in units:
            t, si = u['table'], u['shard_index']
            final = dat_name(u['schema'], t, opts.compress)   # <schema>.<table>.dat[.gz]
            if si is None:
                outpath = os.path.join(outdir, final)
            else:
                outpath = os.path.join(outdir, f"{final}.part{si}")  # gzip 멤버라 concat 가능
            jobs.append({'cfg': self.cfg, 'schema': u['schema'], 'table': t,
                         'columns': columns_map[t], 'shard_index': si,
                         'shard_count': u['shard_count'], 'outpath': outpath,
                         'compress': opts.compress})

        prog_on = progress_enabled()
        total_units = len(jobs)
        n_shards = sum(1 for u in units if u['shard_index'] is not None)
        print(f"[v_dump] 병렬 덤프: workers={workers}, units={total_units} "
              f"(테이블 {len(targets)}개, 샤드 {n_shards}개)", file=sys.stderr)

        rows_by_table = {}
        err_by_table = {}
        parts_by_table = {}   # table -> [(shard_index, path), ...]
        done = 0
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp_ctx) as ex:
            futs = {ex.submit(dump_unit, j): j for j in jobs}
            for fut in as_completed(futs):
                j = futs[fut]
                res = fut.result()
                done += 1
                t = res['table']
                if res['error']:
                    err_by_table[t] = res['error']
                else:
                    rows_by_table[t] = rows_by_table.get(t, 0) + res['rows']
                if j['shard_index'] is not None:
                    parts_by_table.setdefault(t, []).append((j['shard_index'], j['outpath']))
                if prog_on:
                    sys.stderr.write(f"\r  병렬 진행  [{done}/{total_units} units]  "
                                     f"{sum(rows_by_table.values()):,} rows")
                    sys.stderr.flush()
                else:
                    print(f"[v_dump]   [{done}/{total_units}] {opts.schema}.{t}"
                          f"{('#'+str(j['shard_index'])) if j['shard_index'] is not None else ''}"
                          f"  rows={res['rows']:,}", file=sys.stderr)
        if prog_on:
            sys.stderr.write('\n')
            sys.stderr.flush()

        # 입력 순서대로 stats 구성 + 샤드 재조립
        for t in targets:
            name = t.name
            dname = dat_name(opts.schema, name, opts.compress)
            dat_path = os.path.join(outdir, dname)
            parts = parts_by_table.get(name)
            if name in err_by_table:
                # 실패 → 파트/부분파일 정리
                for _, p in (parts or []):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                try:
                    os.remove(dat_path)
                except OSError:
                    pass
                stats['failed'].append((f"{opts.schema}.{name}", err_by_table[name]))
                print(f"[v_dump] WARN skip {opts.schema}.{name}: {err_by_table[name]}",
                      file=sys.stderr)
                continue
            if parts:
                # 샤드 파일을 하나의 .dat 으로 합침 (순서 무관, COPY 는 집합 적재)
                with open(dat_path, 'wb') as out:
                    for _, p in sorted(parts):
                        with open(p, 'rb') as pf:
                            shutil.copyfileobj(pf, out)
                        os.remove(p)
            n = rows_by_table.get(name, 0)
            stats['rows'] += n
            stats['files'].append((dname, n))
            cols = columns_map[name]
            if cols:
                copy_statements.append(
                    build_copy_statement(opts.schema, name, cols, dname, compress=opts.compress))

    # ---------- internals ----------

    def _resolve_targets(self, conn, opts: DumpOptions) -> List[TableRef]:
        if not schema_exists(conn, opts.schema):
            raise ValueError(f"Schema not found: {opts.schema}")

        if opts.tables:
            refs: List[TableRef] = []
            missing: List[str] = []
            for name in opts.tables:
                if table_exists(conn, opts.schema, name):
                    refs.append(TableRef(schema=opts.schema, name=name))
                else:
                    missing.append(name)
            if missing:
                raise ValueError(
                    f"Table not found: {opts.schema}.{{{', '.join(missing)}}}"
                )
            return refs

        return list_tables_in_schema(conn, opts.schema)

    def _write_ddl(self, conn, outdir: str, opts: DumpOptions) -> tuple:
        # EXPORT_OBJECTS 의 scope 는 콤마 구분 객체 목록을 받는다 → 다중 테이블도 한 번에.
        if opts.tables:
            scope = ','.join(f'{opts.schema}.{t}' for t in opts.tables)
        else:
            scope = opts.schema
        # 재실행해도 깨지지 않게 멱등 형태(IF NOT EXISTS / OR REPLACE)로 변환.
        ddl = make_idempotent(export_objects_ddl(conn, scope))

        # 프로시저는 "테이블 단위" 백업에서만 따로 챙긴다.
        # (스키마 전체 덤프는 위 scope=schema 가 프로시저까지 이미 포함한다.)
        proc_ddl = ''
        proc_info = {'included': [], 'skipped': []}
        if opts.tables and opts.include_procedures:
            proc_ddl, proc_info = self._collect_procedure_ddls(conn, opts)

        path = os.path.join(outdir, 'schema.ddl.sql')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(f"-- Vertica DDL dump\n-- Source: {self.cfg.host}:{self.cfg.port}/{self.cfg.database}\n\n")
            fh.write(ddl or '')
            if ddl and not ddl.endswith('\n'):
                fh.write('\n')
            if proc_ddl:
                fh.write('\n\n-- ===== stored procedures =====\n\n')
                fh.write(proc_ddl)
                if not proc_ddl.endswith('\n'):
                    fh.write('\n')
        return path, proc_info

    def _schema_procedures(self, conn, schema: str) -> list:
        """스키마의 전체 프로시저 목록 (이름, 인자). 덤프 1회 내에서 캐시."""
        if schema not in self._proc_cache:
            self._proc_cache[schema] = list_procedures(conn, schema)
        return self._proc_cache[schema]

    def _collect_procedure_ddls(self, conn, opts: DumpOptions) -> tuple:
        """스키마의 프로시저 목록 중 '이름에 대상 테이블명이 포함된' 것들의 DDL 을 추출.
        (DM/DS 같은 명명 규칙을 가정하지 않는다. 단순 부분 일치.)
        존재하지 않거나 추출 실패한 건 건너뛰고 사유를 기록(부분 성공 허용).
        """
        info = {'included': [], 'skipped': []}
        all_procs = self._schema_procedures(conn, opts.schema)
        if not all_procs:
            return '', info

        # 이 작업의 대상 테이블명(보통 1개) 중 하나라도 프로시저명에 포함되면 매칭.
        wanted = [t.upper() for t in (opts.tables or [])]
        matched = [
            (name, args) for (name, args) in all_procs
            if any(t in name.upper() for t in wanted)
        ]

        chunks = []
        for name, args in matched:
            # EXPORT_OBJECTS 는 인자 있는 프로시저를 시그니처로 식별한다.
            sig = f'({args})' if args.strip() else ''
            scope = f'{opts.schema}.{name}{sig}'
            try:
                ddl = export_objects_ddl(conn, scope)
            except Exception as e:
                # 실패가 세션을 aborted 상태로 두지 않도록 롤백 (읽기전용이라 무해).
                try:
                    conn.rollback()
                except Exception:
                    pass
                reason = str(e).splitlines()[0]
                info['skipped'].append((name, reason))
                print(f"[v_dump] WARN procedure skip {opts.schema}.{name}: {reason}", file=sys.stderr)
                continue
            if ddl and ddl.strip():
                chunks.append(make_idempotent(ddl).rstrip() + '\n')
                info['included'].append(name)
            else:
                info['skipped'].append((name, 'export 결과 비어있음'))
                print(f"[v_dump] WARN procedure empty {opts.schema}.{name}", file=sys.stderr)
        return '\n'.join(chunks), info

    def _write_load_script(self, outdir: str, copy_statements: List[str]) -> str:
        path = os.path.join(outdir, 'load.sql')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(
                "-- Reload script. vsql 에서 이 파일이 있는 디렉토리로 cd 후:\n"
                "--   vsql -h <host> -U <user> -d <db> -f load.sql\n\n"
                "\\set ON_ERROR_STOP on\n"
                "BEGIN;\n\n"
            )
            for stmt in copy_statements:
                fh.write(stmt)
            fh.write("\nCOMMIT;\n")
        return path

    def _write_manifest(
        self,
        outdir: str,
        opts: DumpOptions,
        stats: dict,
        ddl_path: Optional[str],
        load_path: Optional[str],
        proc_info: Optional[dict] = None,
    ) -> None:
        proc_info = proc_info or {'included': [], 'skipped': []}
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if opts.tables:
            scope = f"{opts.schema}.{{{','.join(opts.tables)}}}"
        else:
            scope = opts.schema
        path = os.path.join(outdir, 'MANIFEST.txt')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(
                f"v_dump manifest\n"
                f"  host        : {self.cfg.host}:{self.cfg.port}\n"
                f"  database    : {self.cfg.database}\n"
                f"  scope       : {scope}\n"
                f"  dumped at   : {now}\n"
                f"  schema_only : {opts.schema_only}\n"
                f"  data_only   : {opts.data_only}\n"
                f"  tables      : {stats['tables']}\n"
                f"  rows total  : {stats['rows']}\n"
                f"  procedures  : {len(proc_info.get('included') or [])} included\n"
            )
            if ddl_path:
                fh.write(f"  ddl file    : {os.path.basename(ddl_path)}\n")
            if load_path:
                fh.write(f"  load script : {os.path.basename(load_path)}\n")
            fh.write("\nfiles:\n")
            for name, n in stats['files']:
                fh.write(f"  {name}\t{n} rows\n")
            if stats.get('failed'):
                fh.write("\nfailed:\n")
                for qname, reason in stats['failed']:
                    fh.write(f"  {qname}\t{reason}\n")
            if proc_info.get('included') or proc_info.get('skipped'):
                fh.write("\nprocedures:\n")
                for name in proc_info.get('included') or []:
                    fh.write(f"  {name}\tincluded\n")
                for name, reason in proc_info.get('skipped') or []:
                    fh.write(f"  {name}\tskipped ({reason})\n")

    # 형식 정보 (외부에서 참조)
    FORMAT = {
        'delimiter': '|',
        'null': r'\N',
        'escape': '\\',
        'record_terminator': '\\n',
        'encoding': 'utf-8',
    }
