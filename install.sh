#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# v_dump 설치 스크립트 (멱등).
#
# 아무것도 설치되지 않은 환경 가정. 여러 번 실행해도 안전하다.
#   - python3 (>=3.8) 탐지
#   - v_dump/.venv 생성 (없을 때만)
#   - requirements.txt 설치 (pip 는 멱등)
#   - v_dump.yaml 없으면 example 로 생성 (기존 설정은 절대 덮어쓰지 않음)
#   - run.sh 실행권한 부여
#   - import 검증
#
# 사용:
#   ./install.sh                 # 기본
#   PYTHON=/usr/bin/python3.11 ./install.sh   # 특정 인터프리터 지정
###############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../v_dump
VENV_DIR="$HERE/.venv"
REQ="$HERE/requirements.txt"
MIN_MAJOR=3
MIN_MINOR=8

log()  { echo "[install] $*"; }
die()  { echo "[install] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1) python3 인터프리터 탐지
#    venv + ensurepip 둘 다 되고 버전이 충분한 것만 채택.
# ---------------------------------------------------------------------------
capable() {  # $1=interpreter : 버전/venv/ensurepip 전부 만족하면 0
  local p="$1" maj min
  command -v "$p" >/dev/null 2>&1 || return 1
  read -r maj min < <("$p" -c 'import sys;print(sys.version_info.major, sys.version_info.minor)' 2>/dev/null) || return 1
  (( maj > MIN_MAJOR || (maj == MIN_MAJOR && min >= MIN_MINOR) )) || return 1
  "$p" -c 'import venv, ensurepip' >/dev/null 2>&1 || return 1
  return 0
}

pick_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    capable "$PYTHON" || die "PYTHON='$PYTHON' 가 부적합 (>=${MIN_MAJOR}.${MIN_MINOR} + venv + ensurepip 필요)."
    echo "$PYTHON"; return
  fi
  for cand in python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3 python; do
    if capable "$cand"; then echo "$cand"; return; fi
  done
  die "사용 가능한 python(>=${MIN_MAJOR}.${MIN_MINOR}, venv+ensurepip 포함) 을 찾지 못했습니다.
       Debian/Ubuntu: sudo apt install python3-venv
       RHEL/Rocky   : sudo dnf install python3
       또는 PYTHON=/path/to/python3 ./install.sh 로 지정."
}

PY_BIN="$(pick_python)"
read -r PY_MAJOR PY_MINOR < <("$PY_BIN" -c 'import sys;print(sys.version_info.major, sys.version_info.minor)')
log "python: $PY_BIN (${PY_MAJOR}.${PY_MINOR})"

# ---------------------------------------------------------------------------
# 2) venv 생성 (멱등). pip 가 없는 깨진 venv 는 재생성.
# ---------------------------------------------------------------------------
venv_ok() { [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; }

if venv_ok; then
  log "venv 이미 정상: $VENV_DIR (재사용)"
else
  if [[ -e "$VENV_DIR" ]]; then
    log "기존 venv 가 불완전 → 재생성"
    rm -rf "$VENV_DIR"
  fi
  log "venv 생성: $VENV_DIR"
  "$PY_BIN" -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"

# ---------------------------------------------------------------------------
# 3) 의존성 설치 (멱등 — 이미 만족되어 있으면 pip 가 알아서 skip)
# ---------------------------------------------------------------------------
[[ -f "$REQ" ]] || die "requirements.txt 가 없습니다: $REQ"
log "pip 업그레이드"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
log "의존성 설치: $REQ"
"$VENV_PY" -m pip install -r "$REQ"

# ---------------------------------------------------------------------------
# 4) 설정 파일 시드 (기존 것은 절대 덮어쓰지 않음)
# ---------------------------------------------------------------------------
if [[ -f "$HERE/v_dump/v_dump.yaml" ]]; then
  log "v_dump.yaml 이미 존재 → 보존 (덮어쓰지 않음)"
elif [[ -f "$HERE/v_dump/v_dump.yaml.example" ]]; then
  cp "$HERE/v_dump/v_dump.yaml.example" "$HERE/v_dump/v_dump.yaml"
  log "v_dump.yaml 생성됨 (example 복사) → 접속 정보를 채우세요: $HERE/v_dump/v_dump.yaml"
else
  log "WARN: v_dump.yaml.example 없음 → 설정 파일 시드 건너뜀"
fi

# ---------------------------------------------------------------------------
# 5) 실행권한
# ---------------------------------------------------------------------------
chmod +x "$HERE/run.sh" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 6) 검증
# ---------------------------------------------------------------------------
log "import 검증"
"$VENV_PY" -c "import vertica_python, yaml; print('  vertica_python', vertica_python.__version__, '| PyYAML', yaml.__version__)"
env PYTHONPATH="$HERE" "$VENV_PY" -m v_dump --help >/dev/null \
  && log "CLI OK"

cat <<EOF

[install] 완료.

다음 단계:
  1) 접속 정보 입력:   $HERE/v_dump/v_dump.yaml
  2) 실행:             $HERE/run.sh --schema <SCHEMA> -o ./backup
  (선택) 별칭 등록:    echo "alias v_dump=$HERE/run.sh" >> ~/.bashrc && source ~/.bashrc

EOF
