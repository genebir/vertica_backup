#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# v_dump 통합 이미지 엔트리포인트. 첫 인자로 동작을 디스패치한다.
#
#   dump    <v_dump 인자...>     덤프  (python -m v_dump ...)
#   restore <dump_dir> [vsql..]  복원  (dump_dir 로 cd 후 vsql -f load.sql)
#   vsql    <vsql 인자...>       vsql 원시 실행 (임의 쿼리/점검용)
#   shell                        컨테이너 안 bash
#   help                         이 도움말
#
# 접속정보는 v_dump.config.build_config 로 단일 해석한다:
#   CLI(덤프 한정) > 환경변수 VERTICA_* > 마운트된 v_dump.yaml
###############################################################################
set -euo pipefail

usage() {
  cat <<'EOF'
v_dump 통합 이미지 (덤프 + 복원)

사용:
  dump    <v_dump 인자...>      -o /backup 아래 <schema>/<table> (전체는 <schema>/all) 로 생성
                               예) dump --schema SCH -o /backup          → /backup/SCH/all/
                               예) dump --schema SCH -t TB_A,TB_B -o /backup → /backup/SCH/TB_A , TB_B/
  restore <dump_dir> [옵션]     dump_dir 은 그 구조의 말단 폴더를 가리킨다
                               예) restore /backup/SCH/all                (스키마 전체)
                               예) restore /backup/SCH/TB_A               (단일 테이블)
                               예) restore /backup/SCH/all --with-ddl     (구조 생성 후 적재)
      옵션: -t/--table <T>   해당 테이블의 COPY 만 적재 (반복/콤마 가능)
            --with-ddl       데이터 적재 전 schema.ddl.sql(테이블+프로시저) 먼저 실행
  vsql    <vsql 인자...>        예) vsql -c "SELECT version()"
  shell                         컨테이너 내부 셸
  help                          도움말

접속정보 (우선순위: 환경변수 > 마운트한 v_dump.yaml):
  VERTICA_HOST  VERTICA_PORT  VERTICA_USER  VERTICA_PASSWORD
  VERTICA_DATABASE  VERTICA_TLSMODE

마운트:
  -v <호스트백업경로>:/backup          .dat / load.sql 입출력
  -v <호스트경로>/v_dump.yaml:/app/v_dump/v_dump.yaml:ro   (yaml 로 접속정보 줄 때)
EOF
}

# build_config 로 접속정보를 해석해 VHOST/VPORT/... 변수로 export.
# (env VERTICA_* 가 yaml 보다 우선 — build_config 가 그렇게 처리한다.)
load_conn() {
  eval "$(python - <<'PY'
import shlex
from v_dump.config import build_config
c = build_config()
print(f"VHOST={shlex.quote(c.host)}")
print(f"VPORT={c.port}")
print(f"VUSER={shlex.quote(c.user)}")
print(f"VPASS={shlex.quote(c.password)}")
print(f"VDB={shlex.quote(c.database)}")
print(f"VTLS={shlex.quote(c.tlsmode)}")
PY
)"
}

