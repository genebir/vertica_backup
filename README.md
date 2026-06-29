# v_dump — Vertica 데이터 백업 도구

Vertica 의 스키마/테이블을 **Vertica 가 다시 가장 빠르게 읽을 수 있는 형식**으로 파일로 떨어뜨리고,
나중에 `vsql -f load.sql` 한 줄로 복원할 수 있게 해 주는 백업 유틸리티.

`pg_dump` 가 SQL 한 파일로 떨구는 것과 달리, v_dump 는 **데이터 자체를 별도 `.dat` 파일**로 저장한다.
이 파일은 Vertica `COPY` 의 기본 컨벤션(pipe-delimited, `\N` NULL, 백슬래시 escape) 그대로라,
`COPY ... FROM LOCAL` 로 가장 효율적인 적재가 가능하다.

---

## 1. 디렉토리 구조

```
except/
├── v_dump.sh                  # 편의 단축 (내부적으로 v_dump/run.sh 위임)
└── v_dump/                    # ← 배포 단위. 이 폴더만 복사하면 됨
    ├── install.sh             # 멱등 설치 스크립트 (venv 생성 + 의존성)
    ├── run.sh                 # 정식 런처 (자체 venv, 위치 독립)
    ├── requirements.txt       # 핀 고정된 런타임 의존성
    ├── v_dump.yaml.example    # 접속 설정 템플릿 (커밋 대상)
    ├── v_dump.yaml            # 실제 접속 설정 (install 이 생성, git 제외)
    ├── .gitignore             # .venv / v_dump.yaml / __pycache__ 제외
    ├── .venv/                 # install 이 만든 가상환경 (배포 시 따라가지 않음)
    ├── __main__.py            # `python -m v_dump` 진입점
    ├── cli.py                 # argparse CLI
    ├── config.py              # 접속 설정 로더 (CLI > env > yaml)
    ├── connection.py          # vertica_python 커넥션 컨텍스트 매니저
    ├── inspector.py           # v_catalog 메타조회 (스키마/테이블/컬럼)
    ├── ddl.py                 # EXPORT_OBJECTS 기반 DDL 추출
    ├── data.py                # 테이블 → .dat 직렬화 + COPY 문 생성
    ├── escape.py              # Vertica COPY 컨벤션 이스케이프
    ├── dumper.py              # 오케스트레이터 (전체 흐름)
    ├── Dockerfile             # 덤프+복원 통합 이미지 (python + vsql)
    ├── .dockerignore          # 이미지 빌드 컨텍스트 제외 목록
    ├── vertica-client-*.tar.gz# vsql 클라이언트 (이미지에 vsql 설치용)
    ├── docker/
    │   ├── entrypoint.sh      # dump/restore/vsql 디스패처
    │   ├── filter_load.py     # 선택 복원용 load.sql 필터
    │   ├── build-image.sh     # 빌드(+--save tar) — 인터넷 빌드 머신용
    │   └── v_dump-docker.sh   # 폐쇄망 실행 래퍼 (마운트/접속정보 자동)
    ├── GUIDE.md               # 운영 가이드(Docker/폐쇄망 처음~끝 흐름)
    └── README.md              # ← 본 문서(레퍼런스)
```

> 처음 도입/운영 흐름은 **`GUIDE.md`** 를 먼저 보면 빠르다. 본 README 는 항목별 레퍼런스다.

> **두 가지 실행 방식**이 있다.
> - **네이티브** (`install.sh` + `run.sh`): 대상 머신에 파이썬을 직접 깔 수 있을 때. 아래 2~6장.
> - **Docker** (`Dockerfile` + `docker/`): 폐쇄망 등 파이썬 설치가 곤란할 때. **11장** 참고.
>   파이썬·vsql·의존성을 한 이미지에 말아 컨테이너로 덤프/복원한다.

---

## 2. 설치 (다른 환경 배포)

배포 단위는 **`v_dump/` 폴더 하나**다. 아무것도 설치되지 않은 새 환경에 그대로 복사한 뒤
`install.sh` 만 돌리면 자체 venv 가 만들어지고 의존성이 깔린다. **여러 번 실행해도 안전(멱등)** 하다.

```bash
# 1) 폴더 통째로 새 환경에 복사 (예시)
scp -r v_dump user@newhost:/opt/

# 2) 설치
cd /opt/v_dump
./install.sh

# 3) 접속 정보 입력 (install 이 v_dump.yaml.example 을 복사해 만들어 둠)
vi v_dump.yaml

# 4) 실행
./run.sh --schema BDA_DM_DB -o ./BDA_DM_DB_backup
```

