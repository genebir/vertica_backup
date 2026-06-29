# v_dump — Vertica 数据备份工具

[🇰🇷 한국어](README.md) · [🇬🇧 English](README.en.md) · [🇯🇵 日本語](README.ja.md) · **🇨🇳 中文**

将 Vertica 的模式/表以 **Vertica 能够再次最快读取的格式**导出为文件，
之后只需 `vsql -f load.sql` 一行命令即可恢复的备份工具。

与 `pg_dump` 将内容导出为单个 SQL 文件不同，v_dump 将**数据本身保存为独立的 `.dat` 文件**。
该文件完全遵循 Vertica `COPY` 的默认约定（pipe 分隔、`\N` 表示 NULL、反斜杠转义），
因此可以通过 `COPY ... FROM LOCAL` 进行最高效的加载。

---

## 1. 目录结构

```
except/
├── v_dump.sh                  # 便捷快捷方式（内部委托给 v_dump/run.sh）
└── v_dump/                    # ← 部署单元。只需复制此文件夹即可
    ├── install.sh             # 幂等安装脚本（创建 venv + 依赖）
    ├── run.sh                 # 正式启动器（自带 venv，位置无关）
    ├── requirements.txt       # 锁定版本的运行时依赖
    ├── v_dump.yaml.example    # 连接配置模板（纳入提交）
    ├── v_dump.yaml            # 实际连接配置（由 install 生成，git 排除）
    ├── .gitignore             # 排除 .venv / v_dump.yaml / backup / 构建产物
    ├── .gitattributes         # 换行策略（脚本固定为 LF，标记二进制文件）
    ├── .venv/                 # install 创建的虚拟环境（部署时不随带）
    ├── __main__.py            # `python -m v_dump` 入口点
    ├── cli.py                 # argparse CLI
    ├── config.py              # 连接配置加载器（CLI > env > yaml）
    ├── connection.py          # vertica_python 连接上下文管理器
    ├── inspector.py           # v_catalog 元数据查询（模式/表/列）
    ├── ddl.py                 # 基于 EXPORT_OBJECTS 的 DDL 提取
    ├── data.py                # 表 → .dat 序列化 + 生成 COPY 语句
    ├── escape.py              # Vertica COPY 约定的转义
    ├── dumper.py              # 编排器（整体流程）
    ├── Dockerfile             # 转储+恢复一体化镜像（python + vsql）
    ├── .dockerignore          # 镜像构建上下文排除列表
    ├── vertica-client-*.tar.gz# vsql 客户端（用于在镜像中安装 vsql）
    ├── docker/
    │   ├── entrypoint.sh      # dump/restore/vsql 分发器
    │   ├── filter_load.py     # 用于选择性恢复的 load.sql 过滤器
    │   ├── build-image.sh     # 构建（+--save tar）— 用于联网构建机
    │   └── v_dump-docker.sh   # 隔离网运行封装（自动挂载/连接信息）
    ├── GUIDE.md               # 运维指南（Docker/隔离网从头到尾的流程）
    └── README.md              # ← 本文档（参考手册）
```

> 初次引入/运维流程建议先看 **`GUIDE.md`** 会更快。本 README 是按条目的参考手册。

> 有**两种执行方式**。
> - **原生**（`install.sh` + `run.sh`）：能在目标机器上直接安装 Python 时。参见下文第 2~6 章。
> - **Docker**（`Dockerfile` + `docker/`）：隔离网等难以安装 Python 时。参见**第 11 章**。
>   将 Python、vsql、依赖打包进同一镜像，以容器方式进行转储/恢复。

---

## 2. 安装（部署到其他环境）

部署单元就是**单个 `v_dump/` 文件夹**。将其原样复制到一个未安装任何东西的新环境后，
只需运行 `install.sh`，就会创建自带 venv 并安装依赖。**多次执行也安全（幂等）**。

