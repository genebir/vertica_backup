# v_dump User Guide (Docker / Air-gapped)

[🇰🇷 한국어](GUIDE.md) · **🇬🇧 English** · [🇯🇵 日本語](GUIDE.ja.md) · [🇨🇳 中文](GUIDE.zh.md)

A practical guide to operating **v_dump** — a tool that backs up Vertica schemas, tables, and procedures to files and reloads (restores) them with `vsql` — as a **Docker container**.

> This document assumes an air-gapped (no internet) server where installing Python directly is difficult.
> Python, vsql, and dependencies are bundled into a single image so that backup/restore runs in a container.
> For reference-level details see `README.md`; for the operational flow, read this document.

---

## Table of Contents

1. [Overview — What and How](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Building the Image & Bringing It Into the Air-gapped Network](#3-building-the-image--bringing-it-into-the-air-gapped-network)
4. [Initial Setup — Connection Info](#4-initial-setup--connection-info)
5. [Backup (Dump)](#5-backup-dump)
6. [Restore](#6-restore)
7. [Operational Scenarios (Recipes)](#7-operational-scenarios-recipes)
8. [Command Reference](#8-command-reference)
9. [Troubleshooting](#9-troubleshooting)
10. [Cautions & Limitations](#10-cautions--limitations)

---

## 1. Overview

### 1-1. What v_dump Does

| Stage | Content | Output/Tool |
|---|---|---|
| **Backup (dump)** | Extracts table data into `COPY`-compatible `.dat` files and structure into `schema.ddl.sql` | `vertica-python` (pure-Python driver) |
| **Restore** | Reloads `.dat` via `COPY ... FROM LOCAL` | `vsql` |

Unlike `pg_dump`, which produces one lump of SQL, v_dump drops **data (.dat)** and **structure (DDL)** separately.
`.dat` follows Vertica's default `COPY` convention as-is (pipe delimiter, `\N` for NULL, backslash escaping), so it reloads as fast as possible.

### 1-2. How It Works at a Glance

```
┌─────────────────────────┐        backup        ┌──────────────────────┐
│  v_dump container        │  vertica-python →    │  Vertica (prod DB)    │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup (mounted)       │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │   restore  vsql COPY │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ host's ./backup folder is mounted to container's /backup
```

Core principle: **what doesn't change (code, python, vsql) goes in the image; what changes (connection info, backup files) is mounted/injected at runtime.**
→ Build the image once and reuse it.

### 1-3. Two Ways to Run

- **Docker (this document)** — Run the container via the `v_dump-docker.sh` wrapper. For air-gapped / hard-to-install-Python environments.
- **Native** — Install Python on the host and run `run.sh`. See chapters 2–6 of `README.md`.

This guide covers only the **Docker approach**.

---

## 2. Prerequisites

### Build machine (internet: yes)
- Docker (or podman)
- Internet connection (to pull the base image + download pip dependencies)
- Architecture: **x86_64 (amd64)** — the vsql binary is amd64, so the image is pinned to amd64 as well.

### Air-gapped host (internet: no)
- Docker (or podman) — **nothing else (Python/vsql, etc.) is required**
- Network reachability to the target Vertica (e.g., port `5433`)

### Materials (repository layout)
- `v_dump/` — the Python package (source)
- `docker/` — run wrapper, entrypoint, build/packaging scripts
- `assets/vertica-client-*.tar.gz` — the vsql client (bundled, for installing vsql into the image)
- root `Dockerfile` · `requirements.txt`

---

## 3. Building the Image & Bringing It Into the Air-gapped Network

Since an air-gapped network can't pull from a registry, **save the image as a tar and move it.**

### 3-1. Build (internet-connected build machine)

```bash
cd v_dump
./docker/build-image.sh            # build only
./docker/build-image.sh --save     # build + save v_dump-image.tar (for transport)
```

- The build machine is connected to the internet, so pip fetches the right wheels.
- At the end, `RUN vsql --version` self-verifies vsql linking. If this fails, add the missing system
  library to the apt line in the `Dockerfile`.
- **The source is always freshly applied on every build** (cache-bust is in effect). Fix the code, rebuild, done.

### 3-2. Building the Transport Bundle

Only 3 things need to go to the air-gapped network:

| File | Role | Required |
|---|---|---|
| `v_dump-image.tar` | The image itself (includes python, vsql, and all code) | ✅ |
| `docker/v_dump-docker.sh` | Run wrapper (mount/connection info/`-o` automatic) | ✅ |
| `v_dump.yaml` | Connection info (optional if you'll pass it via env) | △ |

```bash
# [build machine]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # if you'll fill in connection info

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. Transport & Registration (air-gapped host)

After moving `v_dump-bundle.tar.gz` via USB / internal network / etc.:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # register the image
docker images | grep v_dump             # confirm v_dump:latest
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` automatically selects podman if `podman` is present, otherwise `docker`.

---

## 4. Initial Setup — Connection Info

There are two ways to supply connection info, and you only need **one of them**.

### 4-1. (Recommended) yaml File

If you fill in `v_dump.yaml`, the wrapper finds it automatically and mounts it into the container. Fill it in once and you don't need to provide it every time.

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ Since the password is plaintext, it is not baked into the image (excluded via `.dockerignore`). It is mounted only at runtime.
> Auto-discovery order: `run-location/v_dump.yaml` → `run-location/backup/v_dump.yaml` → script's parent.

**Multi-node cluster.** If you list **multiple nodes separated by commas** in `host`:
- If the leading node goes down, it **automatically fails over** to the next node (8-second connection timeout per node)
- Parallel dump workers and restore COPY sessions are **distributed across the nodes**, relieving the single-node initiator bottleneck

```yaml
vertica:
  host: node1,node2,node3      # put the closest, fastest node first
  port: 5433                   # common port for all nodes
  ...
```

> If you list only one node, it behaves exactly as before (backward compatible).

### 4-2. Environment Variables (one-off / different DB)

Prepend them just for that run and they take precedence over the yaml.

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

Available variables: `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. Verifying the Connection

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# OK if it prints something like  Vertica Analytic Database v24.1.0-0
```

---

## 5. Backup (Dump)

### 5-1. Understand the Output Structure First

The backup folder is created under **`backup/` in the path where `v_dump-docker.sh` lives** (next to the script no matter where you run it; no `-o` needed).
Below it, things drop into a **`<schema>/<table>`** structure, with a full schema going to **`<schema>/all`**.

```
backup/                              # next to v_dump-docker.sh (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # full schema dump (without -t)
    │   ├── MANIFEST.txt             # metadata (row counts, failed/procedure lists)
    │   ├── schema.ddl.sql           # structure DDL (idempotent form)
    │   ├── load.sql                 # reload COPY statements
    │   └── MY_SCHEMA.<table>.dat    # per-table data
    ├── TB_SAMPLE/                # table specified with -t (one folder per table)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # this table + matching procedure DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

Each leaf folder (`all`, `TB_xxx`) is **a self-contained unit that can be restored on its own**.

### 5-2. Full Schema Backup

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (all regular tables in the schema + structure + procedures)
```

When run, it prints something like:
```
[run] backup dir: /current-path/backup  →  container /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. Specific Table Backup (one / several)

```bash
# one
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# several — repeat (-t A -t B) or comma (-t A,B,C), can be mixed
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → one folder per table: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> Even when you pass several tables, **only one** connection is opened and they are processed sequentially. If one table fails, the rest continue.

### 5-4. Backing Up Procedures Alongside

For a table-level (`-t`) backup, v_dump looks for **procedures in the same schema whose names contain that table name** and
includes their DDL at the end of `schema.ddl.sql` (ON by default). It's a simple substring match that assumes no naming convention.

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   at the end of schema.ddl.sql:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- To turn it off, use `--no-procedures`.
- Which procedures were included/skipped is recorded in the `procedures:` section of `MANIFEST.txt`.
- (A full schema dump already includes procedures, so this extra step isn't needed.)

### 5-5. Modes — Structure Only / Data Only

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # DDL only (no .dat)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # data only (no DDL/load.sql)
```

### 5-6. Checking the Result

```bash
ls -R backup/MY_SCHEMA/TB_SAMPLE/
cat  backup/MY_SCHEMA/TB_SAMPLE/MANIFEST.txt
```

Example `MANIFEST.txt`:
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

> Output files drop as **owned by the host user** (not container root). You can delete and move them on the host as usual.

### 5-7. Speed & Size — Adaptive Parallelism & Compression

**Adaptive parallelism (automatic).** The dump looks at the workload and parallelizes on its own — sequential for small jobs,
per-table parallelism when there are many tables, and row-level sharding for huge tables. Nothing to worry about.
The worker count defaults to `min(cores, 4)`. To tune it, use the environment variable:

```bash
V_DUMP_JOBS=8 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 8 workers
V_DUMP_JOBS=1 ./v_dump-docker.sh dump --schema MY_SCHEMA   # force sequential
```

**Multi-node is faster.** If you list nodes comma-separated in `host` (4-1), parallel workers/COPY sessions are
distributed across the nodes, relieving the bottleneck of data piling onto a single node. With 3 nodes, throughput rises accordingly.

**Compression (`--compress`).** Saves `.dat` as gzip (`.dat.gz`). For air-gapped transfer it **greatly reduces the size you carry
over USB** (5–10×, depending on the data). On restore, a `GZIP` filter is automatically embedded in `load.sql`, so
**Vertica COPY reads the compressed file directly** — no need to unpack it first, and it's **lossless**.

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --compress      # → MY_SCHEMA.<table>.dat.gz
```

> Every stage of the migration pipeline (① dump → ② transfer → ③ load) gets faster:
> ① parallel dump · ② lighter transfer load via `--compress` · ③ parallel restore too (chapter 6 below).

---

## 6. Restore

### 6-1. Understanding the Restore Model

The default restore is a **data load (`COPY`)**. That is, **the target table must already exist**.
If the structure is absent, use `--with-ddl` to create the structure first and then load.

The restore path is written as a **path relative to backup** (the leaf folder).

> **Restore is parallel too (automatic).** When there are multiple tables, COPY loads them concurrently over several sessions
> (tune with `V_DUMP_JOBS`; `=1` for sequential). Compressed backups (`.dat.gz`) restore as-is too — `load.sql`
> has a `GZIP` filter, so Vertica unpacks them automatically.
> Note that parallel commits **per table** (`=1` sequential is a single transaction). If you need full atomicity, use `V_DUMP_JOBS=1`.

### 6-2. Restoring a Whole / Single Folder (Data)

```bash
# restore a full-schema backup
./v_dump-docker.sh restore MY_SCHEMA/all

# restore a single table folder
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. Selective Restore (only some tables within a folder)

Load only part of a folder that contains many tables, like `all`. It extracts and runs only the `COPY` statements for the
specified tables, and if you pass a table that isn't in the folder, it **fails before execution** to prevent accidents.

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. Create Structure First, Then Load — `--with-ddl`

For restoring to an empty target (a DB where the tables don't exist yet). It runs `schema.ddl.sql` (tables + procedures) first, then loads the data.

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- Because the DDL is **idempotent** (see 6-5), it simply skips over objects that already exist.
- When combined with `-t`, the **structure is built from the full** `schema.ddl.sql` while **only the specified tables' data** is loaded.

### 6-5. Idempotency (safe to re-run)

The DDL in `schema.ddl.sql` is transformed so that re-running it doesn't raise errors.

| Original | Stored form |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ Running the same DDL twice passes with `nothing was done` instead of an "already exists" error.
(However, constraints like `ALTER TABLE ... ADD CONSTRAINT` are not idempotent and may be duplicated on re-run.)

### 6-6. Restoring to a Different Server

If the restore target is a different DB from the backup origin, override the connection info for that run only.

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. Operational Scenarios (Recipes)

### 7-1. Migrating a Few Specific Tables from Prod → Dev DB

```bash
# 1) back up from prod (yaml = prod connection)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) restore structure+data to dev DB (override dev connection via env)
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. Archiving a Whole-Schema Backup

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. Back Up / Restore Including Procedures

```bash
# backup: a -t backup includes procedures automatically (default)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# restore: --with-ddl creates table + procedure DDL together, then loads data
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. Ad-hoc Inspection Queries

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # run DDL only, manually
```

---

## 8. Command Reference

### 8-1. Wrapper (`v_dump-docker.sh`)

```
./v_dump-docker.sh <command> [args...]

commands:
  dump    <v_dump args>   Backup. -o is automatic (/backup). Result: ./backup/<schema>/<table|all>/
  restore <folder> [opts] Restore. folder = path relative to backup (leaf folder)
  vsql    <vsql args>     Raw vsql in the container (inspection / manual SQL)
  help                    Help
```

### 8-2. dump arguments

| Argument | Description |
|---|---|
| `--schema, -n <S>` | Target schema (required) |
| `--table, -t <T>` | Specific table. Repeat/comma for several. Omit for the full schema (`all`) |
| `--schema-only` | DDL only |
| `--data-only` | Data only |
| `--with-procedures` / `--no-procedures` | Extract procedures alongside ON/OFF (default ON) |
| `--compress` | `.dat` to gzip (`.dat.gz`) → smaller transfer/storage (auto, lossless on restore) |

> `-o` is set to `/backup` automatically by the wrapper, so there's no need to pass it.
> Parallelism is automatic (workload-based). Tune with `V_DUMP_JOBS` (8-5).

### 8-3. restore options

| Option | Description |
|---|---|
| `-t, --table <T>` | Load only the specified table's COPY within the folder (repeat/comma) |
| `--with-ddl` | Run `schema.ddl.sql` (structure + procedures) before loading data |

### 8-4. Connection Info (precedence: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. Other Environment Variables

| Variable | Default | Description |
|---|---|---|
| `V_DUMP_JOBS` | `auto` | Parallel worker count. `auto`=min(cores,4), integer=fixed, `1`=sequential. **Common to dump & restore** |
| `V_DUMP_PROGRESS` | `auto` | Force the progress bar on (`1`) / off (`0`). Default is only when on a terminal |
| `ENGINE` | (auto) | Force the container engine (`docker`\|`podman`). If unset, auto-selects the engine that has the image |
| `BACKUP_DIR` | `backup` next to the script | Force the backup folder location |
| `IMAGE` | `v_dump:latest` | Image tag to use |
| `V_DUMP_YAML` | (auto-discovery) | Force the yaml path |

---

## 9. Troubleshooting

### Build / Image

**Q. I fixed the source and rebuilt, but the old behavior remains.**
→ There used to be an issue where the buildkit cache didn't pick up source changes. Now cache-bust is in effect, so
just running `./docker/build-image.sh` always reflects the source. If you still suspect it:
`docker run --rm --entrypoint grep v_dump:latest -c "<part of the changed code>" /app/v_dump/dumper.py`
to inspect the code inside the image directly, or force a rebuild with `docker build --no-cache ...`.

**Q. The build fails at `RUN vsql --version`.**
→ A system library that vsql links against is missing. Add the corresponding `.so` package to the apt install line in
the `Dockerfile` (default: `libssl3`, `libreadline8`).

### Connection

**Q. The container can't connect to Vertica.**
→ Isolate-check with `./v_dump-docker.sh vsql -c "SELECT 1;"`. For host-only/private networks, verify routing works from
the container's default bridge (add `--network host` to the wrapper if needed).

### Backup

**Q. Procedures don't come along.**
→ ① It must be a table-level (`-t`) backup (a full schema is already included by EXPORT). ② The procedure name must
actually contain the target table name. ③ If it shows as `skipped` in the `procedures:` of `MANIFEST.txt`, check the
reason (argument-less procedures may be skipped because they can't be extracted individually).

**Q. Will data with newlines/tabs in text get corrupted?**
→ It won't. The escaping conforms to the Vertica COPY convention (backslash + original bytes), so
newlines, tabs, `|`, and `\` round-trip losslessly.

### Restore

**Q. `COPY: Input record has different number of columns` during restore.**
→ The column count in `.dat` doesn't match the target table structure. Align the structure with the same backup's
`schema.ddl.sql` (`--with-ddl`), or check the target table definition.

**Q. I see red errors while running `--with-ddl`.**
→ Being idempotent, "already exists" passes as `nothing was done`. However, since `ON_ERROR_STOP` is left off,
some errors like duplicate constraints just print a message and proceed. If the structure really wasn't created,
the subsequent data load (COPY) fails clearly and tells you.

---

## 10. Cautions & Limitations

- **Beware of the prod DB**: `restore` / `--with-ddl` cause **writes** to the target DB. Restoring to a prod table
  accumulates data or replaces procedures. Always verify the target connection info.
- **External tables** are excluded automatically because their data lives outside Vertica (DDL is included, no `.dat`).
- **Argument-less procedures** may be skipped because their DDL can't be extracted individually (recorded in the manifest).
- **Constraints (ALTER ADD CONSTRAINT)** are not idempotent and may be duplicated on re-run.
- vsql is an **amd64** binary. The image/host must be x86_64.
- The password is plaintext yaml, so mind file permissions and git exclusion.

---

## Appendix A. Quick-Start Checklist

```
[build machine]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  (enter connection info)
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[air-gapped host]
□ tar xzf v_dump-bundle.tar.gz
□ docker load -i v_dump-image.tar
□ chmod +x v_dump-docker.sh
□ ./v_dump-docker.sh vsql -c "SELECT version();"   (verify connection)

[backup]
□ ./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
□ ls backup/MY_SCHEMA/TB_SAMPLE/

[restore]
□ ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

## Appendix B. Handy One-liners

```bash
# verify connection
./v_dump-docker.sh vsql -c "SELECT version();"

# full schema backup
./v_dump-docker.sh dump --schema MY_SCHEMA

# back up several tables (+ procedures)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# restore a single table (structure first)
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# restore to a different DB
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Kiseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. Licensed under the Apache License 2.0 (see LICENSE).