`install.sh` 가 하는 일:
- `python3` (>=3.8, `venv`+`ensurepip` 가능한 것) 자동 탐지 — `PYTHON=/path/to/python3 ./install.sh` 로 지정도 가능
- `v_dump/.venv` 생성 (없을 때만, 깨진 venv 는 자동 재생성)
- `requirements.txt` 설치
- `v_dump.yaml` 이 없으면 `v_dump.yaml.example` 을 복사해 생성 (**기존 설정은 절대 덮어쓰지 않음**)
- `run.sh` 실행권한 부여 + import/CLI 검증

> **사전 요구사항**: 대상 머신에 `python3` 와 `python3-venv`(Debian/Ubuntu) 또는 동급이 있어야 한다.
> 없으면 `install.sh` 가 어떤 패키지를 깔아야 하는지 안내하고 멈춘다.
>
> 인터넷이 없는 폐쇄망이면, 같은 OS/파이썬 버전의 머신에서 `pip download -r requirements.txt -d wheels`
> 로 받은 wheel 들을 함께 복사하고 `./install.sh` 대신
> `.venv/bin/pip install --no-index --find-links wheels -r requirements.txt` 로 설치한다.

### 실행 인터페이스 — `run.sh` vs `v_dump.sh`

- `v_dump/run.sh` — **정식 런처**. 자체 venv(`v_dump/.venv`) 사용, 위치 독립적. 배포 환경에서 이걸 쓴다.
- `except/v_dump.sh` — 이 프로젝트 안에서의 편의 단축. 내부적으로 `v_dump/run.sh` 로 위임할 뿐이다.

둘 다 동일하게 동작한다. 별칭은 어느 쪽으로 걸어도 된다.

```bash
echo 'alias v_dump=/opt/v_dump/run.sh' >> ~/.bashrc && source ~/.bashrc
```

### 기본 접속 설정

`v_dump/v_dump.yaml` 에 들어간다 (install 이 example 로부터 생성). 다른 환경에 붙으려면 값만 바꾸면 된다.

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: VMart
  tlsmode: disable
```

> 비밀번호가 평문으로 저장되니, `.gitignore` 에
> ```
> v_dump/v_dump.yaml
> ```
> 항목을 반드시 추가하라.

### 2-3. 설정 우선순위

같은 항목이 여러 곳에 있으면 다음 순서로 결정된다.

1. **CLI 인자** — `--host`, `--user`, `--password`, `--database`, `--port`, `--tlsmode`
2. **환경 변수** — `VERTICA_HOST`, `VERTICA_PORT`, `VERTICA_USER`, `VERTICA_PASSWORD`, `VERTICA_DATABASE`, `VERTICA_TLSMODE`
3. **`--config` 로 지정한 yaml**
4. **기본 yaml** (`v_dump/v_dump.yaml`)

### 2-4. 프로시저 동반 추출

테이블 단위(`-t`) 백업 시, **같은 스키마의 프로시저 중 이름에 그 테이블명이 포함된 것**을
찾아 DDL 을 함께 내린다(`schema.ddl.sql` 에 추가). 명명 규칙(접두/접미)을 가정하지 않는
단순 부분 일치라 별도 설정이 필요 없다.

- 예: 테이블 `TB_BCOLOG701` → 프로시저 `PID_SM_TB_BCOLOG701_1`(이름에 테이블명 포함) 자동 매칭.
- 프로시저 목록은 `v_catalog.user_procedures` 에서 읽고, 인자 있는 프로시저는 시그니처까지
  포함해 `EXPORT_OBJECTS` 로 추출한다.
- 매칭이 없거나 추출 실패한 건 건너뛰고 `MANIFEST.txt` 에 사유를 남긴다(덤프는 계속).
- 전체를 끄려면 CLI 에서 `--no-procedures`.

---

## 3. 실행

### 3-1. 가장 빠른 한 줄

```bash
/home/duarl/KRWay/except/v_dump.sh --schema BDA_DM_DB -o ./BDA_DM_DB_backup
```

`v_dump.sh` 는 venv 의 python + PYTHONPATH 를 자동으로 잡아 준다. **어떤 디렉토리에서 호출해도** 동작.

별칭 등록을 권장:

```bash
echo 'alias v_dump=/home/duarl/KRWay/except/v_dump.sh' >> ~/.bashrc
source ~/.bashrc
v_dump --schema BDA_DM_DB -o ./BDA_DM_DB_backup
```

### 3-2. 자주 쓰는 패턴

```bash
# (모든 예시에서 -o 는 베이스 경로. 그 아래 <schema>/<table|all> 로 생성된다.)

