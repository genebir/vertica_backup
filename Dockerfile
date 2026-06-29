# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# v_dump 통합 이미지 (덤프 + 복원).
#
#  - python + vertica-python(순수 파이썬 드라이버)  → 덤프
#  - vsql (vertica-client)                          → 복원(COPY FROM LOCAL)
#
# 빌드는 "인터넷 되는 빌드 머신"에서 한다. 폐쇄망에는 결과 이미지를
# `podman save | load` (또는 docker)로 tar 전송한다. build-image.sh 참고.
#
# build context = v_dump/ (이 Dockerfile 이 있는 디렉토리)
#   docker build -f Dockerfile -t v_dump:latest .
#
# vsql 은 x86_64 바이너리이므로 이미지도 amd64 여야 한다.
# arm64 호스트에서 빌드한다면:  --platform linux/amd64
###############################################################################
FROM --platform=linux/amd64 python:3.12-slim

# vsql 이 동적 링크하는 시스템 라이브러리. slim 에는 빠져 있어 명시 설치.
# (libssl/libcrypto, readline 계열. 없으면 vsql 이 .so 못 찾고 죽는다.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        libssl3 \
        libreadline8 \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 1) 파이썬 의존성 (빌드 머신이 인터넷 → pip 가 base 파이썬에 맞는 휠을 받음.
#    번들 휠의 cp314 버전 핀 문제를 자연스럽게 회피.)
# ---------------------------------------------------------------------------
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt

# ---------------------------------------------------------------------------
# 2) vsql 설치 (tar 안의 opt/vertica/ 를 / 로 풀면 /opt/vertica/bin/vsql)
# ---------------------------------------------------------------------------
COPY vertica-client-24.2.0-1.x86_64.tar.gz /tmp/vclient.tar.gz
RUN tar xzf /tmp/vclient.tar.gz -C / opt/vertica/bin/vsql opt/vertica/lib64 \
 && rm -f /tmp/vclient.tar.gz
ENV PATH="/opt/vertica/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/vertica/lib64"
# vsql 이 라이브러리를 다 찾는지 빌드 시점에 검증 (못 찾으면 여기서 실패).
RUN vsql --version

# ---------------------------------------------------------------------------
# 3) v_dump 패키지 배치 (import 이름 'v_dump' → PYTHONPATH=/app)
# ---------------------------------------------------------------------------
# CACHEBUST: 빌드 때마다 다른 값을 주면 이 줄 이후(소스 COPY)는 항상 새로 굽는다.
# 위의 무거운 레이어(apt/pip/vsql)는 캐시 유지. → 소스 변경이 항상 반영되도록 보장.
ARG CACHEBUST=0
WORKDIR /app
COPY __init__.py __main__.py cli.py config.py connection.py \
     data.py ddl.py dumper.py escape.py inspector.py progress.py \
     /app/v_dump/
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/filter_load.py /usr/local/bin/filter_load.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/filter_load.py

# 작업 기본 디렉토리. 호스트의 백업 폴더를 여기에 마운트한다.
WORKDIR /backup

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["help"]
