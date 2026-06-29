# v_dump 사용 가이드 (Docker / 폐쇄망)

**🇰🇷 한국어** · [🇬🇧 English](GUIDE.en.md) · [🇯🇵 日本語](GUIDE.ja.md) · [🇨🇳 中文](GUIDE.zh.md)

Vertica 의 스키마·테이블·프로시저를 파일로 백업하고, `vsql` 로 다시 적재(복원)하는 도구
**v_dump** 를 **Docker 컨테이너**로 운영하는 실무 가이드.

> 이 문서는 폐쇄망(인터넷 불가) 서버에 파이썬을 직접 설치하기 어려운 환경을 가정한다.
> 파이썬·vsql·의존성을 한 이미지에 말아 컨테이너로 백업/복원을 수행한다.
> 레퍼런스성 상세는 `README.md`, 운영 흐름은 이 문서를 본다.

---

## 목차

1. [개요 — 무엇을, 어떻게](#1-개요)
2. [사전 준비물](#2-사전-준비물)
3. [이미지 빌드 & 폐쇄망 반입](#3-이미지-빌드--폐쇄망-반입)
4. [최초 설정 — 접속 정보](#4-최초-설정--접속-정보)
5. [백업(덤프)](#5-백업덤프)
6. [복원](#6-복원)
7. [운영 시나리오(레시피)](#7-운영-시나리오레시피)
8. [명령어 레퍼런스](#8-명령어-레퍼런스)
9. [트러블슈팅](#9-트러블슈팅)
10. [주의사항·한계](#10-주의사항한계)

---

## 1. 개요

### 1-1. v_dump 가 하는 일

| 단계 | 내용 | 산출/도구 |
|---|---|---|
| **백업(dump)** | 테이블 데이터를 `COPY` 호환 `.dat` 파일로, 구조를 `schema.ddl.sql` 로 추출 | `vertica-python`(순수 파이썬 드라이버) |
| **복원(restore)** | `.dat` 을 `COPY ... FROM LOCAL` 로 다시 적재 | `vsql` |

`pg_dump` 처럼 SQL 한 덩어리가 아니라 **데이터(.dat)** 와 **구조(DDL)** 를 분리해 떨어뜨린다.
`.dat` 은 Vertica `COPY` 기본 컨벤션(파이프 구분, `\N` NULL, 백슬래시 escape) 그대로라 가장 빠르게 재적재된다.

### 1-2. 동작 원리 한눈에

```
┌─────────────────────────┐        백업          ┌──────────────────────┐
│  v_dump 컨테이너         │  vertica-python →    │  Vertica (운영 DB)    │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup (마운트)        │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │   복원   vsql COPY    │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ 호스트의 ./backup 폴더가 컨테이너 /backup 으로 마운트
```

핵심 원칙: **변하지 않는 것(코드·python·vsql)은 이미지에, 변하는 것(접속정보·백업파일)은 런타임에 마운트/주입.**
→ 이미지는 한 번만 빌드하면 재사용한다.

### 1-3. 실행 방식 두 가지

- **Docker (이 문서)** — `v_dump-docker.sh` 래퍼로 컨테이너 실행. 폐쇄망/파이썬 설치 곤란 환경.
- **네이티브** — 호스트에 파이썬을 깔고 `run.sh` 실행. `README.md` 2~6장 참고.

이 가이드는 **Docker 방식**만 다룬다.

---

## 2. 사전 준비물

### 빌드 머신 (인터넷 O)
- Docker (또는 podman)
- 인터넷 연결 (base 이미지 pull + pip 의존성 다운로드)
- 아키텍처: **x86_64(amd64)** — vsql 바이너리가 amd64 라 이미지도 amd64 로 고정된다.

### 폐쇄망 호스트 (인터넷 X)
- Docker (또는 podman) — **그 외 파이썬/vsql 등은 아무것도 필요 없음**
- 대상 Vertica 로의 네트워크 도달성 (예: `5433` 포트)

### 자료
- `v_dump/` 디렉토리 (소스 + `Dockerfile` + `docker/`)
- `vertica-client-*.tar.gz` (vsql 클라이언트, `v_dump/` 안에 동봉)

---

## 3. 이미지 빌드 & 폐쇄망 반입

폐쇄망은 레지스트리 pull 이 안 되므로 **이미지를 tar 로 떠서 옮긴다.**

### 3-1. 빌드 (인터넷 빌드 머신)

```bash
cd v_dump
./docker/build-image.sh            # 빌드만
./docker/build-image.sh --save     # 빌드 + v_dump-image.tar 저장(반입용)
```

- 빌드 머신이 인터넷에 연결돼 있어 pip 가 알맞은 휠을 받는다.
- 마지막에 `RUN vsql --version` 으로 vsql 링킹을 자가검증한다. 여기서 실패하면 빠진 시스템
  라이브러리를 `Dockerfile` 의 apt 줄에 추가한다.
- **소스는 빌드할 때마다 항상 새로 반영된다**(cache-bust 적용). 코드 고치고 다시 빌드하면 끝.

### 3-2. 반입 묶음 만들기

폐쇄망으로 가져갈 것은 단 3가지:

| 파일 | 역할 | 필수 |
|---|---|---|
| `v_dump-image.tar` | 이미지 본체 (python·vsql·코드 전부 포함) | ✅ |
| `docker/v_dump-docker.sh` | 실행 래퍼 (마운트/접속정보/`-o` 자동) | ✅ |
| `v_dump.yaml` | 접속 정보 (env 로 줄 거면 생략 가능) | △ |

```bash
# [빌드 머신]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 접속 정보 채울 거면

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 반입 & 등록 (폐쇄망 호스트)

`v_dump-bundle.tar.gz` 를 USB/내부망 등으로 옮긴 뒤:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # 이미지 등록
docker images | grep v_dump             # v_dump:latest 확인
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 는 `podman` 이 있으면 podman, 없으면 `docker` 를 자동 선택한다.

---

## 4. 최초 설정 — 접속 정보

접속 정보를 주는 방법은 두 가지이고, **둘 중 하나만** 하면 된다.

### 4-1. (권장) yaml 파일

`v_dump.yaml` 을 채워두면 래퍼가 자동으로 찾아 컨테이너에 마운트한다. 한 번 채우면 매번 안 줘도 된다.

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ 비밀번호가 평문이므로 이미지에 굽지 않는다(`.dockerignore` 로 제외). 런타임에만 마운트된다.
> 자동 탐색 순서: `실행위치/v_dump.yaml` → `실행위치/backup/v_dump.yaml` → 스크립트 부모.

### 4-2. 환경변수 (일회성·다른 DB)

그 실행 앞에만 붙이면 yaml 보다 우선한다.

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

사용 가능한 변수: `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 연결 확인

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# Vertica Analytic Database v24.1.0-0  처럼 나오면 OK
```

---

## 5. 백업(덤프)

### 5-1. 출력 구조부터 이해하기

백업 폴더는 **`v_dump-docker.sh` 가 있는 경로의 `backup/`** 에 생긴다(어디서 실행하든 스크립트 옆, `-o` 불필요).
그 아래에 **`<스키마>/<테이블>`**, 스키마 전체는 **`<스키마>/all`** 구조로 떨어진다.

```
backup/                              # v_dump-docker.sh 옆 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # 스키마 전체 덤프(-t 없이)
    │   ├── MANIFEST.txt             # 메타정보(행 수, 실패/프로시저 목록)
    │   ├── schema.ddl.sql           # 구조 DDL (멱등 형태)
    │   ├── load.sql                 # 재적재 COPY 문
    │   └── MY_SCHEMA.<table>.dat    # 테이블별 데이터
    ├── TB_SAMPLE/                # -t 로 지정한 테이블(테이블마다 한 폴더)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # 이 테이블 + 매칭 프로시저 DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

각 말단 폴더(`all`, `TB_xxx`)는 **그 자체로 복원 가능한 자기완결 단위**다.

### 5-2. 스키마 전체 백업

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (스키마의 모든 일반 테이블 + 구조 + 프로시저)
```

실행하면 이렇게 출력된다:
```
[run] backup dir: /현재경로/backup  →  컨테이너 /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 특정 테이블 백업 (1개 / 여러 개)

```bash
# 1개
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 여러 개 — 반복(-t A -t B) 또는 콤마(-t A,B,C), 혼용 가능
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → 테이블마다 한 폴더씩: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 여러 테이블을 줘도 커넥션은 **1개만** 열어 순차 처리한다. 한 테이블이 실패해도 나머지는 계속된다.

### 5-4. 프로시저 동반 백업

테이블 단위(`-t`) 백업이면, **같은 스키마에서 이름에 그 테이블명이 포함된 프로시저**를 찾아
DDL 을 `schema.ddl.sql` 끝에 함께 담는다(기본 ON). 명명 규칙을 가정하지 않는 단순 부분 일치다.

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql 끝에:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 끄려면 `--no-procedures`.
- 어떤 프로시저가 포함/스킵됐는지는 `MANIFEST.txt` 의 `procedures:` 섹션에 남는다.
- (스키마 전체 덤프는 프로시저가 이미 포함되므로 이 추가 동작이 필요 없다.)

### 5-5. 모드 — 구조만 / 데이터만

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # DDL 만 (.dat X)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # 데이터만 (DDL/load.sql X)
```

### 5-6. 결과 확인

```bash
ls -R backup/MY_SCHEMA/TB_SAMPLE/
cat  backup/MY_SCHEMA/TB_SAMPLE/MANIFEST.txt
```

`MANIFEST.txt` 예:
```
v_dump manifest
  host        : 10.0.0.5:5433
  database    : MYDB
  scope       : MY_SCHEMA.{TB_SAMPLE}
  tables      : 1
  rows total  : 7751
  procedures  : 1 included
  ...
procedures:
  PROC_TB_SAMPLE_1	included
```

> 산출 파일은 **호스트 사용자 소유**로 떨어진다(컨테이너 root 아님). 호스트에서 그냥 지우고 옮길 수 있다.

---

## 6. 복원

### 6-1. 복원 모델 이해

기본 복원은 **데이터 적재(`COPY`)** 다. 즉 **대상 테이블이 미리 존재**해야 한다.
구조가 없으면 `--with-ddl` 로 구조부터 만들고 적재한다.

복원 경로는 **backup 기준 상대경로**(말단 폴더)로 적는다.

### 6-2. 전체/단일 폴더 복원 (데이터)

```bash
# 스키마 전체 백업본 복원
./v_dump-docker.sh restore MY_SCHEMA/all

# 단일 테이블 폴더 복원
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 선택 복원 (폴더 안에서 일부 테이블만)

`all` 처럼 여러 테이블이 든 폴더에서 일부만 적재한다. 지정 테이블의 `COPY` 문만 추려 실행하며,
폴더에 없는 테이블을 주면 **실행 전에 실패**시켜 사고를 막는다.

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 구조부터 생성 후 적재 — `--with-ddl`

빈 대상(테이블이 아직 없는 DB)에 복원할 때. `schema.ddl.sql`(테이블+프로시저)을 먼저 실행하고 데이터를 적재한다.

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- DDL 이 **멱등**(아래 6-5)이라 이미 있는 객체가 있어도 그냥 넘어간다.
- `-t` 와 함께 주면 **구조는 전체** `schema.ddl.sql` 로 만들고 **데이터는 지정 테이블만** 넣는다.

### 6-5. 멱등성 (재실행 안전)

`schema.ddl.sql` 의 DDL 은 다시 실행해도 에러가 안 나도록 변환되어 있다.

| 원본 | 저장되는 형태 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 같은 DDL 을 두 번 돌려도 "already exists" 에러 없이 `nothing was done` 으로 넘어간다.
(단 `ALTER TABLE ... ADD CONSTRAINT` 같은 제약조건은 멱등 대상이 아니라 재실행 시 중복될 수 있다.)

### 6-6. 다른 서버로 복원

복원 대상이 백업 원본과 다른 DB 면, 그 실행에만 접속 정보를 덮어쓴다.

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 운영 시나리오(레시피)

### 7-1. 운영 → 개발 DB 로 특정 테이블 몇 개 이관

```bash
# 1) 운영에서 백업 (yaml = 운영 접속)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 개발 DB 로 구조+데이터 복원 (env 로 개발 접속 덮어쓰기)
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. 스키마 통째 백업 보관

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. 프로시저까지 포함해 백업/복원

```bash
# 백업: -t 백업이면 프로시저 자동 포함(기본)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 복원: --with-ddl 이 테이블+프로시저 DDL 을 함께 생성 후 데이터 적재
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 임의 점검 쿼리

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # DDL만 수동 실행
```

---

## 8. 명령어 레퍼런스

### 8-1. 래퍼 (`v_dump-docker.sh`)

```
./v_dump-docker.sh <명령> [인자...]

명령:
  dump    <v_dump 인자>   백업. -o 는 자동(/backup). 결과는 ./backup/<schema>/<table|all>/
  restore <폴더> [옵션]   복원. 폴더 = backup 기준 상대경로(말단 폴더)
  vsql    <vsql 인자>     컨테이너 vsql 원시 실행(점검/수동 SQL)
  help                    도움말
```

### 8-2. dump 인자

| 인자 | 설명 |
|---|---|
| `--schema, -n <S>` | 대상 스키마 (필수) |
| `--table, -t <T>` | 특정 테이블. 반복/콤마로 여러 개. 생략 시 스키마 전체(`all`) |
| `--schema-only` | DDL 만 |
| `--data-only` | 데이터만 |
| `--with-procedures` / `--no-procedures` | 프로시저 동반 추출 ON/OFF (기본 ON) |

> `-o` 는 래퍼가 `/backup` 으로 자동 지정하므로 줄 필요 없다.

### 8-3. restore 옵션

| 옵션 | 설명 |
|---|---|
| `-t, --table <T>` | 폴더 안에서 지정 테이블의 COPY 만 적재 (반복/콤마) |
| `--with-ddl` | 데이터 적재 전에 `schema.ddl.sql`(구조+프로시저) 먼저 실행 |

### 8-4. 접속 정보 (우선순위: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. 기타 환경변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `BACKUP_DIR` | `$PWD/backup` | 백업 폴더 위치 강제 지정 |
| `IMAGE` | `v_dump:latest` | 사용할 이미지 태그 |
| `V_DUMP_YAML` | (자동탐색) | yaml 경로 강제 지정 |

---

## 9. 트러블슈팅

### 빌드/이미지

**Q. 소스를 고쳐 다시 빌드했는데 옛 동작 그대로다.**
→ 예전엔 buildkit 캐시가 소스를 못 잡는 사고가 있었다. 현재는 cache-bust 가 적용돼
`./docker/build-image.sh` 만 돌리면 소스가 항상 반영된다. 그래도 의심되면:
`docker run --rm --entrypoint grep v_dump:latest -c "<바뀐 코드 일부>" /app/v_dump/dumper.py`
로 이미지 안 코드를 직접 확인하거나, `docker build --no-cache ...` 로 강제 재빌드.

**Q. `RUN vsql --version` 에서 빌드가 실패한다.**
→ vsql 이 링크하는 시스템 라이브러리가 빠진 것. `Dockerfile` 의 apt 설치 줄에 해당 `.so`
패키지를 추가한다(기본: `libssl3`, `libreadline8`).

### 연결

**Q. 컨테이너에서 Vertica 에 못 붙는다.**
→ `./v_dump-docker.sh vsql -c "SELECT 1;"` 로 격리 점검. 호스트-온리/사설망이면 컨테이너의
기본 브리지에서 라우팅이 되는지 확인(필요 시 `--network host` 를 래퍼에 추가).

### 백업

**Q. 프로시저가 안 따라온다.**
→ ① 테이블 단위(`-t`) 백업이어야 한다(스키마 전체는 EXPORT 가 이미 포함). ② 프로시저 이름에
대상 테이블명이 실제로 포함돼 있어야 한다. ③ `MANIFEST.txt` 의 `procedures:` 에 `skipped` 로
찍혔으면 사유를 본다(무인자 프로시저는 개별 추출이 안 돼 스킵될 수 있음).

**Q. 텍스트에 줄바꿈/탭이 있는 데이터가 깨질까?**
→ 안 깨진다. escape 가 Vertica COPY 규약(백슬래시+원본 바이트)에 맞춰져 있어
줄바꿈·탭·`|`·`\` 가 무손실로 라운드트립된다.

### 복원

**Q. 복원 시 `COPY: Input record has different number of columns`.**
→ `.dat` 의 컬럼 수와 대상 테이블 구조가 안 맞는 경우. 같은 백업본의 `schema.ddl.sql` 로
구조를 맞추거나(`--with-ddl`), 대상 테이블 정의를 확인한다.

**Q. `--with-ddl` 실행 중 빨간 오류가 보인다.**
→ 멱등이라 "already exists" 는 `nothing was done` 으로 넘어간다. 다만 `ON_ERROR_STOP` 을 꺼
두었기 때문에 제약조건 중복 등 일부 오류는 메시지만 출력되고 진행한다. 구조가 진짜로 안
만들어졌으면 이어지는 데이터 적재(COPY)가 명확히 실패해 알려준다.

---

## 10. 주의사항·한계

- **운영 DB 주의**: `restore` / `--with-ddl` 은 대상 DB 에 **쓰기**가 발생한다. 운영 테이블에
  복원하면 데이터가 누적되거나 프로시저가 교체된다. 대상 접속 정보를 반드시 확인할 것.
- **외부 테이블**은 데이터가 Vertica 밖에 있어 자동 제외된다(DDL 은 포함, `.dat` 없음).
- **무인자 프로시저**는 개별 DDL 추출이 안 돼 스킵될 수 있다(매니페스트에 기록).
- **제약조건(ALTER ADD CONSTRAINT)** 은 멱등 대상이 아니라 재실행 시 중복될 수 있다.
- vsql 은 **amd64** 바이너리다. 이미지/호스트가 x86_64 여야 한다.
- 비밀번호는 평문 yaml 이므로 파일 권한·git 제외에 유의한다.

---

## 부록 A. 빠른 시작 체크리스트

```
[빌드 머신]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  (접속정보 입력)
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[폐쇄망 호스트]
□ tar xzf v_dump-bundle.tar.gz
□ docker load -i v_dump-image.tar
□ chmod +x v_dump-docker.sh
□ ./v_dump-docker.sh vsql -c "SELECT version();"   (연결 확인)

[백업]
□ ./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
□ ls backup/MY_SCHEMA/TB_SAMPLE/

[복원]
□ ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

## 부록 B. 자주 쓰는 한 줄

```bash
# 연결 확인
./v_dump-docker.sh vsql -c "SELECT version();"

# 스키마 전체 백업
./v_dump-docker.sh dump --schema MY_SCHEMA

# 테이블 여러 개 백업 (+프로시저)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 단일 테이블 복원 (구조부터)
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 다른 DB 로 복원
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. All rights reserved.