# 스키마 전체 (74개 테이블 한 번에)         → ./backup/BDA_DM_DB/all/
v_dump --schema BDA_DM_DB -o ./backup

# 특정 테이블만                             → ./backup/BDA_DM_DB/TB_BCOLOG701/
v_dump --schema BDA_DM_DB --table TB_BCOLOG701 -o ./backup

# 테이블 여러 개 (반복/콤마/혼용) — 테이블마다 한 폴더
v_dump --schema BDA_DM_DB -t TB_BCOLOG701 -t TB_BCOLOG702 -o ./backup
v_dump --schema BDA_DM_DB -t TB_A,TB_B,TB_C -o ./backup

# 테이블 단위 백업 시, 이름에 그 테이블명이 포함된 프로시저 DDL 도 자동 포함 (기본 ON)
v_dump --schema BDA_DM_DB -t TB_BCOLOG701 -o ./backup            # +프로시저 DDL
v_dump --schema BDA_DM_DB -t TB_BCOLOG701 --no-procedures -o ./backup  # 프로시저 제외

# DDL 만 (.dat 안 만듦)
v_dump --schema BDA_DM_DB --schema-only -o ./backup

# 데이터만 (DDL/load.sql 안 만듦)
v_dump --schema BDA_DM_DB --data-only -o ./backup

# 다른 환경에 접속
v_dump --host 10.0.0.5 --user readonly --password secret \
       --database VMart --schema PUBLIC -o ./backup

# 다른 설정 파일 사용
v_dump --config /etc/v_dump/prod.yaml --schema BDA_DM_DB -o ./backup