```bash
# 1) 将整个文件夹复制到新环境（示例）
scp -r v_dump user@newhost:/opt/

# 2) 安装
cd /opt/v_dump
./install.sh

# 3) 填写连接信息（install 会复制 v_dump.yaml.example 生成）
vi v_dump.yaml

# 4) 执行
./run.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

`install.sh` 所做的事：
- 自动探测 `python3`（>=3.8，支持 `venv`+`ensurepip` 的）— 也可通过 `PYTHON=/path/to/python3 ./install.sh` 指定
- 创建 `v_dump/.venv`（仅在不存在时，损坏的 venv 会自动重建）
- 安装 `requirements.txt`
- 若 `v_dump.yaml` 不存在，则复制 `v_dump.yaml.example` 生成（**绝不覆盖已有配置**）
- 赋予 `run.sh` 执行权限 + import/CLI 校验

> **前置要求**：目标机器上必须有 `python3` 和 `python3-venv`（Debian/Ubuntu）或同等组件。
> 若没有，`install.sh` 会提示需要安装哪个软件包并停止。
>
> 若是无网络的隔离网，请在相同 OS/Python 版本的机器上用 `pip download -r requirements.txt -d wheels`
> 下载的 wheel 一起复制过去，并以
> `.venv/bin/pip install --no-index --find-links wheels -r requirements.txt` 代替 `./install.sh` 进行安装。

### 执行接口 — `run.sh` vs `v_dump.sh`

- `v_dump/run.sh` — **正式启动器**。使用自带 venv（`v_dump/.venv`），位置无关。部署环境中使用它。
- `except/v_dump.sh` — 本项目内的便捷快捷方式。内部只是委托给 `v_dump/run.sh` 而已。

两者行为完全相同。别名指向哪一个都可以。

```bash
echo 'alias v_dump=/opt/v_dump/run.sh' >> ~/.bashrc && source ~/.bashrc
```

### 基本连接配置

写在 `v_dump/v_dump.yaml`（由 install 从 example 生成）。要连接到其他环境只需改值即可。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: VMart
  tlsmode: disable
```

> 由于密码以明文保存，请务必在 `.gitignore` 中加入
> ```
> v_dump/v_dump.yaml
> ```
> 这一项。

### 2-3. 配置优先级

当同一项出现在多处时，按以下顺序决定。

1. **CLI 参数** — `--host`、`--user`、`--password`、`--database`、`--port`、`--tlsmode`
2. **环境变量** — `VERTICA_HOST`、`VERTICA_PORT`、`VERTICA_USER`、`VERTICA_PASSWORD`、`VERTICA_DATABASE`、`VERTICA_TLSMODE`
3. **通过 `--config` 指定的 yaml**
4. **默认 yaml**（`v_dump/v_dump.yaml`）

### 2-4. 一并提取存储过程

按表（`-t`）备份时，会查找**同一模式中名称包含该表名的存储过程**，
并将其 DDL 一并导出（追加到 `schema.ddl.sql`）。这是不假定命名规则（前缀/后缀）的
简单部分匹配，因此无需额外配置。

- 例：表 `TB_SAMPLE` → 自动匹配存储过程 `PROC_TB_SAMPLE_1`（名称含表名）。
- 存储过程列表从 `v_catalog.user_procedures` 读取，带参数的存储过程会连同签名一起
  用 `EXPORT_OBJECTS` 提取。
- 无匹配或提取失败的会跳过，并在 `MANIFEST.txt` 中记录原因（转储继续进行）。
- 若要全部关闭，在 CLI 中使用 `--no-procedures`。

---

## 3. 执行

### 3-1. 最快的一行命令

```bash
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

`v_dump.sh` 会自动设置 venv 的 python + PYTHONPATH。**无论从哪个目录调用**都能工作。

建议注册别名：

```bash
echo 'alias v_dump=/home/duarl/KRWay/except/v_dump.sh' >> ~/.bashrc
source ~/.bashrc
v_dump --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

### 3-2. 常用模式

