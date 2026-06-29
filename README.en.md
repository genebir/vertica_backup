# v_dump — Vertica Data Backup Tool

[🇰🇷 한국어](README.md) · **🇬🇧 English** · [🇯🇵 日本語](README.ja.md) · [🇨🇳 中文](README.zh.md)

A backup utility that dumps Vertica schemas/tables to files in **the format Vertica can re-read the fastest**,
and lets you restore them later with a single line of `vsql -f load.sql`.

Unlike `pg_dump`, which dumps everything into a single SQL file, v_dump stores **the data itself as separate `.dat` files**.
These files follow Vertica `COPY`'s default conventions (pipe-delimited, `\N` for NULL, backslash escape) exactly,
so the most efficient loading is possible via `COPY ... FROM LOCAL`.

---

## 1. Directory Structure

```
except/
├── v_dump.sh                  # convenience shortcut (internally delegates to v_dump/run.sh)
└── v_dump/                    # ← deployment unit. Copy just this folder
    ├── install.sh             # idempotent install script (creates venv + dependencies)
    ├── run.sh                 # official launcher (own venv, location-independent)
    ├── requirements.txt       # pinned runtime dependencies
    ├── v_dump.yaml.example    # connection config template (committed)
    ├── v_dump.yaml            # actual connection config (created by install, git-excluded)
    ├── .gitignore             # excludes .venv / v_dump.yaml / backup / build artifacts
    ├── .gitattributes         # newline policy (scripts fixed to LF, mark binaries)
    ├── .venv/                 # virtual environment created by install (not carried over on deploy)
    ├── __main__.py            # `python -m v_dump` entry point
    ├── cli.py                 # argparse CLI
    ├── config.py              # connection config loader (CLI > env > yaml)
    ├── connection.py          # vertica_python connection context manager
    ├── inspector.py           # v_catalog metadata queries (schemas/tables/columns)
    ├── ddl.py                 # DDL extraction based on EXPORT_OBJECTS
    ├── data.py                # table → .dat serialization + COPY statement generation
    ├── escape.py              # Vertica COPY convention escaping
    ├── dumper.py              # orchestrator (entire flow)
    ├── Dockerfile             # combined dump+restore image (python + vsql)
    ├── .dockerignore          # exclusion list for image build context
    ├── vertica-client-*.tar.gz# vsql client (for installing vsql in the image)
    ├── docker/
    │   ├── entrypoint.sh      # dump/restore/vsql dispatcher
    │   ├── filter_load.py     # load.sql filter for selective restore
    │   ├── build-image.sh     # build (+--save tar) — for an internet-connected build machine
    │   └── v_dump-docker.sh   # air-gapped run wrapper (auto mount/connection info)
    ├── GUIDE.md               # operations guide (Docker/air-gapped, start-to-finish flow)
    └── README.md              # ← this document (reference)
```

> For the initial adoption/operations flow, it's faster to read **`GUIDE.md`** first. This README is an item-by-item reference.

> There are **two ways to run** it.
> - **Native** (`install.sh` + `run.sh`): when you can install Python directly on the target machine. See chapters 2–6 below.
> - **Docker** (`Dockerfile` + `docker/`): when installing Python is difficult, such as on air-gapped networks. See **chapter 11**.
>   It bundles Python, vsql, and dependencies into a single image and runs dump/restore via containers.

---

## 2. Installation (Deploying to Another Environment)

The deployment unit is **a single `v_dump/` folder**. Copy it as-is to a fresh environment where nothing is installed,
then just run `install.sh` and it creates its own venv and installs dependencies. **It is safe (idempotent) to run multiple times.**

