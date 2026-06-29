#!/usr/bin/env python3
# ───────────────────────────────────────────────────────────────────────────
# Author    : 염기승 (Gibseung Yeom)  <duarltmd1@naver.com>
# Copyright : (c) 2026 염기승. All rights reserved.  무단 수정·재배포 금지.
# ───────────────────────────────────────────────────────────────────────────
###############################################################################
# load.sql 에서 지정한 테이블의 COPY 문만 골라 load.filtered.sql 로 출력.
# (선택 복원용. 현재 작업 디렉토리에 load.sql 이 있다고 가정.)
#
#   filter_load.py <table> [<table> ...]
#
# 테이블은 bare 이름('T') 또는 정규명('SCH.T') 둘 다 허용.
# load.sql 의 COPY 문은 `FROM LOCAL 'SCH.T.dat'` 형태라 그 파일명으로 매칭한다.
# 요청한 테이블 중 load.sql 에 없는 게 있으면 비정상 종료(부분 복원 사고 방지).
###############################################################################
import re
import sys

req = [t.strip().strip('"') for t in sys.argv[1:] if t.strip()]
if not req:
    sys.exit("[filter_load] 복원할 테이블 인자가 없습니다.")

want = set(req)
copy_re = re.compile(r"FROM LOCAL '([^']+)\.dat'")

with open('load.sql', encoding='utf-8') as f:
    lines = f.readlines()

out = []
matched = set()
for ln in lines:
    m = copy_re.search(ln)
    if not m:
        out.append(ln)                 # \set / BEGIN / COMMIT / 주석 등은 그대로 유지
        continue
    base = m.group(1)                  # 'SCH.T'
    sch, _, tbl = base.partition('.')
    if base in want or tbl in want:
        out.append(ln)
        matched |= {base, tbl} & want

missing = want - matched
if missing:
    sys.exit(f"[filter_load] load.sql 에서 못 찾은 테이블: {', '.join(sorted(missing))}")

with open('load.filtered.sql', 'w', encoding='utf-8') as f:
    f.writelines(out)