```bash
# （所有示例中 -o 都是基础路径。其下会生成 <schema>/<table|all>。）

# 整个模式（74 张表一次性）                  → ./backup/MY_SCHEMA/all/
v_dump --schema MY_SCHEMA -o ./backup

# 仅特定表                                   → ./backup/MY_SCHEMA/TB_SAMPLE/
v_dump --schema MY_SCHEMA --table TB_SAMPLE -o ./backup

# 多张表（重复/逗号/混用）— 每张表一个文件夹
v_dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2 -o ./backup
v_dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C -o ./backup

# 按表备份时，名称含该表名的存储过程 DDL 也会自动包含（默认 ON）
v_dump --schema MY_SCHEMA -t TB_SAMPLE -o ./backup            # +存储过程 DDL
v_dump --schema MY_SCHEMA -t TB_SAMPLE --no-procedures -o ./backup  # 排除存储过程

# 仅 DDL（不生成 .dat）
v_dump --schema MY_SCHEMA --schema-only -o ./backup

# 仅数据（不生成 DDL/load.sql）
v_dump --schema MY_SCHEMA --data-only -o ./backup

# 连接到其他环境
v_dump --host 10.0.0.5 --user readonly --password secret \
       --database VMart --schema PUBLIC -o ./backup

# 使用其他配置文件
v_dump --config /etc/v_dump/prod.yaml --schema MY_SCHEMA -o ./backup

# 帮助
v_dump --help
```

---

## 4. 输出产物

`-o` 是**基础路径**，其下会生成 `<schema>/<table>`（整个模式则为 `<schema>/all`）结构。
每个末端文件夹本身就是一个可独立恢复的**自包含转储单元**。

```
<基础路径>/                        # -o 给定的路径（Docker 中为 /backup = 固定 backup）
├── <schema>/
│   ├── all/                      # 整个模式的转储（不带 -t）
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql
│   │   ├── load.sql
│   │   └── <schema>.<table>.dat  # 所有表
│   ├── <table_A>/                # 用 -t 指定的表（每张表一个文件夹）
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql        # 该表（+匹配存储过程）的 DDL
│   │   ├── load.sql
│   │   └── <schema>.<table_A>.dat
│   └── <table_B>/
│       └── ...
```

每个文件夹内的文件构成：

| 文件 | 内容 |
|---|---|
| `MANIFEST.txt` | 转储元信息（主机、scope、行数、失败/存储过程列表） |
| `schema.ddl.sql` | `CREATE SCHEMA / TABLE ...`（若为按表则含匹配存储过程的 DDL） |
| `load.sql` | 将该文件夹的 `.dat` 重新加载的 `COPY` 语句集合 |
| `<schema>.<table>.dat` | 各表的数据文件 |

### 4-1. `.dat` 文件格式

与 Vertica `COPY` 的默认约定精确一致。

| 项目 | 值 | 备注 |
|---|---|---|
| 分隔符 | `|` | Vertica COPY default |
| NULL 表示 | `\N` | `NULL AS '\N'` |
| ESCAPE | `\` | Vertica COPY default |
| 行终结 | LF (`\n`) | |
| 编码 | UTF-8 | |
| 引号处理 | 无 ENCLOSED BY | 仅对分隔符/终结符/escape 用 `\` 转义 |

转义对象只有 **escape char（`\`）、分隔符（`|`）、行终结符（LF/CR）**。
由于 Vertica COPY 的 escape 会"将 `\` 之后的 1 字节原样视为数据"，
所以转义写成**反斜杠 + 原始字节**（例如：换行 → `\`+LF）。若像 `\n` 那样
加上字母 `n`，加载时会被破坏成字母 `n`。制表符、垂直制表符、换页符等既非分隔符
也非终结符，因此不转义原样保留。

按类型的表示：
- BOOLEAN: `t` / `f`
- INT, NUMERIC, FLOAT: 字符串原样
- DATE: `YYYY-MM-DD`
- TIMESTAMP: `YYYY-MM-DD HH:MM:SS[.ffffff]`
- TIME: `HH:MM:SS`
- VARBINARY: `\xNN` 序列
- NULL: `\N`

示例：
```
1|Store1|1|16 Elm St|Concord|CA|West|Plan1|Premium|None|1000|2000|2007-03-01|\N|18|12576|39|2284
```

### 4-2. `schema.ddl.sql`

将 Vertica 的 `EXPORT_OBJECTS()` 生成的 DDL 转换为**可重复执行安全（幂等）**的形式后存入。
包含 `CREATE SCHEMA`、`CREATE SEQUENCE`、`CREATE TABLE`、`CREATE PROJECTION` 等。

幂等转换（对已存在的对象再次执行也不报错地跳过）：

| 原文 | 转换 |
|---|---|
| `CREATE SCHEMA` / `TABLE` / `SEQUENCE` / `PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE` / `VIEW` | `CREATE OR REPLACE ...` |

> 仅以行首（`^`）为基准进行替换，因此不会改动存储过程主体内部的文本。
> （约束 `ALTER TABLE ... ADD CONSTRAINT` 不是幂等对象 — 重复执行时可能出现重复错误。）

按表（`-t`）备份时若有名称含该表名的存储过程，文件末尾会以
`-- ===== stored procedures =====` 段落附加匹配存储过程的 `CREATE PROCEDURE` DDL。
不存在或提取失败的存储过程会跳过，并在 `MANIFEST.txt` 中记录原因（转储继续）。
（整个模式的转储中，`EXPORT_OBJECTS(schema)` 已包含存储过程，因此没有此段落。）

### 4-3. `load.sql`

用于将各 `.dat` 重新加载的 `COPY FROM LOCAL` 语句集合。它被包在单个事务中，中途失败时整体回滚。

```sql
\set ON_ERROR_STOP on
BEGIN;

