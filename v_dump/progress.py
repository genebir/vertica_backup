# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Kiseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승 (Kiseung Yeom)
# License   : Apache-2.0 (see LICENSE)
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# File            : v_dump/progress.py
# Desc            : 데이터 덤프 진행률 표시. 터미널이면 stderr 에 \r 로 라이브 바,
#                   비대화형이면 테이블별 완료 라인만 출력.
#                   게이지는 유니코드 8분할 블록(█▉▊▋▌▍▎▏)으로 서브셀 해상도.
#                   UTF-8 이 아니면 ASCII(#) 로 자동 폴백.
###############################################################################

import os
import sys
import time

_BAR_WIDTH = 28

# ── 게이지 문자셋: 터미널이 UTF-8 이면 실제 로딩바처럼 부드럽게, 아니면 ASCII 폴백.
_UTF = 'utf' in (getattr(sys.stderr, 'encoding', None) or '').lower()
if _UTF:
    _FULL = '█'                 # 8/8 채움
    _PARTIALS = ' ▏▎▍▌▋▊▉'      # 0/8 ~ 7/8 (부분 셀)
    _EMPTY = '░'                # 남은 트랙
    _L, _R = '│', '│'
else:
    _FULL = '#'
    _PARTIALS = ' '
    _EMPTY = '-'
    _L, _R = '[', ']'

# ANSI '줄 끝까지 지우기'. 라인이 짧아질 때 이전 잔상 제거용. 실제 터미널일 때만.
_ANSI = sys.stderr.isatty()
_CLR = '\r' + ('\033[K' if _ANSI else '')


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


def render_bar(pct: float, width: int = _BAR_WIDTH) -> str:
    """0.0~1.0 비율을 부드러운 게이지 문자열로. 예: │████████▍       │"""
    pct = 0.0 if pct < 0 else (1.0 if pct > 1 else pct)
    filled = pct * width
    full = int(filled)
    bar = _FULL * full
    if full < width:
        idx = int((filled - full) * len(_PARTIALS))
        # 부분 셀이 0/8 이면 빈 셀(트랙)로 — 블록과 트랙 사이 빈틈 방지.
        bar += _EMPTY if idx == 0 else _PARTIALS[idx]
        bar += _EMPTY * (width - full - 1)
    return _L + bar + _R


def fmt_dur(sec: float) -> str:
    """경과/ETA 를 m:ss 또는 h:mm:ss 로."""
    sec = int(sec if sec and sec > 0 else 0)
    if sec < 3600:
        return f"{sec // 60}:{sec % 60:02d}"
    return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def fmt_rate(rows_per_sec: float) -> str:
    """처리율을 사람이 읽기 좋게(1.2k/s, 3.4M/s)."""
    r = rows_per_sec if rows_per_sec and rows_per_sec > 0 else 0
    if r >= 1_000_000:
        return f"{r / 1e6:.1f}M/s"
    if r >= 1_000:
        return f"{r / 1e3:.1f}k/s"
    return f"{r:.0f}/s"


class Progress:
    """한 테이블의 행 적재 진행률.
    enabled=True  → stderr 에 \\r 로 라이브 바(게이지 + %/행수 + 처리율/ETA).
    enabled=False → 완료 시 한 줄 요약만 출력(로그 친화).
    """

    def __init__(self, label: str, total: int, enabled: bool):
        self.label = label
        self.total = max(0, int(total or 0))
        self.enabled = enabled
        self._last = 0.0
        self._start = time.monotonic()

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
            self._render(written, final=True)
            sys.stderr.write('\n')
        else:
            el = time.monotonic() - self._start
            sys.stderr.write(
                f"[v_dump]   {self.label}  rows={written:,}  {fmt_dur(el)}\n")
        sys.stderr.flush()

    def abort(self) -> None:
        if self.enabled:
            sys.stderr.write('\n')
            sys.stderr.flush()

    def _render(self, written: int, final: bool = False) -> None:
        el = time.monotonic() - self._start
        rate = written / el if el > 0 else 0.0
        if self.total > 0:
            pct = min(1.0, written / self.total)
            bar = render_bar(pct)
            if final:
                tail = f"  {fmt_dur(el)}"
            elif rate > 0 and written < self.total:
                eta = (self.total - written) / rate
                tail = f"  {fmt_rate(rate)}  ETA {fmt_dur(eta)}"
            else:
                tail = ''
            line = (f"  {self.label}  {bar} {pct * 100:5.1f}%  "
                    f"{written:,}/{self.total:,}{tail}")
        else:
            # 총계를 모르면 누적 행수 + 처리율 + 경과만 (퍼센트 불확정)
            line = (f"  {self.label}  {written:,} rows  "
                    f"{fmt_rate(rate)}  {fmt_dur(el)}")
        sys.stderr.write(_CLR + line)
        sys.stderr.flush()


class MultiProgress:
    """병렬 덤프용 집계 진행바. unit 완료마다 갱신.
    추정 총 행수가 있으면 행 기준 %, 없으면 unit 완료 개수 기준 %.
    """

    def __init__(self, total_units: int, total_rows_est: int, enabled: bool):
        self.total_units = max(1, int(total_units or 1))
        self.total_rows_est = max(0, int(total_rows_est or 0))
        self.enabled = enabled
        self._start = time.monotonic()

    def update(self, done_units: int, rows: int, current: str = '') -> None:
        if not self.enabled:
            return
        el = time.monotonic() - self._start
        rate = rows / el if el > 0 else 0.0
        if self.total_rows_est > 0:
            pct = min(1.0, rows / self.total_rows_est)
        else:
            pct = min(1.0, done_units / self.total_units)
        bar = render_bar(pct)
        if rate > 0 and self.total_rows_est > rows:
            eta = f"  ETA {fmt_dur((self.total_rows_est - rows) / rate)}"
        else:
            eta = ''
        cur = f"  ← {current}" if current else ''
        line = (f"  병렬  {bar} {pct * 100:5.1f}%  "
                f"{done_units}/{self.total_units} units  {rows:,} rows  "
                f"{fmt_rate(rate)}{eta}{cur}")
        sys.stderr.write(_CLR + line)
        sys.stderr.flush()

    def done(self, rows: int) -> None:
        if self.enabled:
            el = time.monotonic() - self._start
            rate = rows / el if el > 0 else 0.0
            sys.stderr.write(
                _CLR + f"  병렬  완료  {self.total_units} units  {rows:,} rows  "
                       f"{fmt_rate(rate)}  {fmt_dur(el)}\n")
            sys.stderr.flush()