```bash
# 1) Copy the whole folder to the new environment (example)
scp -r v_dump user@newhost:/opt/

# 2) Install
cd /opt/v_dump
./install.sh

# 3) Enter connection info (install copies v_dump.yaml.example to create it)
vi v_dump.yaml

# 4) Run
./run.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

What `install.sh` does:
- Auto-detects `python3` (>=3.8, one capable of `venv`+`ensurepip`) — can also be specified with `PYTHON=/path/to/python3 ./install.sh`
- Creates `v_dump/.venv` (only when absent; a broken venv is auto-recreated)
- Installs `requirements.txt`
- If `v_dump.yaml` doesn't exist, copies `v_dump.yaml.example` to create it (**never overwrites existing config**)
- Grants execute permission to `run.sh` + validates import/CLI

> **Prerequisites**: the target machine must have `python3` and `python3-venv` (Debian/Ubuntu) or equivalent.
> If absent, `install.sh` tells you which package to install and stops.
>
> On an air-gapped network with no internet, copy along the wheels downloaded with `pip download -r requirements.txt -d wheels`
> on a machine with the same OS/Python version, and install with
> `.venv/bin/pip install --no-index --find-links wheels -r requirements.txt` instead of `./install.sh`.

### Run Interfaces — `run.sh` vs `v_dump.sh`

- `v_dump/run.sh` — the **official launcher**. Uses its own venv (`v_dump/.venv`), location-independent. Use this in deployed environments.
- `except/v_dump.sh` — a convenience shortcut within this project. It merely delegates internally to `v_dump/run.sh`.

Both behave identically. You can alias either one.

```bash
echo 'alias v_dump=/opt/v_dump/run.sh' >> ~/.bashrc && source ~/.bashrc
```

### Default Connection Config

It goes into `v_dump/v_dump.yaml` (created by install from the example). To connect to another environment, just change the values.

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: VMart
  tlsmode: disable
```

> Since the password is stored in plaintext, be sure to add the entry
> ```
> v_dump/v_dump.yaml
> ```
> to your `.gitignore`.

### 2-3. Config Precedence

When the same item exists in multiple places, it is decided in the following order.

1. **CLI arguments** — `--host`, `--user`, `--password`, `--database`, `--port`, `--tlsmode`
2. **Environment variables** — `VERTICA_HOST`, `VERTICA_PORT`, `VERTICA_USER`, `VERTICA_PASSWORD`, `VERTICA_DATABASE`, `VERTICA_TLSMODE`
3. **yaml specified with `--config`**
4. **default yaml** (`v_dump/v_dump.yaml`)

### 2-4. Companion Procedure Extraction

For table-level (`-t`) backups, it finds **procedures in the same schema whose name contains that table name**
and dumps their DDL along with it (appended to `schema.ddl.sql`). It assumes no naming convention (prefix/suffix);
it's a simple substring match, so no separate configuration is needed.

- Example: table `TB_SAMPLE` → procedure `PROC_TB_SAMPLE_1` (name contains the table name) is matched automatically.
- The procedure list is read from `v_catalog.user_procedures`, and procedures with arguments are extracted via
  `EXPORT_OBJECTS` including their signature.
- Entries with no match or that fail extraction are skipped, and the reason is recorded in `MANIFEST.txt` (the dump continues).
- To turn it off entirely, use `--no-procedures` on the CLI.

---

## 3. Running

### 3-1. The Fastest One-Liner

```bash
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

`v_dump.sh` automatically sets up the venv's python + PYTHONPATH. It works **no matter which directory you call it from**.

Registering an alias is recommended:

```bash
echo 'alias v_dump=/home/duarl/KRWay/except/v_dump.sh' >> ~/.bashrc
source ~/.bashrc
v_dump --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

### 3-2. Common Patterns