COPY "MY_SCHEMA"."TB_SAMPLE" (col1, col2, ...) FROM LOCAL 'MY_SCHEMA.TB_SAMPLE.dat'
  DELIMITER '|' NULL AS '\N' ENCLOSED BY '' ABORT ON ERROR DIRECT;
...

COMMIT;
```

> 由于带有 `DIRECT`，会绕过 WOS 直接加载到 ROS → 对大数据量最快。

### 4-4. `MANIFEST.txt`

```
v_dump manifest
  host        : 10.0.0.5:5433
  database    : VMart
  scope       : DS.{TB_A,TB_B}          # 多表时用 {..} 表示
  dumped at   : 2026-05-20 10:34:40
  schema_only : False
  data_only   : False
  tables      : 2
  rows total  : 3953181
  procedures  : 2 included              # 包含的存储过程数
  ddl file    : schema.ddl.sql
  load script : load.sql

files:
  DS.TB_A.dat	1200 rows
  DS.TB_B.dat	980 rows

failed:                                  # （仅在有失败时）
  DS.SOME_TABLE	<错误首行>

procedures:                              # （仅在尝试提取存储过程时）
  PROC_TB_SAMPLE_1	included
  PROC_TB_SAMPLE_2	skipped (export 结果为空)
```

---

## 5. 恢复

```bash
# 进入要恢复的末端文件夹（整个模式为 <schema>/all，单张表为 <schema>/<table>）
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql   # 创建表
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql         # 加载数据
```

`load.sql` 中的 `COPY ... FROM LOCAL` 会以 **vsql 客户端的当前目录**为基准查找
`.dat` 文件。因此必须在备份目录中执行。

> 若不是空 DB 而是想累加到已有数据上，可跳过 `schema.ddl.sql` 的执行，
> 只执行 `load.sql` 即可。但如果有过列变更，COPY 语句可能会失败。

- 若要**连同存储过程**一起恢复，执行 `schema.ddl.sql` 即可（与表 DDL 在同一文件中）。
  由于 DDL 是幂等的（`IF NOT EXISTS` / `OR REPLACE`），**即使有已存在的对象也会直接跳过**（重复执行安全）。
- **用 Docker 恢复**时，有将上述两步合为一条命令处理的选项 — `--with-ddl`（先创建结构）、
  `-t`（只加载部分表）。参见**第 11 章**的恢复小节。

---

## 6. 全部 CLI 选项

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
  --schema, -n SCHEMA    要转储的模式名（必填）
  --table, -t TABLE      仅特定表。可用重复（-t A -t B）或逗号（-t A,B）
                         指定多个。省略时为整个模式。

mode:
  --schema-only          仅 DDL（无数据文件）
  --data-only            仅数据文件（无 DDL/load.sql）

procedures:
  --with-procedures      按表（-t）备份时一并提取匹配存储过程 DDL（默认 ON）
  --no-procedures        关闭存储过程 DDL 提取

output:
  --output, -o OUTPUT    输出目录。不存在则创建。（必填）

connection:
  --host HOST
  --port PORT
  --user, -U USER
  --password, -W PASSWORD
  --database, -d DATABASE
  --tlsmode {disable,prefer,require}
  --config CONFIG        yaml 配置文件（默认：v_dump/v_dump.yaml）
```

