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


@dataclass
class ConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    tlsmode: str = 'disable'

    def to_vertica_kwargs(self) -> dict:
        return {
            'host': self.host,
            'port': self.port,
            'user': self.user,
            'password': self.password,
            'database': self.database,
            'tlsmode': self.tlsmode,
            # 깨진 UTF-8 바이트가 섞인 컬럼이 있어도 fetch 가 중단되지 않게.
            # 손상 위치는 U+FFFD 로 치환된다.
            'unicode_error': 'replace',
        }


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