```bash
# (In all examples, -o is the base path. Below it, <schema>/<table|all> is created.)

# Whole schema (74 tables at once)          → ./backup/MY_SCHEMA/all/
v_dump --schema MY_SCHEMA -o ./backup

# A specific table only                     → ./backup/MY_SCHEMA/TB_SAMPLE/
v_dump --schema MY_SCHEMA --table TB_SAMPLE -o ./backup

# Multiple tables (repeat/comma/mixed) — one folder per table
v_dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2 -o ./backup
v_dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C -o ./backup

# For table-level backups, procedure DDL whose name contains that table name is also auto-included (ON by default)
v_dump --schema MY_SCHEMA -t TB_SAMPLE -o ./backup            # + procedure DDL
v_dump --schema MY_SCHEMA -t TB_SAMPLE --no-procedures -o ./backup  # exclude procedures

# DDL only (no .dat created)
v_dump --schema MY_SCHEMA --schema-only -o ./backup

# Data only (no DDL/load.sql created)
v_dump --schema MY_SCHEMA --data-only -o ./backup

# Connect to another environment
v_dump --host 10.0.0.5 --user readonly --password secret \
       --database VMart --schema PUBLIC -o ./backup

# Use a different config file
v_dump --config /etc/v_dump/prod.yaml --schema MY_SCHEMA -o ./backup

# Help
v_dump --help
```

---

## 4. Outputs

`-o` is the **base path**, and underneath it a `<schema>/<table>` structure (for a whole schema, `<schema>/all`) is created.
Each leaf folder is a **self-contained dump unit** that can be restored on its own.

```
<base>/                            # path given via -o (Docker: /backup = fixed backup)
├── <schema>/
│   ├── all/                      # whole-schema dump (without -t)
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql
│   │   ├── load.sql
│   │   └── <schema>.<table>.dat  # all tables
│   ├── <table_A>/                # table specified via -t (one folder per table)
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql        # DDL for that table (+ matched procedures)
│   │   ├── load.sql
│   │   └── <schema>.<table_A>.dat
│   └── <table_B>/
│       └── ...
```

File composition inside each folder:

| File | Contents |
|---|---|
| `MANIFEST.txt` | dump metadata (host, scope, row counts, failures/procedure list) |
| `schema.ddl.sql` | `CREATE SCHEMA / TABLE ...` (+ matched procedure DDL for table-level) |
| `load.sql` | set of `COPY` statements that re-load the folder's `.dat` files |
| `<schema>.<table>.dat` | per-table data file |

### 4-1. `.dat` File Format

It matches Vertica `COPY`'s default conventions exactly.

| Item | Value | Note |
|---|---|---|
| delimiter | `|` | Vertica COPY default |
| NULL representation | `\N` | `NULL AS '\N'` |
| ESCAPE | `\` | Vertica COPY default |
| row terminator | LF (`\n`) | |
| encoding | UTF-8 | |
| quote handling | no ENCLOSED BY | only delimiter/terminator/escape are escaped with `\` |

The escape targets are only the **escape char (`\`), the delimiter (`|`), and the row terminator (LF/CR)**.
Since Vertica COPY's escape treats "the 1 byte following `\` as literal data," the escape is written as
**backslash + the original byte** (e.g., newline → `\`+LF). If you append a literal `n` like `\n`,
it gets garbled into the literal character `n` on load. Tab, vertical tab, form feed, etc. are neither
delimiters nor terminators, so they are left as-is without escaping.

Representation by type:
- BOOLEAN: `t` / `f`
- INT, NUMERIC, FLOAT: string as-is
- DATE: `YYYY-MM-DD`
- TIMESTAMP: `YYYY-MM-DD HH:MM:SS[.ffffff]`
- TIME: `HH:MM:SS`
- VARBINARY: `\xNN` sequence
- NULL: `\N`

Example:
```
1|Store1|1|16 Elm St|Concord|CA|West|Plan1|Premium|None|1000|2000|2007-03-01|\N|18|12576|39|2284
```

### 4-2. `schema.ddl.sql`

It converts the DDL produced by Vertica's `EXPORT_OBJECTS()` into a **re-run-safe (idempotent)** form and stores it.
It includes `CREATE SCHEMA`, `CREATE SEQUENCE`, `CREATE TABLE`, `CREATE PROJECTION`, etc.

Idempotent conversion (re-running on an already-existing object passes without error):

| Original | Converted |
|---|---|
| `CREATE SCHEMA` / `TABLE` / `SEQUENCE` / `PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE` / `VIEW` | `CREATE OR REPLACE ...` |

> Since substitution is done only at the start of a line (`^`), text inside the procedure body is untouched.
> (Constraints `ALTER TABLE ... ADD CONSTRAINT` are not idempotent — re-running may cause duplicate errors.)

In a table-level (`-t`) backup, if there are procedures whose name contains that table name, a
`-- ===== stored procedures =====` section is appended at the end of the file with the `CREATE PROCEDURE` DDL of the matched procedures.
Procedures that don't exist or fail extraction are skipped, and the reason is recorded in `MANIFEST.txt` (the dump continues).
(A whole-schema dump doesn't have this separate section, since `EXPORT_OBJECTS(schema)` already includes procedures.)

### 4-3. `load.sql`

A set of `COPY FROM LOCAL` statements to re-load each `.dat`. It is wrapped in a single transaction, so a mid-way failure rolls back everything.

```sql
\set ON_ERROR_STOP on
BEGIN;