# 도움말
v_dump --help
```

---

## 4. 출력물

`-o` 는 **베이스 경로**이고, 그 아래에 `<schema>/<table>` (스키마 전체는 `<schema>/all`) 구조가 만들어진다.
각 말단 폴더는 그 자체로 복원 가능한 **자기완결 덤프 단위**다.

```
<베이스>/                          # -o 로 준 경로 (도커는 /backup = 고정 backup)
├── <schema>/
│   ├── all/                      # 스키마 전체 덤프 (-t 없이)
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql
│   │   ├── load.sql
│   │   └── <schema>.<table>.dat  # 모든 테이블
│   ├── <table_A>/                # -t 로 지정한 테이블 (테이블마다 한 폴더)
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql        # 그 테이블(+매칭 프로시저) DDL
│   │   ├── load.sql
│   │   └── <schema>.<table_A>.dat
│   └── <table_B>/
│       └── ...
```

각 폴더 안의 파일 구성:

| 파일 | 내용 |
|---|---|
| `MANIFEST.txt` | 덤프 메타정보 (호스트, scope, 행 수, 실패/프로시저 목록) |
| `schema.ddl.sql` | `CREATE SCHEMA / TABLE ...` (+테이블 단위면 매칭 프로시저 DDL) |
| `load.sql` | 그 폴더의 `.dat` 을 다시 적재하는 `COPY` 문 집합 |
| `<schema>.<table>.dat` | 테이블별 데이터 파일 |

### 4-1. `.dat` 파일 형식

Vertica `COPY` 의 기본 컨벤션과 정확히 일치한다.

| 항목 | 값 | 비고 |
|---|---|---|
| 구분자 | `|` | Vertica COPY default |
| NULL 표현 | `\N` | `NULL AS '\N'` |
| ESCAPE | `\` | Vertica COPY default |
| 행 종결 | LF (`\n`) | |
| 인코딩 | UTF-8 | |
| 따옴표 처리 | ENCLOSED BY 없음 | 구분자/종결자/escape 만 `\` 로 이스케이프 |

이스케이프 대상은 **escape char(`\`), 구분자(`|`), 행 종결자(LF/CR)** 뿐이다.
Vertica COPY 의 escape 는 "`\` 다음 1바이트를 데이터로 그대로 취급" 하므로,
이스케이프는 **백슬래시 + 원본 바이트**로 쓴다(예: 개행 → `\`+LF). `\n` 처럼 글자 `n` 을
붙이면 적재 시 글자 `n` 으로 깨진다. 탭·수직탭·폼피드 등은 구분자도 종결자도 아니므로
이스케이프 없이 그대로 둔다.

타입별 표현:
- BOOLEAN: `t` / `f`
- INT, NUMERIC, FLOAT: 문자열 그대로
- DATE: `YYYY-MM-DD`
- TIMESTAMP: `YYYY-MM-DD HH:MM:SS[.ffffff]`
- TIME: `HH:MM:SS`
- VARBINARY: `\xNN` 시퀀스
- NULL: `\N`

예시:
```
1|Store1|1|16 Elm St|Concord|CA|West|Plan1|Premium|None|1000|2000|2007-03-01|\N|18|12576|39|2284
```

### 4-2. `schema.ddl.sql`

Vertica 의 `EXPORT_OBJECTS()` 가 만들어주는 DDL 을 **재실행 안전(멱등)** 형태로 변환해 담는다.
`CREATE SCHEMA`, `CREATE SEQUENCE`, `CREATE TABLE`, `CREATE PROJECTION` 등을 포함.

멱등 변환(이미 있는 객체에 다시 실행해도 에러 없이 넘어감):

| 원본 | 변환 |
|---|---|
| `CREATE SCHEMA` / `TABLE` / `SEQUENCE` / `PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE` / `VIEW` | `CREATE OR REPLACE ...` |

> 줄 시작(`^`) 기준으로만 치환하므로 프로시저 본문 내부 텍스트는 건드리지 않는다.
> (제약조건 `ALTER TABLE ... ADD CONSTRAINT` 는 멱등 대상이 아니다 — 재실행 시 중복 오류 가능.)

테이블 단위(`-t`) 백업에서 이름에 그 테이블명이 포함된 프로시저가 있으면, 파일 끝에
`-- ===== stored procedures =====` 섹션으로 매칭 프로시저의 `CREATE PROCEDURE` DDL 이 붙는다.
존재하지 않거나 추출 실패한 프로시저는 건너뛰고 `MANIFEST.txt` 에 사유가 남는다(덤프는 계속).
(스키마 전체 덤프는 `EXPORT_OBJECTS(schema)` 가 프로시저까지 이미 포함하므로 이 섹션이 따로 없다.)

### 4-3. `load.sql`

각 `.dat` 을 다시 적재하기 위한 `COPY FROM LOCAL` 문 집합. 한 트랜잭션으로 감싸져 있어 중간 실패 시 전체 롤백.

```sql
\set ON_ERROR_STOP on
BEGIN;

COPY "BDA_DM_DB"."TB_BCOLOG701" (col1, col2, ...) FROM LOCAL 'BDA_DM_DB.TB_BCOLOG701.dat'
  DELIMITER '|' NULL AS '\N' ENCLOSED BY '' ABORT ON ERROR DIRECT;
...

COMMIT;
```

> `DIRECT` 가 붙어 있어 WOS 를 거치지 않고 ROS 로 바로 적재 → 대용량에 가장 빠름.

### 4-4. `MANIFEST.txt`

```
v_dump manifest
  host        : 10.0.0.5:5433
  database    : VMart
  scope       : DS.{TB_A,TB_B}          # 다중 테이블이면 {..} 로 표기
  dumped at   : 2026-05-20 10:34:40
  schema_only : False
  data_only   : False
  tables      : 2
  rows total  : 3953181
  procedures  : 2 included              # 포함된 프로시저 수
  ddl file    : schema.ddl.sql
  load script : load.sql

files:
  DS.TB_A.dat	1200 rows
  DS.TB_B.dat	980 rows

failed:                                  # (실패가 있을 때만)
  DS.SOME_TABLE	<오류 첫 줄>

procedures:                              # (프로시저 추출을 시도했을 때만)
  PID_SM_TB_BCOLOG701_1	included
  PID_SM_TB_BCOLOG701_2	skipped (export 결과 비어있음)
