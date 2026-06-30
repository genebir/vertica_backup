# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/cli.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : v_dump CLI. -o 는 베이스 경로이고 그 아래로
#                   <schema>/<table> (전체는 <schema>/all) 구조가 만들어진다.
#   python -m v_dump --schema MY_SCHEMA -o ./backup           # → ./backup/MY_SCHEMA/all/
#   python -m v_dump --schema MY_SCHEMA -t TB_SAMPLE -o ./backup  # → ./backup/MY_SCHEMA/TB_SAMPLE/
#   python -m v_dump --schema MY_SCHEMA -t TB_A,TB_B -o ./backup     # → ./backup/MY_SCHEMA/{TB_A,TB_B}/ 각각
#   # -t 백업 시, 같은 스키마에서 이름에 테이블명이 포함된 프로시저 DDL 도 같이 추출(기본 ON)
#   python -m v_dump --schema MY_SCHEMA -t TB_SAMPLE -o ./backup        # +프로시저
#   python -m v_dump --schema MY_SCHEMA -t TB_SAMPLE --no-procedures -o ./backup
###############################################################################

import argparse
import os
import sys
from typing import Optional, Sequence

from v_dump.config import build_config
from v_dump.dumper import DumpOptions, VerticaDumper


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='v_dump',
        description='Vertica 데이터 백업 도구. 파이프 구분 .dat 파일 + DDL + reload 스크립트 생성.',
    )

    target = p.add_argument_group('target')
    target.add_argument('--schema', '-n', required=True, help='덤프할 스키마명 (필수)')
    target.add_argument(
        '--table', '-t', action='append', metavar='TABLE',
        help='특정 테이블만. 반복(-t A -t B) 또는 콤마(-t A,B) 로 여러 개 지정 가능. '
             '생략 시 스키마 전체.',
    )

    mode = p.add_argument_group('mode').add_mutually_exclusive_group()
    mode.add_argument('--schema-only', action='store_true', help='DDL 만 (데이터 파일 X)')
    mode.add_argument('--data-only', action='store_true', help='데이터 파일만 (DDL/load.sql X)')

    proc = p.add_argument_group('procedures').add_mutually_exclusive_group()
    proc.add_argument(
        '--with-procedures', dest='procedures', action='store_true', default=None,
        help='테이블 단위(-t) 백업 시, 같은 스키마에서 이름에 테이블명이 포함된 '
             '프로시저 DDL 도 함께 추출 (기본 ON).',
    )
    proc.add_argument(
        '--no-procedures', dest='procedures', action='store_false',
        help='프로시저 DDL 추출 끔.',
    )

    comp = p.add_argument_group('compression')
    comp.add_argument(
        '--compress', action='store_true',
        help='.dat 를 gzip(.dat.gz)으로 저장 → 전송/보관 용량 대폭 감소. '
             '복원은 load.sql 의 GZIP 필터로 자동(무손실).',
    )

    out = p.add_argument_group('output')
    out.add_argument(
        '--output', '-o',
        required=True,
        help='출력 베이스 디렉토리. 이 아래에 <schema>/<table> (전체는 <schema>/all) 구조로 '
             '.dat / schema.ddl.sql / load.sql / MANIFEST.txt 가 생성된다.',
    )

    conn = p.add_argument_group('connection')
    conn.add_argument('--host')
    conn.add_argument('--port', type=int)
    conn.add_argument('--user', '-U')
    conn.add_argument('--password', '-W')
    conn.add_argument('--database', '-d')
    conn.add_argument('--tlsmode', choices=['disable', 'prefer', 'require'])
    conn.add_argument(
        '--config',
        help='yaml 설정 파일. 기본값은 v_dump/v_dump.yaml. CLI/env 값이 우선.',
    )

    return p


def _normalize_tables(raw: Optional[Sequence[str]]) -> Optional[list]:
    """--table 입력(반복/콤마 혼용)을 평탄화. 공백 제거 + 순서 유지 중복 제거."""
    if not raw:
        return None
    flat = []
    for item in raw:
        flat.extend(t.strip() for t in item.split(',') if t.strip())
    seen = set()
    return [t for t in flat if not (t in seen or seen.add(t))] or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        cfg = build_config(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            tlsmode=args.tlsmode,
            config_path=args.config,
        )
    except ValueError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    tables = _normalize_tables(args.table)
    include_proc = (args.procedures is not False)   # 기본 ON, --no-procedures 만 OFF

    # 출력 레이아웃: <output> 을 베이스로 보고 그 아래에 구조를 만든다.
    #   - 테이블 지정(-t) : <output>/<schema>/<table>   (테이블마다 자기완결 덤프)
    #   - 스키마 전체       : <output>/<schema>/all
    jobs = []  # (label, outdir, DumpOptions)
    if tables:
        for t in tables:
            outdir = os.path.join(args.output, args.schema, t)
            jobs.append((
                f"{args.schema}.{t}",
                outdir,
                DumpOptions(
                    schema=args.schema, tables=[t],
                    schema_only=args.schema_only, data_only=args.data_only,
                    include_procedures=include_proc, compress=args.compress,
                ),
            ))
    else:
        outdir = os.path.join(args.output, args.schema, 'all')
        jobs.append((
            args.schema,
            outdir,
            DumpOptions(
                schema=args.schema, tables=None,
                schema_only=args.schema_only, data_only=args.data_only,
                include_procedures=include_proc, compress=args.compress,
            ),
        ))

    dumper = VerticaDumper(cfg)
    try:
        results = dumper.dump_jobs(jobs)   # 커넥션 1개로 전체 작업 처리
    except Exception as e:
        print(f"[v_dump] connection failed: {e}", file=sys.stderr)
        return 1

    rc = 0
    for label, outdir, stats, err in results:
        if err is not None:
            print(f"[v_dump] dump failed: {label}: {err}", file=sys.stderr)
            rc = 1
            continue
        failed = len(stats.get('failed') or [])
        print(
            f"[v_dump] {label} → tables={stats['tables']} rows={stats['rows']}"
            f"{(' failed=' + str(failed)) if failed else ''} "
            f"→ {outdir}/",
            file=sys.stderr,
        )
    return rc