COPY "MY_SCHEMA"."TB_SAMPLE" (col1, col2, ...) FROM LOCAL 'MY_SCHEMA.TB_SAMPLE.dat'
  DELIMITER '|' NULL AS '\N' ENCLOSED BY '' ABORT ON ERROR DIRECT;
...

COMMIT;
```

> `DIRECT` is attached so it loads straight into ROS without going through WOS → fastest for large volumes.

### 4-4. `MANIFEST.txt`

```
v_dump manifest
  host        : 10.0.0.5:5433
  database    : VMart
  scope       : DS.{TB_A,TB_B}          # for multiple tables, denoted with {..}
  dumped at   : 2026-05-20 10:34:40
  schema_only : False
  data_only   : False
  tables      : 2
  rows total  : 3953181
  procedures  : 2 included              # number of included procedures
  ddl file    : schema.ddl.sql
  load script : load.sql

files:
  DS.TB_A.dat	1200 rows
  DS.TB_B.dat	980 rows

failed:                                  # (only when there are failures)
  DS.SOME_TABLE	<first line of error>

procedures:                              # (only when procedure extraction was attempted)
  PROC_TB_SAMPLE_1	included
  PROC_TB_SAMPLE_2	skipped (export result empty)
```

---

## 5. Restore

```bash
# Enter the leaf folder to restore (whole schema is <schema>/all, single table is <schema>/<table>)
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql   # create tables
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql         # load data
```

The `COPY ... FROM LOCAL` in `load.sql` finds the `.dat` files relative to **the vsql client's current directory**.
So it must be run from the backup directory.

> If you want to accumulate onto existing data rather than an empty DB, skip running `schema.ddl.sql`
> and run only `load.sql`. Note that if there were column changes, the COPY statements may fail.

- To **restore procedures** as well, just run `schema.ddl.sql` (they're in the same file as the table DDL).
  Since the DDL is idempotent (`IF NOT EXISTS` / `OR REPLACE`), **it just passes over already-existing objects** (re-run safe).
- When **restoring with Docker**, there are options that handle the two steps above in one command — `--with-ddl` (create the structure first),
  `-t` (load only some tables). See the restore section of **chapter 11**.

---

## 6. Full CLI Options

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
  --schema, -n SCHEMA    name of the schema to dump (required)
  --table, -t TABLE      a specific table only. Specify multiple via repeat
                         (-t A -t B) or comma (-t A,B). If omitted, whole schema.

mode:
  --schema-only          DDL only (no data files)
  --data-only            data files only (no DDL/load.sql)

procedures:
  --with-procedures      also extract matched procedure DDL for table-level (-t) backups (ON by default)
  --no-procedures        turn off procedure DDL extraction

output:
  --output, -o OUTPUT    output directory. Created if absent. (required)

connection:
  --host HOST
  --port PORT
  --user, -U USER
  --password, -W PASSWORD
  --database, -d DATABASE
  --tlsmode {disable,prefer,require}
  --config CONFIG        yaml config file (default: v_dump/v_dump.yaml)
```