```

---

## 5. 복원

```bash
# 복원할 말단 폴더로 진입 (스키마 전체는 <schema>/all, 단일 테이블은 <schema>/<table>)
cd ./backup/BDA_DM_DB/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql   # 테이블 생성
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql         # 데이터 적재
```

`load.sql` 의 `COPY ... FROM LOCAL` 은 **vsql 클라이언트의 현재 디렉토리** 를 기준으로
`.dat` 파일을 찾는다. 그래서 반드시 백업 디렉토리에서 실행해야 한다.

> 빈 DB 가 아니라 기존 데이터에 누적하고 싶다면, `schema.ddl.sql` 실행은 건너뛰고
> `load.sql` 만 실행하면 된다. 다만 컬럼 변경이 있었다면 COPY 문이 실패할 수 있다.

- **프로시저**도 함께 복원하려면 `schema.ddl.sql` 을 실행하면 된다(테이블 DDL 과 같은 파일에 들어있음).
  DDL 이 멱등(`IF NOT EXISTS` / `OR REPLACE`)이라 **이미 있는 객체가 있어도 그냥 넘어간다**(재실행 안전).
- **Docker 로 복원**할 때는 위 두 단계를 한 명령으로 처리하는 옵션이 있다 — `--with-ddl`(구조 먼저 생성),
  `-t`(일부 테이블만 적재). **11장**의 복원 절을 참고.

---

## 6. CLI 옵션 전체

```
usage: v_dump [-h] --schema SCHEMA [--table TABLE]
              [--schema-only | --data-only]
              [--with-procedures | --no-procedures]
              --output OUTPUT
              [--host HOST] [--port PORT]
              [--user USER] [--password PASSWORD]
              [--database DATABASE]
              [--tlsmode {disable,prefer,require}]
              [--config CONFIG]

target:
  --schema, -n SCHEMA    덤프할 스키마명 (필수)
  --table, -t TABLE      특정 테이블만. 반복(-t A -t B) 또는 콤마(-t A,B) 로
                         여러 개 지정 가능. 생략 시 스키마 전체.

mode:
  --schema-only          DDL 만 (데이터 파일 X)
  --data-only            데이터 파일만 (DDL/load.sql X)

procedures:
  --with-procedures      테이블 단위(-t) 백업 시 매칭 프로시저 DDL 도 추출 (기본 ON)
  --no-procedures        프로시저 DDL 추출 끔

output:
  --output, -o OUTPUT    출력 디렉토리. 없으면 생성. (필수)

connection:
  --host HOST
  --port PORT
  --user, -U USER
  --password, -W PASSWORD
  --database, -d DATABASE
  --tlsmode {disable,prefer,require}
  --config CONFIG        yaml 설정 파일 (기본: v_dump/v_dump.yaml)
