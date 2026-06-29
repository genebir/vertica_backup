#!/usr/bin/env bash
###############################################################################
# 폐쇄망 호스트용 실행 래퍼. 컨테이너 마운트/접속정보를 알아서 채워준다.
#
# 준비:
#   1) 빌드 머신에서 build-image.sh --save → v_dump-image.tar 전송
#   2) 폐쇄망에서:  podman load -i v_dump-image.tar   (또는 docker)
#   3) 접속정보: 아래 둘 중 하나
#        a) 환경변수:  export VERTICA_HOST=... VERTICA_USER=... VERTICA_PASSWORD=... VERTICA_DATABASE=...
#        b) yaml:      v_dump.yaml (실행위치/backup 옆/스크립트 부모 순으로 자동 탐색)
#
# 사용 (백업 디렉토리·-o 자동 — 신경 쓸 필요 없음):
#   ./v_dump-docker.sh dump --schema BDA_DM_DB           # → backup/BDA_DM_DB/all/
#   ./v_dump-docker.sh dump --schema DS -t TB_A,TB_B     # → backup/DS/TB_A , TB_B/
#   ./v_dump-docker.sh restore DS/TB_A                   # 말단 폴더 (backup 기준 상대경로)
#   ./v_dump-docker.sh restore DS/TB_A --with-ddl
#   ./v_dump-docker.sh vsql -c "SELECT version()"
#   ./v_dump-docker.sh help
#
# 핵심: 백업 폴더는 이 스크립트가 있는 경로의 backup/ 에 생기고 컨테이너 /backup 로 마운트된다.
#       dump 의 출력 베이스는 자동으로 /backup(-o 불필요), restore 경로는 backup 기준 상대경로.
###############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../v_dump/docker

IMAGE="${IMAGE:-v_dump:latest}"
# 백업 디렉토리는 "이 스크립트(v_dump-docker.sh)가 있는 경로" 아래 backup/ 으로 잡는다.
# 즉 어디서 실행하든 항상 스크립트 옆에 backup/ 이 생긴다. 따로 지정할 필요 없음.
# 꼭 다른 곳에 두고 싶을 때만 BACKUP_DIR 환경변수로 덮어쓸 수 있다.
BACKUP_DIR="${BACKUP_DIR:-$HERE/backup}"

# 접속정보 yaml 자동 탐색.
# V_DUMP_YAML 을 명시했으면 그걸 쓰고, 아니면 아래 후보를 순서대로 찾는다.
# (한 번 채워두면 매번 env/경로를 안 넘겨도 됨. env VERTICA_* 가 있으면 그게 우선.)
V_DUMP_YAML="${V_DUMP_YAML:-}"
if [[ -z "$V_DUMP_YAML" ]]; then
  for cand in "$PWD/v_dump.yaml" "$BACKUP_DIR/v_dump.yaml" "$HERE/../v_dump.yaml"; do
    if [[ -f "$cand" ]]; then V_DUMP_YAML="$cand"; break; fi
  done
fi

# 런타임 선택
if command -v podman >/dev/null 2>&1; then
  ENGINE=podman
elif command -v docker >/dev/null 2>&1; then
  ENGINE=docker
else
  echo "[run] docker 도 podman 도 없습니다." >&2
  exit 1
fi

# help 는 마운트 없이 바로.
if [[ "${1:-help}" == "help" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  exec "$ENGINE" run --rm "$IMAGE" help
fi

mkdir -p "$BACKUP_DIR"
echo "[run] backup dir: $BACKUP_DIR  →  컨테이너 /backup" >&2

# 컨테이너를 호출자 UID/GID 로 실행 → /backup 에 떨어지는 산출물이 host 소유가 된다
# (안 그러면 컨테이너 root 가 만든 파일이라 host 에서 못 지움). 앱은 /backup 과 /app 만
# 건드리므로 비루트로도 정상 동작한다.
# USER/HOME 도 준다: uid 가 컨테이너 passwd 에 없을 때 나는 getpwuid 경고를 막는다.
RUNAS=(--user "$(id -u):$(id -g)" -e USER=vdump -e HOME=/tmp)

# 마운트 구성
MOUNTS=(-v "$BACKUP_DIR:/backup:z")

# 접속정보: yaml 을 줬으면 컨테이너 안 기본 경로로 마운트(ro).
if [[ -n "$V_DUMP_YAML" ]]; then
  [[ -f "$V_DUMP_YAML" ]] || { echo "[run] V_DUMP_YAML 파일 없음: $V_DUMP_YAML" >&2; exit 2; }
  MOUNTS+=(-v "$V_DUMP_YAML:/app/v_dump/v_dump.yaml:ro,z")
fi

# 환경변수 전달 (설정된 것만).
ENVS=()
for k in VERTICA_HOST VERTICA_PORT VERTICA_USER VERTICA_PASSWORD VERTICA_DATABASE VERTICA_TLSMODE; do
  if [[ -n "${!k:-}" ]]; then ENVS+=(-e "$k=${!k}"); fi
done

# dump 는 출력 베이스가 항상 /backup 이다. 사용자가 -o 를 안 줘도(또는 주지 않도록)
# 자동으로 -o /backup 을 붙인다 → 결과는 backup/<schema>/<table> 로 떨어진다.
# (상대경로 -o 로 인한 /backup/backup 중복을 막기 위해 절대경로로 고정.)
ARGS=("$@")
if [[ "${1:-}" == "dump" ]]; then
  has_o=0
  for a in "${ARGS[@]}"; do
    case "$a" in -o|--output|-o=*|--output=*) has_o=1; break ;; esac
  done
  if [[ "$has_o" -eq 0 ]]; then
    ARGS+=(-o /backup)
  else
    echo "[run] 알림: dump 출력 베이스는 항상 /backup 입니다. -o 는 무시하고 /backup 을 권장합니다." >&2
  fi
fi

exec "$ENGINE" run --rm "${RUNAS[@]}" "${MOUNTS[@]}" "${ENVS[@]}" "$IMAGE" "${ARGS[@]}"
