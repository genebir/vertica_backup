# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Gibseung Yeom)
# License   : Apache-2.0 (see LICENSE)
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
def vertica_connection(cfg: ConnectionConfig, primary_index: int = 0):
    conn = vertica_python.connect(**cfg.to_vertica_kwargs(primary_index))
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
