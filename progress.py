###############################################################################
# File            : v_dump/progress.py
# Desc            : 데이터 덤프 진행률 표시. 터미널이면 stderr 에 \r 로 라이브 바,
#                   비대화형이면 테이블별 완료 라인만 출력.
###############################################################################

import os
import sys
import time

_BAR_WIDTH = 24


def progress_enabled() -> bool:
    """진행률 바 표시 여부.
    V_DUMP_PROGRESS=1/on/true → 강제 ON, =0/off/false → 강제 OFF,
    미지정(auto) 이면 stderr 가 터미널일 때만 ON.
    """
    v = os.environ.get('V_DUMP_PROGRESS', 'auto').strip().lower()
    if v in ('0', 'off', 'false', 'no'):
        return False
    if v in ('1', 'on', 'true', 'yes'):
        return True
    return sys.stderr.isatty()


class Progress:
    """한 테이블의 행 적재 진행률.
    enabled=True  → stderr 에 \\r 로 라이브 바(완료 시 줄바꿈).
    enabled=False → 완료 시 한 줄 요약만 출력(로그 친화).
    """

    def __init__(self, label: str, total: int, enabled: bool):
        self.label = label
        self.total = max(0, int(total or 0))
        self.enabled = enabled
        self._last = 0.0

    def update(self, written: int) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        # 0.1초마다(또는 마지막 도달 시) 갱신해 과도한 출력 방지
        if now - self._last < 0.1 and not (self.total and written >= self.total):
            return
        self._last = now
        self._render(written)

    def done(self, written: int) -> None:
        if self.enabled:
            self._render(written)
            sys.stderr.write('\n')
        else:
            sys.stderr.write(f"[v_dump]   {self.label}  rows={written:,}\n")
        sys.stderr.flush()

    def abort(self) -> None:
        if self.enabled:
            sys.stderr.write('\n')
            sys.stderr.flush()

    def _render(self, written: int) -> None:
        if self.total > 0:
            pct = min(1.0, written / self.total)
            filled = int(_BAR_WIDTH * pct)
            bar = '#' * filled + ' ' * (_BAR_WIDTH - filled)
            line = f"  {self.label}  [{bar}] {pct * 100:5.1f}%  {written:,}/{self.total:,}"
        else:
            # 총계를 모르면 누적 행수만 표시(불확정)
            line = f"  {self.label}  {written:,} rows"
        sys.stderr.write('\r' + line)
        sys.stderr.flush()