```

---

## 7. 동작 특성 / 주의사항

### 7-1. 외부 테이블은 자동 스킵

`CREATE EXTERNAL TABLE ... AS COPY FROM 's3://...'` 같이 실제 데이터가 Vertica 밖에 있는
테이블은 `SELECT` 자체가 외부 파일 접근을 시도해서 실패한다. v_dump 는
`v_catalog.tables.table_definition` 이 채워진 항목을 외부 테이블로 보고
**`list_tables_in_schema()` 단계에서 자동 제외**한다.

→ 외부 테이블의 DDL 은 `schema.ddl.sql` 에는 그대로 포함되지만 `.dat` 은 생성되지 않는다.
복원 후에도 외부 데이터 소스는 살아있어야 데이터가 보인다.

### 7-2. 단일 테이블 실패 격리

한 테이블 처리가 예외로 끝나도 나머지 테이블은 계속 진행된다. 실패 사유는

- stderr: `[v_dump] WARN skip <schema>.<table>: <reason>`
- `MANIFEST.txt` 의 `failed:` 섹션

양쪽에 기록된다. 부분 생성된 `.dat` 은 자동 삭제.

### 7-3. UTF-8 깨진 바이트 관용 처리

`vertica_python` 의 unicode 디코딩이 기본 `strict` 라, 일부 컬럼에 깨진 멀티바이트가
섞여 있으면 fetch 가 중단된다. v_dump 는 커넥션 옵션 `unicode_error='replace'` 로 열기 때문에
손상 위치가 `U+FFFD` 로 치환되고 row 단위 손실 없이 덤프된다.

> 백업 우선이라 lenient 가 기본. 데이터 무결성 검증이 더 중요하면 `config.py` 의
> `unicode_error` 를 `'strict'` 로 바꾸면 된다 (실패 테이블은 manifest 에 표시).

### 7-4. 메모리

서버측 커서로 10,000행씩 fetch (`data._FETCH_SIZE`). 수억 행짜리 테이블도 메모리 OOM 없이
스트리밍 처리 가능. 출력은 한 줄 단위로 즉시 디스크에 flush.

### 7-5. 시퀀스/뷰/프로시저

DDL 추출은 `EXPORT_OBJECTS('', 'scope')` 로 한다. **스키마 전체** 덤프면 스코프가 스키마라
**Vertica 가 export 해 주는 모든 객체**(스키마, 시퀀스, 테이블, 프로젝션, 프로시저 등)가 그대로
`schema.ddl.sql` 에 들어간다.

**테이블 단위(`-t`)** 덤프는 스코프가 지정 테이블들로 좁혀지므로 프로시저가 자동으로 빠진다.
이때를 위해 v_dump 는 `v_catalog.user_procedures` 에서 그 스키마의 프로시저 목록을 읽어
**이름에 대상 테이블명이 포함된 것**(`2-4` 참고)만 골라, 인자 시그니처까지 붙여
`EXPORT_OBJECTS` 로 추출하고 `schema.ddl.sql` 끝에 덧붙인다.
매칭/추출 실패 건은 건너뛰고 manifest 에 사유를 남긴다(세션은 롤백되어 덤프 전체가 깨지지 않음).

> 부분 일치라 같은 이름 조각을 공유하는 프로시저는 함께 잡힐 수 있다(의도된 동작).
> 추출 결과가 비면 manifest 에 `skipped` 로 찍히므로 바로 알 수 있다.

데이터 덤프(`.dat`)는 여전히 **테이블만** 대상으로 한다.

### 7-6. 권한

덤프 실행자는 다음 권한이 필요하다.

- 대상 스키마의 `USAGE`
- 대상 테이블의 `SELECT`
- `EXPORT_OBJECTS` 호출 권한 (보통 모든 사용자 가능)
- `v_catalog.*` 조회 권한 (기본 부여)

복원 실행자는 추가로 `CREATE` 권한과 `COPY ... FROM LOCAL` 사용 권한.

---

## 8. 트러블슈팅

### `No module named v_dump`

`python -m v_dump` 는 `v_dump/` 의 **부모 디렉토리**(즉 `except/`) 가 cwd / PYTHONPATH 에 있어야 한다.
간단히는 `v_dump.sh` 런처를 쓰면 해결.

### `dump failed: Cannot expand glob pattern due to error: Access Denied`

외부 테이블이 가리키는 S3 등 외부 파일에 접근 권한이 없을 때 발생.
**현재 버전은 외부 테이블을 자동 제외하므로** 발생하지 않아야 한다. 그래도 뜬다면
inspector 필터 조건이 환경에 안 맞는 것이니 manifest 의 `failed:` 를 확인.

### `unicode_error` 관련 디코드 오류

기본 `replace` 가 처리해 줘야 하는데, 그래도 다른 인코딩이라면 컬럼이 LATIN1 등으로 들어가 있을
수 있다. Vertica 서버 측 `SET SESSION CHARACTERSET` 또는 컬럼 인코딩을 확인.

### vsql 복원 시 `ERROR: COPY: Input record N has different number of columns`

`.dat` 직렬화와 실제 컬럼 수가 안 맞는 경우. 거의 항상 데이터 자체에 깨진 escape
(예: `\` 가 단독으로 들어간 값) 가 있다는 뜻. v_dump 의 escape 는 입력의 모든 `\` 를
`\\` 로 변환하므로 정상적으로는 발생하지 않는다. 변환 로직을 손댄 적이 있다면
`escape.py` 의 `_ESCAPES` 매핑을 점검.

### load.sql 이 너무 길어 메모리 부담

`load.sql` 은 73개 `COPY` 문 정도면 수십 KB 수준. 테이블 수가 수천 개면 분할이 필요한데,
그 경우는 스키마별로 따로 덤프하는 게 운영상 더 안전.

---

## 9. 내부 모듈 한 줄 요약

| 모듈 | 책임 |
|---|---|
| `config.py` | `ConnectionConfig` dataclass + CLI/env/yaml 우선순위 결정 |
| `connection.py` | `vertica_python.connect` 컨텍스트 매니저 |
| `inspector.py` | `v_catalog` 조회: 스키마/테이블/컬럼 목록, 외부 테이블 판별 |
| `ddl.py` | `EXPORT_OBJECTS()` 호출 래퍼 |
| `escape.py` | 단일 값/행 → Vertica COPY 본문 문자열 변환 |
| `data.py` | 테이블 행 스트리밍 + COPY 문 빌더 |
| `dumper.py` | 전체 흐름 오케스트레이션 + manifest/실패 처리 |
| `cli.py` | argparse + 종료 코드 |
| `__main__.py` | `python -m v_dump` 진입 |

---

## 10. 빠른 참조 — 한 페이지 요약

```bash
# 백업 (→ ./backup/BDA_DM_DB/all/ 에 생성)
/home/duarl/KRWay/except/v_dump.sh --schema BDA_DM_DB -o ./backup