---

## 7. 行为特性 / 注意事项

### 7-1. 外部表自动跳过

像 `CREATE EXTERNAL TABLE ... AS COPY FROM 's3://...'` 这样实际数据位于 Vertica 之外的
表，`SELECT` 本身会尝试访问外部文件而失败。v_dump 将
`v_catalog.tables.table_definition` 被填充的项视为外部表，
**在 `list_tables_in_schema()` 阶段自动排除**。

→ 外部表的 DDL 仍会原样包含在 `schema.ddl.sql` 中，但不会生成 `.dat`。
恢复后也需要外部数据源仍然存活，数据才能可见。

### 7-2. 单表失败隔离

即使某张表的处理以异常结束，其余表仍会继续进行。失败原因会记录在

- stderr：`[v_dump] WARN skip <schema>.<table>: <reason>`
- `MANIFEST.txt` 的 `failed:` 段落

两处。部分生成的 `.dat` 会自动删除。

### 7-3. 对 UTF-8 损坏字节的宽容处理

由于 `vertica_python` 的 unicode 解码默认为 `strict`，若某些列中混入了损坏的多字节，
fetch 会中断。v_dump 以连接选项 `unicode_error='replace'` 打开，因此
损坏位置会被替换为 `U+FFFD`，可在不丢失行的情况下转储。

> 因为以备份优先，所以默认 lenient。若数据完整性校验更重要，可将 `config.py` 的
> `unicode_error` 改为 `'strict'`（失败的表会在 manifest 中标示）。

### 7-4. 内存

以服务端游标每次 fetch 10,000 行（`data._FETCH_SIZE`）。即使数亿行的表也能在不发生内存
OOM 的情况下流式处理。输出以单行为单位即时 flush 到磁盘。

### 7-5. 序列/视图/存储过程

DDL 提取通过 `EXPORT_OBJECTS('', 'scope')` 进行。**整个模式**的转储时，scope 为模式，
因此 **Vertica 导出的所有对象**（模式、序列、表、投影、存储过程等）会原样进入
`schema.ddl.sql`。

**按表（`-t`）**转储时，scope 收窄为指定的表，所以存储过程会自动被排除。
为此 v_dump 会从 `v_catalog.user_procedures` 读取该模式的存储过程列表，
只挑出**名称含目标表名的**（参见 `2-4`），连同参数签名一起用
`EXPORT_OBJECTS` 提取，并追加到 `schema.ddl.sql` 末尾。
匹配/提取失败的会跳过并在 manifest 中记录原因（会话会回滚，因此整个转储不会被破坏）。

> 由于是部分匹配，共享相同名称片段的存储过程可能会被一并捕获（这是预期行为）。
> 提取结果为空时会在 manifest 中标为 `skipped`，因此可立即察觉。

数据转储（`.dat`）仍然**仅以表**为对象。

### 7-6. 权限

转储执行者需要以下权限。

- 目标模式的 `USAGE`
- 目标表的 `SELECT`
- `EXPORT_OBJECTS` 调用权限（通常所有用户均可）
- `v_catalog.*` 查询权限（默认授予）

恢复执行者另需 `CREATE` 权限和 `COPY ... FROM LOCAL` 使用权限。

---

## 8. 故障排查

### `No module named v_dump`

`python -m v_dump` 要求 `v_dump/` 的**父目录**（即 `except/`）位于 cwd / PYTHONPATH 中。
简单的解决办法是使用 `v_dump.sh` 启动器。

### `dump failed: Cannot expand glob pattern due to error: Access Denied`

当对外部表指向的 S3 等外部文件没有访问权限时发生。
**当前版本会自动排除外部表，**因此本不应发生。若仍然出现，
说明 inspector 的过滤条件不适配当前环境，请查看 manifest 的 `failed:`。

### `unicode_error` 相关的解码错误

默认 `replace` 本应处理，但若仍然报错，可能是该列以 LATIN1 等其他编码存入。
请检查 Vertica 服务端的 `SET SESSION CHARACTERSET` 或列编码。

### vsql 恢复时 `ERROR: COPY: Input record N has different number of columns`

