###############################################################################
# File            : v_dump/escape.py
# Writer          : 염기승
# Date            : 2026-05-20
# Desc            : Vertica COPY 기본 컨벤션에 맞춘 데이터 직렬화.
#                   - 구분자: | (PIPE, Vertica COPY default)
#                   - NULL : \N
#                   - 행 종결: LF
#                   - escape char: \ (Vertica COPY default)
#
# Vertica COPY 의 escape 는 "escape char 다음 1바이트를 데이터로 그대로 취급" 한다.
# 즉 C 스타일 변환(\t→탭)이 아니라, \X 는 항상 '리터럴 X' 로 적재된다.
# → 따라서 escape 가 필요한 건 (1) escape char 자신, (2) 구분자, (3) 행 종결자뿐이고,
#   그 경우 "백슬래시 + 원본 바이트" 로 쓴다. (\n 을 '\'+'n' 으로 쓰면 글자 n 으로 깨진다.)
#   탭/수직탭/폼피드 등은 구분자도 종결자도 아니므로 그대로 둔다(escape 불필요).
###############################################################################

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

NULL_MARKER = r'\N'
FIELD_DELIM = '|'
ROW_DELIM = '\n'

# 값(value) → 직렬화. 백슬래시 다음에 '원본 바이트' 를 그대로 둔다.
_ESCAPES = {
    '\\': '\\\\',      # escape char 자신
    '|': '\\|',        # 구분자
    '\n': '\\\n',      # 행 종결자(LF) → 백슬래시 + 실제 LF
    '\r': '\\\r',      # CR → 백슬래시 + 실제 CR (CRLF 환경 안전)
}


def escape_copy_value(value: Any) -> str:
    """단일 컬럼 값을 Vertica COPY 본문 문자열로 변환."""
    if value is None:
        return NULL_MARKER

    if isinstance(value, bool):
        return 't' if value else 'f'

    if isinstance(value, (int, float, Decimal)):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat(sep=' ')

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    if isinstance(value, (bytes, bytearray, memoryview)):
        # Vertica VARBINARY: \xNN 시퀀스
        return ''.join(f'\\x{b:02x}' for b in bytes(value))

    text = str(value)
    return ''.join(_ESCAPES.get(ch, ch) for ch in text)


def format_copy_row(row: tuple) -> str:
    return FIELD_DELIM.join(escape_copy_value(v) for v in row) + ROW_DELIM