---

## 7. Behavioral Characteristics / Caveats

### 7-1. External Tables Are Auto-Skipped

Tables whose actual data lives outside Vertica, like `CREATE EXTERNAL TABLE ... AS COPY FROM 's3://...'`,
fail because the `SELECT` itself attempts to access external files. v_dump treats entries where
`v_catalog.tables.table_definition` is populated as external tables and
**auto-excludes them at the `list_tables_in_schema()` stage**.

→ The DDL of external tables is still included in `schema.ddl.sql`, but no `.dat` is generated.
Even after restore, the external data source must be alive for the data to be visible.

### 7-2. Single-Table Failure Isolation

Even if processing one table ends in an exception, the remaining tables continue. The failure reason is recorded in both:

- stderr: `[v_dump] WARN skip <schema>.<table>: <reason>`
- the `failed:` section of `MANIFEST.txt`

Any partially generated `.dat` is auto-deleted.

### 7-3. Tolerant Handling of Broken UTF-8 Bytes

Since `vertica_python`'s unicode decoding is `strict` by default, fetch halts if some column contains
broken multibyte data. v_dump opens with the connection option `unicode_error='replace'`, so the
corrupted position is replaced with `U+FFFD` and the dump proceeds without row-level loss.

> Since backup comes first, lenient is the default. If data integrity verification matters more, change
> `unicode_error` in `config.py` to `'strict'` (failed tables are shown in the manifest).

### 7-4. Memory

It fetches 10,000 rows at a time via a server-side cursor (`data._FETCH_SIZE`). Even tables with hundreds of millions
of rows can be processed in a streaming fashion without memory OOM. Output is flushed to disk immediately, one line at a time.

### 7-5. Sequences/Views/Procedures

DDL extraction is done via `EXPORT_OBJECTS('', 'scope')`. For a **whole-schema** dump, the scope is the schema, so
**all the objects Vertica exports** (schema, sequences, tables, projections, procedures, etc.) go straight into
`schema.ddl.sql`.

