# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/config.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : Vertica 접속 설정 dataclass + 로더.
#                   CLI 인자 > 환경변수 > yaml config 순으로 우선순위 결정.
###############################################################################

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

# v_dump/ 패키지와 같은 위치의 v_dump.yaml 을 기본 설정으로 자동 로드.
DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parent / 'v_dump.yaml')

# 노드당 연결 시도 타임아웃(초). 멀티노드 failover 가 빠르게 다음 노드로 넘어가게 한다.
CONNECT_TIMEOUT = 8


@dataclass
class ConnectionConfig:
    host: str          # 'node1' 또는 멀티노드 'node1,node2,node3'
    port: int
    user: str
    password: str
    database: str
    tlsmode: str = 'disable'

    @property
    def hosts(self) -> list:
        """host 를 콤마로 분해한 노드 목록 (멀티노드)."""
        hs = [h.strip() for h in (self.host or '').split(',') if h.strip()]
        return hs or [self.host]

    def to_vertica_kwargs(self, primary_index: int = 0) -> dict:
        """primary_index 노드를 1순위로, 나머지는 backup_server_node 로(자동 failover).
        워커마다 primary_index 를 돌리면 커넥션이 노드들에 분산돼 단일노드 병목을 푼다.
        """
        hs = self.hosts
        n = len(hs)
        primary = hs[primary_index % n]
        backups = [hs[(primary_index + i) % n] for i in range(1, n)]
        kw = {
            'host': primary,
            'port': self.port,
            'user': self.user,
            'password': self.password,
            'database': self.database,
            'tlsmode': self.tlsmode,
            # 깨진 UTF-8 바이트가 섞인 컬럼이 있어도 fetch 가 중단되지 않게.
            # 손상 위치는 U+FFFD 로 치환된다.
            'unicode_error': 'replace',
            # 응답 없는 노드(블랙홀)에 무한정 매달리지 않게 → failover 가 실제로 작동.
            'connection_timeout': CONNECT_TIMEOUT,
        }
        if backups:
            kw['backup_server_node'] = backups   # 1순위 노드 불가 시 자동 failover
        return kw


def _from_yaml(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return (data.get('vertica') or {})


def build_config(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    tlsmode: Optional[str] = None,
    config_path: Optional[str] = None,
) -> ConnectionConfig:
    resolved_path = config_path or (
        DEFAULT_CONFIG_PATH if os.path.exists(DEFAULT_CONFIG_PATH) else None
    )
    file_cfg: dict = {}
    if resolved_path and os.path.exists(resolved_path):
        file_cfg = _from_yaml(resolved_path)

    def pick(cli_val, env_key, file_key, default=None):
        if cli_val is not None and cli_val != '':
            return cli_val
        env_val = os.environ.get(env_key)
        if env_val:
            return env_val
        if file_key in file_cfg and file_cfg[file_key] is not None:
            return file_cfg[file_key]
        return default

    resolved_host = pick(host, 'VERTICA_HOST', 'host')
    resolved_port = int(pick(port, 'VERTICA_PORT', 'port', 5433))
    resolved_user = pick(user, 'VERTICA_USER', 'username')
    resolved_password = pick(password, 'VERTICA_PASSWORD', 'password', '')
    resolved_db = pick(database, 'VERTICA_DATABASE', 'dbname')
    resolved_tls = pick(tlsmode, 'VERTICA_TLSMODE', 'tlsmode', 'disable')

    missing = [k for k, v in {
        'host': resolved_host,
        'user': resolved_user,
        'database': resolved_db,
    }.items() if not v]
    if missing:
        raise ValueError(f"Missing required connection fields: {', '.join(missing)}")

    return ConnectionConfig(
        host=resolved_host,
        port=resolved_port,
        user=resolved_user,
        password=resolved_password or '',
        database=resolved_db,
        tlsmode=resolved_tls,
    )