`.dat` 序列化与实际列数不匹配的情况。几乎总是意味着数据本身存在损坏的 escape
（例如：值中混入了单独的 `\`）。v_dump 的 escape 会将输入中的所有 `\` 转换为
`\\`，因此正常情况下不会发生。若曾改动过转换逻辑，
请检查 `escape.py` 的 `_ESCAPES` 映射。

### load.sql 过长导致内存负担

`load.sql` 在 73 个 `COPY` 语句左右时仅为数十 KB 量级。若表数量达数千，则需要分割，
那种情况下按模式分别转储在运维上更安全。

---

## 9. 内部模块一句话概述

| 模块 | 职责 |
|---|---|
| `config.py` | `ConnectionConfig` dataclass + CLI/env/yaml 优先级决定 |
| `connection.py` | `vertica_python.connect` 上下文管理器 |
| `inspector.py` | `v_catalog` 查询：模式/表/列列表、外部表判别 |
| `ddl.py` | `EXPORT_OBJECTS()` 调用封装 |
| `escape.py` | 单值/行 → Vertica COPY 正文字符串转换 |
| `data.py` | 表行流式处理 + COPY 语句构建器 |
| `dumper.py` | 整体流程编排 + manifest/失败处理 |
| `cli.py` | argparse + 退出码 |
| `__main__.py` | `python -m v_dump` 入口 |

---

## 10. 快速参考 — 单页摘要

```bash
# 备份（→ 生成于 ./backup/MY_SCHEMA/all/）
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./backup

# 恢复（cd 到末端文件夹）
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql
```

---

## 11. Docker（隔离网 / 难以安装 Python 的环境）

在目标机器上难以直接安装 Python 时使用的方式。将 **Python + vsql + 依赖**打包进同一镜像，
以容器方式执行转储和恢复。连接信息、备份文件、转储对象这类"变化的值"全部
在运行时通过卷/环境变量注入，因此**镜像只需构建一次**便可重复使用。

```
镜像（不变：python·vsql·代码）  +  运行时挂载（连接信息·备份文件）  =  容器
```

### 11-1. 构建（在联网机器上）

```bash
cd v_dump
./docker/build-image.sh            # 仅构建镜像
./docker/build-image.sh --save     # 构建 + 保存 v_dump-image.tar（用于带入隔离网）
```

- 构建机器已联网，pip 会自行下载适配 base Python 的 wheel
  （规避捆绑 wheel 的版本锁定问题）。
- `vsql` 是 x86_64 二进制，因此镜像也**固定为 amd64**（`Dockerfile` 中的 `--platform=linux/amd64`）。
  即便在 arm64 Mac 上构建，也会产出 amd64 镜像。
- 构建末尾以 `RUN vsql --version` 自检库链接。万一因缺少其他 `.so`
  而失败，只需把该软件包加到 `Dockerfile` 的 apt 行即可。

### 11-2. 带入隔离网 — 假设只有 docker

隔离网无法从 registry pull，因此**将镜像导出为 tar 搬运**。要带的东西仅 3 样：

| 要带的 | 是什么 | 备注 |
|---|---|---|
| `v_dump-image.tar` | 镜像本体 | `--save` 产物。python·vsql·代码·`filter_load.py` 全在内 |
| `docker/v_dump-docker.sh` | 运行封装 | 自动组装挂载/连接信息（即使没有也可用原生 `docker run`，见 11-6） |
| `v_dump.yaml` | 连接信息 | 或者若打算用 env 提供则可省略 |

```bash
# [联网构建机] 打成一个包
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml          # 若打算填好连接信息
tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

# ── 将 v_dump-bundle.tar.gz 带入隔离网（USB/内网等）──

# [隔离网，仅安装了 docker]
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar             # 注册镜像
docker images | grep v_dump                 # 确认 v_dump:latest
vi v_dump.yaml                              # 填写连接信息（或用 env）
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 在有 `podman` 时选 podman，没有则自动选 `docker`。
> 即使在只有 docker 的环境中也能原样工作。

### 11-3. 转储（容器）

备份文件夹会生成在 **`v_dump-docker.sh` 所在路径的 `backup/`** 中，并挂载到容器的 `/backup`
（无论从哪里执行，总是生成在脚本旁边）。**无需提供 `-o`** —
封装会始终将输出基础路径设为 `/backup`，并在其下按 `<schema>/<table>`（整体为 `<schema>/all`）结构生成。

