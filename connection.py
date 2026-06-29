# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/connection.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : Vertica 커넥션 컨텍스트 매니저.
###############################################################################

import warnings
from contextlib import contextmanager

import vertica_python

from v_dump.config import ConnectionConfig

warnings.filterwarnings('ignore', category=UserWarning, module=r'vertica_python\..*')


@contextmanager
def vertica_connection(cfg: ConnectionConfig):
    conn = vertica_python.connect(**cfg.to_vertica_kwargs())
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
