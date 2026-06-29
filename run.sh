#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# v_dump 런처 (자체 venv 사용, 위치 독립적).
# install.sh 로 만든 v_dump/.venv 를 쓰고, 패키지 import 를 위해
# PYTHONPATH 에 v_dump 의 부모 디렉토리를 넣는다.
# 어느 위치에서 호출해도 동작한다.
###############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../v_dump
PARENT="$(dirname "$HERE")"
VENV_PY="$HERE/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[v_dump] venv 가 없습니다. 먼저 설치하세요:" >&2
  echo "  $HERE/install.sh" >&2
  exit 1
fi

exec env PYTHONPATH="$PARENT" "$VENV_PY" -m v_dump "$@"