# load.sql 적재. 워커가 2개 이상이고 COPY 문이 2개 이상이면 COPY 를 세션 N개로
# 분산해 동시 적재한다(각 COPY 는 서로 다른 테이블이라 병렬 안전). 그 외엔 순차.
# 병렬도: V_DUMP_JOBS(auto=min(nproc,4) | 정수). 순차는 기존처럼 단일 트랜잭션,
# 병렬은 세션별 AUTOCOMMIT(테이블 단위 커밋).
_run_load() {
  local load_file="$1"; shift
  local passthru=("$@")
  local cores n jobs total
  cores="$(nproc 2>/dev/null || echo 1)"
  jobs="${V_DUMP_JOBS:-auto}"
  case "$jobs" in
    ''|auto|*[!0-9]*) n=$(( cores < 4 ? cores : 4 ));;
    *) n="$jobs";;
  esac
  total="$(grep -c '^COPY ' "$load_file" 2>/dev/null || echo 0)"

  if [[ "$n" -le 1 || "$total" -le 1 ]]; then
    vsql -h "$VHOST" -p "$VPORT" -U "$VUSER" -w "$VPASS" -d "$VDB" \
         -v ON_ERROR_STOP=on -f "$load_file" "${passthru[@]}"
    return $?
  fi

  echo "[restore] 병렬 적재: ${n} sessions, ${total} COPY" >&2
  local tmp; tmp="$(mktemp -d)"
  grep '^COPY ' "$load_file" | awk -v n="$n" -v d="$tmp" '{ print > (d "/b." (NR % n) ".copy") }'
  local pids=() ks=() rc=0 k i
  for ((k=0; k<n; k++)); do
    local cf="$tmp/b.$k.copy"
    [[ -s "$cf" ]] || continue
    { echo '\set ON_ERROR_STOP on'; echo '\set AUTOCOMMIT on'; cat "$cf"; } > "$tmp/s.$k.sql"
    vsql -h "$VHOST" -p "$VPORT" -U "$VUSER" -w "$VPASS" -d "$VDB" \
         -f "$tmp/s.$k.sql" "${passthru[@]}" > "$tmp/s.$k.log" 2>&1 &
    pids+=("$!"); ks+=("$k")
  done
  for i in "${!pids[@]}"; do
    if ! wait "${pids[$i]}"; then
      rc=1
      echo "[restore] 세션 ${ks[$i]} 오류:" >&2
      grep -iE 'ERROR|ROLLBACK' "$tmp/s.${ks[$i]}.log" | head -3 >&2 || true
    fi
  done
  echo "[restore] 병렬 적재 종료 (rc=$rc)" >&2
  rm -rf "$tmp"
  return $rc
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  dump)
    exec python -m v_dump "$@"
    ;;

  restore)
    dir="${1:-}"
    if [[ -z "$dir" ]]; then
      echo "[restore] dump_dir 인자가 필요합니다. 예: restore /backup/SCH/all" >&2
      exit 2
    fi
    shift
    if [[ ! -f "$dir/load.sql" ]]; then
      echo "[restore] $dir/load.sql 이 없습니다. (덤프 결과 디렉토리가 맞나요?)" >&2
      exit 2
    fi

    # 선택 복원: -t/--table 로 일부 테이블만. 그 외 인자는 vsql 로 통과.
    tables=()
    passthru=()
    with_ddl=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -t|--table)
          [[ -n "${2:-}" ]] || { echo "[restore] $1 값 누락" >&2; exit 2; }
          # 콤마 입력도 허용: -t A,B
          IFS=',' read -ra _ts <<< "$2"
          for _t in "${_ts[@]}"; do [[ -n "$_t" ]] && tables+=("$_t"); done
          shift 2 ;;
        --with-ddl)
          with_ddl=1; shift ;;
        *) passthru+=("$1"); shift ;;
      esac
    done

    load_conn
    # FROM LOCAL 은 vsql 클라이언트의 상대경로 기준 → load.sql 디렉토리로 진입.
    cd "$dir"
    # tlsmode 를 vsql sslmode 로 전달 (disable/prefer/require 호환). 아래 vsql 모두 적용.
    export VSQL_SSLMODE="$VTLS"

    # --with-ddl: 데이터 적재 전에 구조(테이블+프로시저) DDL 을 먼저 생성.
    # DDL 은 멱등 형태(IF NOT EXISTS / OR REPLACE)라 이미 있는 객체엔 그냥 넘어간다.
    # 다만 제약조건(ALTER ... ADD CONSTRAINT)은 멱등이 아니므로, 혹시 모를 중복 오류로
    # 멈추지 않게 ON_ERROR_STOP 은 끈 채로 둔다.
    if [[ "$with_ddl" == 1 ]]; then
      if [[ ! -f schema.ddl.sql ]]; then
        echo "[restore] --with-ddl 인데 schema.ddl.sql 이 없습니다." >&2
        exit 2
      fi
      echo "[restore] DDL 적용: $(pwd)/schema.ddl.sql" >&2
      vsql -h "$VHOST" -p "$VPORT" -U "$VUSER" -w "$VPASS" -d "$VDB" -f schema.ddl.sql || true
    fi

    load_file=load.sql
    if [[ ${#tables[@]} -gt 0 ]]; then
      # 지정 테이블의 COPY 문만 추려 load.filtered.sql 생성 (없는 테이블이면 실패).
      python /usr/local/bin/filter_load.py "${tables[@]}"
      load_file=load.filtered.sql
      echo "[restore] 선택 복원: ${tables[*]}" >&2
    fi

    echo "[restore] $VHOST:$VPORT/$VDB ← $(pwd)/$load_file" >&2
    _run_load "$load_file" "${passthru[@]}"
    exit $?
    ;;

  vsql)
    load_conn
    export VSQL_SSLMODE="$VTLS"
    exec vsql -h "$VHOST" -p "$VPORT" -U "$VUSER" -w "$VPASS" -d "$VDB" "$@"
    ;;

  shell)
    exec /bin/bash
    ;;

  help|-h|--help)
    usage
    ;;

  *)
    echo "[entrypoint] 알 수 없는 명령: $cmd" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac
