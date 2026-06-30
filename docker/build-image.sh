#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Gibseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# v_dump 통합 이미지 빌드 (인터넷 되는 빌드 머신에서 실행).
#
#   ./docker/build-image.sh            # 이미지 빌드만
#   ./docker/build-image.sh --save     # 빌드 + tar 저장(폐쇄망 전송용)
#
# 산출물(--save): v_dump-image.tar  → 폐쇄망으로 옮긴 뒤:
#   podman load -i v_dump-image.tar     (또는 docker load -i ...)
#
# docker / podman 중 설치된 것을 자동 선택한다.
###############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../v_dump/docker
ROOT="$(dirname "$HERE")"                              # .../v_dump  (= build context)

IMAGE="${IMAGE:-v_dump:latest}"
OUT_TAR="${OUT_TAR:-$ROOT/v_dump-image.tar}"

# 컨테이너 런타임 선택. ENGINE 환경변수로 강제 가능(docker|podman).
if [[ -n "${ENGINE:-}" ]]; then
  command -v "$ENGINE" >/dev/null 2>&1 || { echo "[build] ENGINE='$ENGINE' 명령을 찾을 수 없습니다." >&2; exit 1; }
elif command -v podman >/dev/null 2>&1; then
  ENGINE=podman
elif command -v docker >/dev/null 2>&1; then
  ENGINE=docker
else
  echo "[build] docker 도 podman 도 없습니다." >&2
  exit 1
fi
echo "[build] engine: $ENGINE / image: $IMAGE"

# vsql 은 x86_64 → 이미지도 amd64 로 고정.
# CACHEBUST 에 매 빌드마다 다른 값(에포크초)을 줘서 소스 COPY 레이어를 항상 새로 굽는다.
# (buildkit 캐시가 소스 변경을 못 잡고 옛 코드를 재사용하는 사고 방지. base 레이어는 캐시 유지.)
CACHEBUST="$(date +%s 2>/dev/null || echo $$)"
"$ENGINE" build --platform linux/amd64 \
  --build-arg "CACHEBUST=$CACHEBUST" \
  -f "$ROOT/Dockerfile" -t "$IMAGE" "$ROOT"

echo "[build] 빌드 완료: $IMAGE (CACHEBUST=$CACHEBUST)"

if [[ "${1:-}" == "--save" ]]; then
  echo "[build] 이미지 저장: $OUT_TAR"
  "$ENGINE" save -o "$OUT_TAR" "$IMAGE"
  echo "[build] 완료. 폐쇄망에서:  $ENGINE load -i $(basename "$OUT_TAR")"
fi