# 복원 (말단 폴더로 cd)
cd ./backup/BDA_DM_DB/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql
```

---

## 11. Docker (폐쇄망 / 파이썬 설치 곤란 환경)

대상 머신에 파이썬을 직접 깔기 어려울 때 쓰는 방식. **파이썬 + vsql + 의존성**을 한 이미지에 말아,
컨테이너로 덤프와 복원을 모두 수행한다. 접속정보·백업파일·덤프 대상 같은 "변하는 값"은 전부
런타임에 볼륨/환경변수로 주입하므로 **이미지는 한 번만 빌드**하면 재사용한다.

```
이미지(불변: python·vsql·코드)  +  런타임 마운트(접속정보·백업파일)  =  컨테이너
```

### 11-1. 빌드 (인터넷 되는 머신에서)

```bash
cd v_dump
./docker/build-image.sh            # 이미지 빌드만
./docker/build-image.sh --save     # 빌드 + v_dump-image.tar 저장 (폐쇄망 반입용)
```

- 빌드 머신이 인터넷에 연결돼 있어 pip 가 base 파이썬에 맞는 휠을 알아서 받는다
  (번들 휠의 버전 핀 문제를 회피).
- `vsql` 은 x86_64 바이너리라 이미지도 **amd64 로 고정**돼 있다(`Dockerfile` 의 `--platform=linux/amd64`).
  arm64 맥에서 빌드해도 amd64 이미지로 나온다.
- 빌드 마지막에 `RUN vsql --version` 으로 라이브러리 링킹을 자가검증한다. 혹시 다른 `.so` 가
  없다고 실패하면 그 패키지만 `Dockerfile` 의 apt 줄에 추가하면 된다.

### 11-2. 폐쇄망 반입 — docker 만 있다는 가정

폐쇄망은 레지스트리 pull 이 안 되므로 **이미지를 tar 로 떠서 옮긴다.** 가져갈 묶음은 단 3가지:

| 가져갈 것 | 무엇 | 비고 |
|---|---|---|
| `v_dump-image.tar` | 이미지 본체 | `--save` 산출물. python·vsql·코드·`filter_load.py` 다 들어있음 |
| `docker/v_dump-docker.sh` | 실행 래퍼 | 마운트/접속정보 조립 자동화 (없어도 raw `docker run` 가능, 11-6) |
| `v_dump.yaml` | 접속정보 | 또는 env 로 줄 거면 생략 가능 |

```bash
# [인터넷 빌드 머신] 한 덩어리로 묶기
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml          # 접속정보 채워둘 거면
tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

# ── v_dump-bundle.tar.gz 를 폐쇄망으로 반입 (USB/내부망 등) ──

# [폐쇄망, docker 만 설치됨]
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar             # 이미지 등록
docker images | grep v_dump                 # v_dump:latest 확인
vi v_dump.yaml                              # 접속정보 입력(또는 env)
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 는 `podman` 이 있으면 podman, 없으면 `docker` 를 자동 선택한다.
> docker 만 있는 환경에서도 그대로 동작한다.

### 11-3. 덤프 (컨테이너)

백업 폴더는 **`v_dump-docker.sh` 가 있는 경로의 `backup/`** 에 생겨 컨테이너 `/backup` 로
마운트된다(어디서 실행하든 항상 스크립트 옆에 생긴다). **`-o` 는 줄 필요가 없다** —
래퍼가 출력 베이스를 항상 `/backup` 으로 잡아주고, 그 아래 `<schema>/<table>`(전체는 `<schema>/all`) 구조로 떨어진다.

```bash
# 접속정보는 v_dump.yaml 자동 탐색 또는 env. -o 생략.
./v_dump-docker.sh dump --schema BDA_DM_DB        # → backup/BDA_DM_DB/all/

# 테이블 여러 개 + 프로시저(DS/DM 자동) — 테이블마다 한 폴더
./v_dump-docker.sh dump --schema DS -t TB_A,TB_B  # → backup/DS/TB_A/ , backup/DS/TB_B/
```