```bash
# 连接信息自动探测 v_dump.yaml 或用 env。省略 -o。
./v_dump-docker.sh dump --schema MY_SCHEMA        # → backup/MY_SCHEMA/all/

# 多张表 + 存储过程（DS/DM 自动）— 每张表一个文件夹
./v_dump-docker.sh dump --schema DS -t TB_A,TB_B  # → backup/DS/TB_A/ , backup/DS/TB_B/
```

→ 结果会落在脚本旁的 `backup/<schema>/...`。
执行时该路径会以 `[run] backup dir: ...` 输出。若想放到别处，只需用 `BACKUP_DIR=/路径` 覆盖即可。

### 11-4. 恢复（容器）

恢复路径以 **相对 backup 的相对路径**（`<schema>/<table>` 或 `<schema>/all`）填写即可。
由于 `COPY ... FROM LOCAL` 是客户端相对路径，封装会自动进入相应文件夹。

```bash
# 恢复整个模式（指向末端文件夹）
./v_dump-docker.sh restore MY_SCHEMA/all

# 恢复单张表文件夹
./v_dump-docker.sh restore DS/TB_A

# 先创建结构再加载 — 在空目标上先执行表+存储过程 DDL
./v_dump-docker.sh restore DS/TB_A --with-ddl

# 从 all 文件夹中只挑选部分表加载
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_A,TB_B

# 任意检查：vsql 原始执行
./v_dump-docker.sh vsql -c "SELECT version()"
```

- `--with-ddl` 会在数据加载**之前**执行 `schema.ddl.sql`（表+存储过程）。
  由于 DDL 是幂等的（`IF NOT EXISTS` / `OR REPLACE`），**对已存在的对象会直接跳过**。
  若结构创建确实失败了，紧接着的 COPY 会明确失败并予以告知。
- 若同时给出 `-t` 与 `--with-ddl`，则**结构用完整的** `schema.ddl.sql` 创建，**数据只放入指定表**。

### 11-5. 注入连接信息（优先级：env > yaml）

| 方式 | 用法 | 备注 |
|---|---|---|
| yaml（推荐） | 填好一次 `v_dump.yaml` 即可自动探测 | 无需每次传递。因是明文密码，不烧进镜像 |
| 环境变量 | 在该次执行前加 `VERTICA_*=...` | 一次性连接到其他 DB 时。优先于 yaml |

```bash
# 平时：直接用 yaml → 什么都不传
./v_dump-docker.sh dump --schema DS -t TB_A

# 仅恢复到其他服务器：仅对该次执行用 env 覆盖
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh restore DS/TB_A --with-ddl
```

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`
中，只有已设置的会传递给容器。若要直接指定 yaml 路径，用 `V_DUMP_YAML=/path/v_dump.yaml`。

### 11-6. 不用封装的原生 `docker run`

`v_dump-docker.sh` 展开的命令归根结底只有这些（也可直接使用）：

```bash
docker run --rm \
  -v "$PWD/backup:/backup" \
  -v "$PWD/v_dump.yaml:/app/v_dump/v_dump.yaml:ro" \
  v_dump:latest \
  dump --schema DS -t TB_A,TB_B -o /backup       # → /backup/DS/TB_A , /backup/DS/TB_B

# 用 env 提供连接信息（恢复指向末端文件夹）：
docker run --rm -v "$PWD/backup:/backup" \
  -e VERTICA_HOST=10.0.0.5 -e VERTICA_USER=dbadmin \
  -e VERTICA_PASSWORD=*** -e VERTICA_DATABASE=VMart \
  v_dump:latest restore /backup/DS/TB_A --with-ddl
```

容器入口点命令：`dump` / `restore` / `vsql` / `shell` / `help`。
用 `docker run --rm v_dump:latest help` 输出帮助。

### 11-7. 何时需要重新打镜像

连接信息、备份文件、转储对象全部是运行时注入，**与镜像无关**。需要重新构建的情况
仅在**镜像内容发生变化时**：

- 修改 `v_dump` 代码（`*.py`）
- 变更 `requirements.txt` 依赖
- 更换 vsql（vertica-client）版本

仅在此时重新运行 11-1，带入新的 `v_dump-image.tar`。