For a **table-level (`-t`)** dump, the scope narrows to the specified tables, so procedures are automatically excluded.
For this case, v_dump reads the schema's procedure list from `v_catalog.user_procedures`, picks only those
**whose name contains a target table name** (see `2-4`), attaches the argument signature, and extracts them via
`EXPORT_OBJECTS`, appending them to the end of `schema.ddl.sql`.
Entries that fail matching/extraction are skipped, and the reason is left in the manifest (the session is rolled back so the whole dump isn't broken).

> Because it's a substring match, procedures sharing the same name fragment may be caught together (intended behavior).
> If the extraction result is empty, it's marked as `skipped` in the manifest, so you'll know right away.

Data dumps (`.dat`) still target **tables only**.

### 7-6. Permissions

The dump executor needs the following permissions.

- `USAGE` on the target schema
- `SELECT` on the target tables
- permission to call `EXPORT_OBJECTS` (usually available to all users)
- permission to query `v_catalog.*` (granted by default)

The restore executor additionally needs `CREATE` permission and permission to use `COPY ... FROM LOCAL`.

---

## 8. Troubleshooting

### `No module named v_dump`

`python -m v_dump` requires the **parent directory** of `v_dump/` (i.e., `except/`) to be in cwd / PYTHONPATH.
Simply using the `v_dump.sh` launcher solves it.

### `dump failed: Cannot expand glob pattern due to error: Access Denied`

Occurs when there's no access permission to external files like S3 that an external table points to.
**Since the current version auto-excludes external tables**, it shouldn't occur. If it still appears,
the inspector filter condition doesn't match your environment, so check the `failed:` section of the manifest.

### `unicode_error`-related decode errors

The default `replace` should handle it, but if it's still a different encoding, the column may be stored as
LATIN1 or similar. Check the Vertica server-side `SET SESSION CHARACTERSET` or the column encoding.

### `ERROR: COPY: Input record N has different number of columns` during vsql restore

A case where the `.dat` serialization and the actual column count don't match. Almost always it means the data
itself contains a broken escape (e.g., a value with a standalone `\`). v_dump's escape converts every `\` in the input
to `\\`, so it normally doesn't occur. If you've touched the conversion logic, inspect the `_ESCAPES` mapping in `escape.py`.

### load.sql too long, memory burden

A `load.sql` with about 73 `COPY` statements is on the order of tens of KB. If there are thousands of tables, splitting is needed,
and in that case dumping per-schema separately is operationally safer.

---

## 9. One-Line Summary of Internal Modules

| Module | Responsibility |
|---|---|
| `config.py` | `ConnectionConfig` dataclass + CLI/env/yaml precedence decision |
| `connection.py` | `vertica_python.connect` context manager |
| `inspector.py` | `v_catalog` queries: schema/table/column lists, external table detection |
| `ddl.py` | wrapper for `EXPORT_OBJECTS()` calls |
| `escape.py` | single value/row → Vertica COPY body string conversion |
| `data.py` | table row streaming + COPY statement builder |
| `dumper.py` | overall flow orchestration + manifest/failure handling |
| `cli.py` | argparse + exit codes |
| `__main__.py` | `python -m v_dump` entry |

---

## 10. Quick Reference — One-Page Summary

```bash
# Backup (→ created in ./backup/MY_SCHEMA/all/)
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./backup

# Restore (cd into the leaf folder)
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql
```

---

## 11. Docker (Air-Gapped / Python-Install-Difficult Environments)

The approach for when installing Python directly on the target machine is difficult. It bundles **Python + vsql + dependencies**
into a single image and performs both dump and restore via containers. The "variable values" like connection info, backup files,
and dump targets are all injected at runtime via volumes/environment variables, so **the image is built only once** and reused.

```
image (immutable: python·vsql·code)  +  runtime mount (connection info·backup files)  =  container
```

### 11-1. Build (On an Internet-Connected Machine)

```bash
cd v_dump
./docker/build-image.sh            # build image only
./docker/build-image.sh --save     # build + save v_dump-image.tar (for air-gapped import)
```

- The build machine is connected to the internet, so pip fetches the wheels matching the base Python on its own
  (avoiding version-pin issues with bundled wheels).
- `vsql` is an x86_64 binary, so the image is also **fixed to amd64** (`--platform=linux/amd64` in the `Dockerfile`).
  Building on an arm64 Mac still produces an amd64 image.
- At the end of the build, `RUN vsql --version` self-validates the library linking. If it fails because some other `.so`
  is missing, just add that package to the apt line in the `Dockerfile`.

### 11-2. Air-Gapped Import — Assuming Only docker Is Available

An air-gapped network can't pull from a registry, so **save the image as a tar and move it.** There are only 3 things to bring:

| What to bring | What it is | Note |
|---|---|---|
| `v_dump-image.tar` | the image itself | `--save` artifact. Contains python·vsql·code·`filter_load.py` all in |
| `docker/v_dump-docker.sh` | the run wrapper | automates mount/connection info assembly (raw `docker run` works without it, see 11-6) |
| `v_dump.yaml` | connection info | or can be omitted if you'll provide it via env |

```bash
# [internet build machine] bundle into one blob
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml          # if you'll fill in connection info
tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

# ── Import v_dump-bundle.tar.gz into the air-gapped network (USB/internal network, etc.) ──

# [air-gapped, only docker installed]
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar             # register the image
docker images | grep v_dump                 # confirm v_dump:latest
vi v_dump.yaml                              # enter connection info (or env)
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` auto-selects podman if `podman` is present, otherwise `docker`.
> It works as-is even in environments with only docker.

### 11-3. Dump (Container)

The backup folder is created at **`backup/` in the path where `v_dump-docker.sh` lives** and mounted to the container's `/backup`
(it always appears next to the script no matter where you run it). **You don't need to give `-o`** —
the wrapper always sets the output base to `/backup`, and below it the `<schema>/<table>` (whole schema is `<schema>/all`) structure is created.

```bash
# Connection info is auto-discovered from v_dump.yaml or env. -o omitted.
./v_dump-docker.sh dump --schema MY_SCHEMA        # → backup/MY_SCHEMA/all/

# Multiple tables + procedures (DS/DM automatic) — one folder per table
./v_dump-docker.sh dump --schema DS -t TB_A,TB_B  # → backup/DS/TB_A/ , backup/DS/TB_B/
```

→ The result lands in `backup/<schema>/...` next to the script.
At run time, that path is printed as `[run] backup dir: ...`. To put it elsewhere, just override with `BACKUP_DIR=/path`.

### 11-4. Restore (Container)

Write the restore path as a **path relative to backup** (`<schema>/<table>` or `<schema>/all`).
Since `COPY ... FROM LOCAL` is a client-relative path, the wrapper automatically enters that folder.

```bash
# Restore whole schema (point at the leaf folder)
./v_dump-docker.sh restore MY_SCHEMA/all

# Restore a single table folder
./v_dump-docker.sh restore DS/TB_A

# Create structure first, then load — run table+procedure DDL first on an empty target
./v_dump-docker.sh restore DS/TB_A --with-ddl

# Load only some tables picked from an all folder
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_A,TB_B

# Ad-hoc check: raw vsql execution
./v_dump-docker.sh vsql -c "SELECT version()"
```

- `--with-ddl` runs `schema.ddl.sql` (tables+procedures) **before** loading data.
  Since the DDL is idempotent (`IF NOT EXISTS` / `OR REPLACE`), **it just passes over already-existing objects**.
  If structure creation actually failed, the subsequent COPY clearly fails and lets you know.
- If you give `-t` and `--with-ddl` together, **the structure is built from the full** `schema.ddl.sql` and **only the specified tables' data** is loaded.

### 11-5. Injecting Connection Info (Precedence: env > yaml)

| Method | Usage | Note |
|---|---|---|
| yaml (recommended) | fill in `v_dump.yaml` once and it's auto-discovered | no need to pass it each time. Plaintext password, so not baked into the image |
| environment variables | prepend `VERTICA_*=...` to that run | for one-off connections to a different DB. Takes precedence over yaml |

```bash
# Usual: yaml as-is → pass nothing
./v_dump-docker.sh dump --schema DS -t TB_A

# Restore only to a different server: override with env for that run only
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh restore DS/TB_A --with-ddl
```

Of `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`,
only the ones that are set are passed to the container. To specify the yaml path directly, use `V_DUMP_YAML=/path/v_dump.yaml`.

### 11-6. Raw `docker run` Without the Wrapper

The command that `v_dump-docker.sh` expands to is ultimately just this (you can write it directly):

```bash
docker run --rm \
  -v "$PWD/backup:/backup" \
  -v "$PWD/v_dump.yaml:/app/v_dump/v_dump.yaml:ro" \
  v_dump:latest \
  dump --schema DS -t TB_A,TB_B -o /backup       # → /backup/DS/TB_A , /backup/DS/TB_B

# Connection info via env (restore points at the leaf folder):
docker run --rm -v "$PWD/backup:/backup" \
  -e VERTICA_HOST=10.0.0.5 -e VERTICA_USER=dbadmin \
  -e VERTICA_PASSWORD=*** -e VERTICA_DATABASE=VMart \
  v_dump:latest restore /backup/DS/TB_A --with-ddl
```

Container entrypoint commands: `dump` / `restore` / `vsql` / `shell` / `help`.
Print help with `docker run --rm v_dump:latest help`.

### 11-7. When You Need to Rebuild the Image

Connection info, backup files, and dump targets are all runtime-injected, so they are **unrelated to the image**. A rebuild is only
needed **when the image's contents change**:

- `v_dump` code (`*.py`) changes
- `requirements.txt` dependency changes
- vsql (vertica-client) version replacement

Only then re-run 11-1 to import a new `v_dump-image.tar`.

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. All rights reserved.