→ 결과가 스크립트 옆 `backup/<schema>/...` 에 떨어진다.
실행 시 그 경로가 `[run] backup dir: ...` 로 출력된다. 다른 곳에 두려면 `BACKUP_DIR=/경로` 로만 덮으면 된다.

### 11-4. 복원 (컨테이너)

복원 경로는 **backup 기준 상대경로**(`<schema>/<table>` 또는 `<schema>/all`)로 적으면 된다.
`COPY ... FROM LOCAL` 이 클라이언트 상대경로라 래퍼가 해당 폴더로 자동 진입한다.

```bash
# 스키마 전체 복원 (말단 폴더를 가리킨다)
./v_dump-docker.sh restore BDA_DM_DB/all

# 단일 테이블 폴더 복원
./v_dump-docker.sh restore DS/TB_A

# 구조부터 생성 후 적재 — 빈 대상에 테이블+프로시저 DDL 먼저 실행
./v_dump-docker.sh restore DS/TB_A --with-ddl

# all 폴더에서 일부 테이블만 골라 적재
./v_dump-docker.sh restore BDA_DM_DB/all -t TB_A,TB_B

# 임의 점검: vsql 원시 실행
./v_dump-docker.sh vsql -c "SELECT version()"
```

- `--with-ddl` 은 `schema.ddl.sql`(테이블+프로시저)을 데이터 적재 **전에** 실행한다.
  DDL 이 멱등(`IF NOT EXISTS` / `OR REPLACE`)이라 **이미 있는 객체엔 그냥 넘어간다**.
  구조 생성이 실제로 실패했다면 이어지는 COPY 가 명확히 실패해 알려준다.
- `-t` 와 `--with-ddl` 을 함께 주면, **구조는 전체** `schema.ddl.sql` 로 만들고 **데이터는 지정 테이블만** 넣는다.

### 11-5. 접속정보 주입 (우선순위: env > yaml)

| 방법 | 사용 | 비고 |
|---|---|---|
| yaml (권장) | `v_dump.yaml` 한 번 채워두면 자동 탐색 | 매번 안 넘겨도 됨. 평문 비번이라 이미지엔 안 구움 |
| 환경변수 | 그 실행 앞에 `VERTICA_*=...` | 일회성으로 다른 DB 붙을 때. yaml 보다 우선 |

```bash
# 평소: yaml 그대로 → 아무것도 안 넘김
./v_dump-docker.sh dump --schema DS -t TB_A

# 복원만 다른 서버로: 그 실행에만 env 로 덮기
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh restore DS/TB_A --with-ddl
```

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`
중 설정된 것만 컨테이너로 전달된다. yaml 경로를 직접 지정하려면 `V_DUMP_YAML=/path/v_dump.yaml`.

### 11-6. 래퍼 없이 raw `docker run`

`v_dump-docker.sh` 가 펼치는 명령은 결국 이것뿐이다(직접 써도 된다):

```bash
docker run --rm \
  -v "$PWD/backup:/backup" \
  -v "$PWD/v_dump.yaml:/app/v_dump/v_dump.yaml:ro" \
  v_dump:latest \
  dump --schema DS -t TB_A,TB_B -o /backup       # → /backup/DS/TB_A , /backup/DS/TB_B

# 접속정보를 env 로 (복원은 말단 폴더를 가리킨다):
docker run --rm -v "$PWD/backup:/backup" \
  -e VERTICA_HOST=10.0.0.5 -e VERTICA_USER=dbadmin \
  -e VERTICA_PASSWORD=*** -e VERTICA_DATABASE=VMart \
  v_dump:latest restore /backup/DS/TB_A --with-ddl
```

컨테이너 엔트리포인트 명령: `dump` / `restore` / `vsql` / `shell` / `help`.
`docker run --rm v_dump:latest help` 로 도움말 출력.

### 11-7. 이미지를 다시 말아야 하는 때

접속정보·백업파일·덤프 대상은 전부 런타임 주입이라 **이미지와 무관**하다. 재빌드가 필요한 경우는
**이미지 내용물이 바뀔 때**뿐:

- `v_dump` 코드(`*.py`) 수정
- `requirements.txt` 의존성 변경
- vsql(vertica-client) 버전 교체

이때만 11-1 을 다시 돌려 새 `v_dump-image.tar` 를 반입한다.
