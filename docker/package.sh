#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# 폐쇄망 반입용 "원샷 패키징" — 빌드 머신(인터넷 O)에서 한 번 실행하면:
#   1) 이미지 빌드
#   2) 이미지 tar 저장
#   3) v_dump-docker.sh + v_dump.yaml + 로더(LOAD-ME.sh) 까지 한 폴더로 묶어
#      v_dump-deploy.tar.gz 하나로 출력
#
#   ./docker/package.sh
#
# 폐쇄망 호스트에서:
#   tar xzf v_dump-deploy.tar.gz && cd v_dump-deploy
#   ./LOAD-ME.sh                       # podman/docker load + 권한
#   ./v_dump-docker.sh dump --schema YOUR_SCHEMA
###############################################################################
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../v_dump/docker
ROOT="$(dirname "$HERE")"                              # .../v_dump

STAGE="$ROOT/v_dump-deploy"
OUT="${OUT:-$ROOT/v_dump-deploy.tar.gz}"

log() { echo "[package] $*"; }

# ---------------------------------------------------------------------------
# 1) 빌드 + 이미지 tar 저장 (build-image.sh 재사용: cache-bust 빌드 + save).
#    OUT_TAR 로 저장 위치를 스테이징 폴더로 지정.
# ---------------------------------------------------------------------------
rm -rf "$STAGE"
mkdir -p "$STAGE"
log "이미지 빌드 + 저장"
OUT_TAR="$STAGE/v_dump-image.tar" "$HERE/build-image.sh" --save

# ---------------------------------------------------------------------------
# 2) 실행 래퍼 + 접속정보 yaml 동봉
# ---------------------------------------------------------------------------
cp "$HERE/v_dump-docker.sh" "$STAGE/"
if [[ -f "$ROOT/v_dump.yaml" ]]; then
  cp "$ROOT/v_dump.yaml" "$STAGE/"
  log "v_dump.yaml 포함 (⚠ 평문 비밀번호 — 전송 경로 주의)"
else
  cp "$ROOT/v_dump.yaml.example" "$STAGE/v_dump.yaml"
  log "v_dump.yaml 없음 → example 복사 (반입 후 접속정보 채울 것)"
fi

# ---------------------------------------------------------------------------
# 3) 폐쇄망용 로더 + 안내
# ---------------------------------------------------------------------------
cat > "$STAGE/LOAD-ME.sh" <<'LOAD'
#!/usr/bin/env bash
# 폐쇄망 호스트에서 실행: 이미지 등록 + 실행 준비.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v podman >/dev/null 2>&1; then ENGINE=podman
elif command -v docker >/dev/null 2>&1; then ENGINE=docker
else echo "podman/docker 가 필요합니다." >&2; exit 1; fi
echo "[load] $ENGINE load -i v_dump-image.tar"
"$ENGINE" load -i v_dump-image.tar
chmod +x v_dump-docker.sh
echo "[load] 준비 완료. 접속 확인:  ./v_dump-docker.sh vsql -c \"SELECT version()\""
echo "[load] 백업 예:             ./v_dump-docker.sh dump --schema YOUR_SCHEMA"
LOAD

cat > "$STAGE/README.txt" <<'TXT'
v_dump 폐쇄망 배포 묶음
=======================
1) ./LOAD-ME.sh                 # 이미지 등록(podman/docker load) + 권한
2) (필요시) v_dump.yaml 에 접속정보 입력
3) ./v_dump-docker.sh dump --schema YOUR_SCHEMA
   ./v_dump-docker.sh restore YOUR_SCHEMA/all --with-ddl

백업 결과는 이 폴더의 backup/ 아래에 생긴다.
TXT

chmod +x "$STAGE/LOAD-ME.sh" "$STAGE/v_dump-docker.sh"

# ---------------------------------------------------------------------------
# 4) 단일 아카이브로 묶기
# ---------------------------------------------------------------------------
log "묶는 중: $OUT"
tar czf "$OUT" -C "$ROOT" "$(basename "$STAGE")"
rm -rf "$STAGE"

SIZE="$(du -h "$OUT" | cut -f1)"
log "완료: $OUT ($SIZE)"
cat <<EOF

[package] 폐쇄망 호스트에서:
  tar xzf $(basename "$OUT") && cd v_dump-deploy
  ./LOAD-ME.sh
  ./v_dump-docker.sh dump --schema YOUR_SCHEMA
EOF
