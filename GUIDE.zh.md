# v_dump 使用指南（Docker / 隔离网）

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · [🇯🇵 日本語](GUIDE.ja.md) · **🇨🇳 中文**

将 Vertica 的 schema、表、存储过程备份为文件，并用 `vsql` 重新加载（恢复）的工具
**v_dump**，以 **Docker 容器**方式运营的实战指南。

> 本文档假定的是难以在隔离网（无法联网）服务器上直接安装 Python 的环境。
> 把 Python、vsql、依赖打进一个镜像，以容器方式执行备份/恢复。
> 参考性的细节看 `README.md`，运营流程看本文档。

---

## 目录

1. [概述 — 是什么、怎么做](#1-概述)
2. [准备工作](#2-准备工作)
3. [镜像构建 & 隔离网导入](#3-镜像构建--隔离网导入)
4. [初次配置 — 连接信息](#4-初次配置--连接信息)
5. [备份（转储）](#5-备份转储)
6. [恢复](#6-恢复)
7. [运营场景（实用配方）](#7-运营场景实用配方)
8. [命令参考](#8-命令参考)
9. [故障排查](#9-故障排查)
10. [注意事项与限制](#10-注意事项与限制)

---

## 1. 概述

### 1-1. v_dump 做的事

| 阶段 | 内容 | 产物/工具 |
|---|---|---|
| **备份（dump）** | 将表数据提取为 `COPY` 兼容的 `.dat` 文件，将结构提取为 `schema.ddl.sql` | `vertica-python`（纯 Python 驱动） |
| **恢复（restore）** | 用 `COPY ... FROM LOCAL` 将 `.dat` 重新加载 | `vsql` |

不像 `pg_dump` 那样把一大块 SQL 打成一团，而是把**数据（.dat）**与**结构（DDL）**分开落盘。
`.dat` 沿用 Vertica `COPY` 的默认约定（管道符分隔、`\N` 表示 NULL、反斜杠转义）原样保存，因此能以最快速度重新加载。

### 1-2. 一眼看懂工作原理

```
┌─────────────────────────┐        备份          ┌──────────────────────┐
│  v_dump 容器             │  vertica-python →    │  Vertica (生产 DB)    │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup (挂载)          │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │   恢复   vsql COPY    │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ 主机的 ./backup 目录挂载到容器的 /backup
```

核心原则：**不变的东西（代码、python、vsql）放进镜像，变化的东西（连接信息、备份文件）在运行时挂载/注入。**
→ 镜像只需构建一次即可反复使用。

### 1-3. 两种执行方式

- **Docker（本文档）** — 用 `v_dump-docker.sh` 包装脚本运行容器。适用于隔离网/难以安装 Python 的环境。
- **原生** — 在主机上装好 Python 后执行 `run.sh`。参考 `README.md` 第 2~6 章。

本指南只讲 **Docker 方式**。

---

## 2. 准备工作

### 构建机（可联网）
- Docker（或 podman）
- 网络连接（pull base 镜像 + 下载 pip 依赖）
- 架构：**x86_64(amd64)** — 由于 vsql 二进制是 amd64，镜像也固定为 amd64。

### 隔离网主机（不可联网）
- Docker（或 podman） — **除此之外 Python/vsql 等什么都不需要**
- 到目标 Vertica 的网络可达性（例如 `5433` 端口）

### 资料
- `v_dump/` 目录（源码 + `Dockerfile` + `docker/`）
- `vertica-client-*.tar.gz`（vsql 客户端，随附在 `v_dump/` 内）

---

## 3. 镜像构建 & 隔离网导入

隔离网无法从镜像仓库 pull，所以**把镜像打成 tar 搬运。**

### 3-1. 构建（联网构建机）

```bash
cd v_dump
./docker/build-image.sh            # 仅构建
./docker/build-image.sh --save     # 构建 + 保存 v_dump-image.tar（用于导入）
```

- 构建机联网，pip 才能下载到合适的 wheel。
- 最后用 `RUN vsql --version` 对 vsql 链接做自检。这里若失败，就把缺失的系统
  库添加到 `Dockerfile` 的 apt 行里。
- **源码每次构建都会重新反映**（已应用 cache-bust）。改完代码重新构建即可。

### 3-2. 制作导入包

带进隔离网的只有 3 样：

| 文件 | 作用 | 必需 |
|---|---|---|
| `v_dump-image.tar` | 镜像本体（python、vsql、代码全部包含） | ✅ |
| `docker/v_dump-docker.sh` | 执行包装脚本（自动处理挂载/连接信息/`-o`） | ✅ |
| `v_dump.yaml` | 连接信息（若用 env 提供则可省略） | △ |

```bash
# [构建机]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 若要填写连接信息

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 导入 & 注册（隔离网主机）

把 `v_dump-bundle.tar.gz` 通过 USB/内网等搬运过去后：

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # 注册镜像
docker images | grep v_dump             # 确认 v_dump:latest
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 在有 `podman` 时用 podman，没有则自动选择 `docker`。

---

## 4. 初次配置 — 连接信息

提供连接信息有两种方法，**二选一**即可。

### 4-1.（推荐）yaml 文件

填好 `v_dump.yaml` 后，包装脚本会自动找到并挂载进容器。填一次后每次都不用再给。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ 密码是明文，因此不烧进镜像（用 `.dockerignore` 排除）。仅在运行时挂载。
> 自动探测顺序：`执行位置/v_dump.yaml` → `执行位置/backup/v_dump.yaml` → 脚本的父目录。

### 4-2. 环境变量（一次性 / 不同的 DB）

只在该次执行前加上，就会优先于 yaml。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

可用变量：`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 连接确认

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# 出现类似 Vertica Analytic Database v24.1.0-0 就 OK
```

---

## 5. 备份（转储）

### 5-1. 先理解输出结构

备份目录生成在 **`v_dump-docker.sh` 所在路径下的 `backup/`**（无论从哪里执行，都在脚本旁边，不需要 `-o`）。
其下按 **`<schema>/<表>`** 落盘，整个 schema 则按 **`<schema>/all`** 结构落盘。

```
backup/                              # v_dump-docker.sh 旁边（docker/backup）
└── MY_SCHEMA/
    ├── all/                         # 整个 schema 的转储（不带 -t）
    │   ├── MANIFEST.txt             # 元信息（行数、失败/存储过程列表）
    │   ├── schema.ddl.sql           # 结构 DDL（幂等形式）
    │   ├── load.sql                 # 重新加载的 COPY 语句
    │   └── MY_SCHEMA.<table>.dat    # 各表的数据
    ├── TB_SAMPLE/                # 用 -t 指定的表（每个表一个目录）
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # 该表 + 匹配的存储过程 DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

每个末端目录（`all`、`TB_xxx`）**本身就是一个可独立恢复的自完备单元**。

### 5-2. 整个 schema 备份

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (schema 的所有普通表 + 结构 + 存储过程)
```

执行后会输出如下：
```
[run] backup dir: /当前路径/backup  →  容器 /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 特定表备份（1 个 / 多个）

```bash
# 1 个
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 多个 — 重复（-t A -t B）或逗号（-t A,B,C），可混用
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → 每个表一个目录：backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 即便给多个表，也**只开 1 个**连接顺序处理。某个表失败了，其余的也会继续。

### 5-4. 连同存储过程一起备份

如果是表级（`-t`）备份，会查找**同一 schema 中名称包含该表名的存储过程**，
把其 DDL 一并放到 `schema.ddl.sql` 末尾（默认 ON）。这是不假设命名规则的简单部分匹配。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql 末尾：
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 要关闭用 `--no-procedures`。
- 哪些存储过程被包含/跳过，记录在 `MANIFEST.txt` 的 `procedures:` 部分。
- （整个 schema 的转储已经包含存储过程，因此不需要这个附加动作。）

### 5-5. 模式 — 仅结构 / 仅数据

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # 仅 DDL (无 .dat)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # 仅数据 (无 DDL/load.sql)
```

### 5-6. 确认结果

```bash
ls -R backup/MY_SCHEMA/TB_SAMPLE/
cat  backup/MY_SCHEMA/TB_SAMPLE/MANIFEST.txt
```

`MANIFEST.txt` 示例：
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

> 产物文件以**主机用户所有**落盘（不是容器 root）。在主机上可以直接删除和移动。

### 5-7. 速度·容量 — 自适应并行 & 压缩

**自适应并行（自动）。** 转储会看工作负载自行并行化 —— 量小就顺序执行，
表多就按表级并行，超大表则按行分片并行。无需操心。
worker 数默认为 `min(核数, 4)`。调整用环境变量：

```bash
V_DUMP_JOBS=8 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 8 个 worker
V_DUMP_JOBS=1 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 强制顺序
```

**压缩（`--compress`）。** 把 `.dat` 以 gzip（`.dat.gz`）保存。在气隙迁移中**大幅减小用
USB 搬运的容量**（视数据而定，5~10×）。恢复时 `load.sql` 会自动嵌入 `GZIP` 过滤器，
**Vertica COPY 直接读取压缩文件**，因此无需另行解压，且**无损**。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --compress      # → MY_SCHEMA.<table>.dat.gz
```

> 迁移流水线（① 转储 → ② 传输 → ③ 加载）的每个阶段都会变快：
> ① 转储并行 · ② `--compress` 减小传输负担 · ③ 恢复也并行（见下方第 6 章）。

---

## 6. 恢复

### 6-1. 理解恢复模型

默认恢复是**数据加载（`COPY`）**。也就是说**目标表必须预先存在**。
若没有结构，就用 `--with-ddl` 先建结构再加载。

恢复路径写成**相对于 backup 的相对路径**（末端目录）。

> **恢复也并行（自动）。** 表有多个时，用多个会话同时执行 COPY 加载
> （用 `V_DUMP_JOBS` 调整，`=1` 则顺序）。压缩备份（`.dat.gz`）也照样恢复 —— `load.sql`
> 里有 `GZIP` 过滤器，Vertica 会自动解压。
> 但并行是**按表提交**（`=1` 顺序则为单一事务）。若需要全量原子性，用 `V_DUMP_JOBS=1`。

### 6-2. 整体/单个目录恢复（数据）

```bash
# 恢复整个 schema 的备份
./v_dump-docker.sh restore MY_SCHEMA/all

# 恢复单个表的目录
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 选择性恢复（仅恢复目录中的部分表）

从像 `all` 这种含多个表的目录里只加载一部分。只挑出指定表的 `COPY` 语句执行，
若给了目录中不存在的表，会在**执行前失败**以防事故。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 先建结构再加载 — `--with-ddl`

恢复到空目标（表还不存在的 DB）时。先执行 `schema.ddl.sql`（表+存储过程）再加载数据。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- 由于 DDL 是**幂等**的（见下方 6-5），即使已有对象也会直接跳过。
- 与 `-t` 一起给时，**结构用完整的** `schema.ddl.sql` 建立，**数据只放指定表**。

### 6-5. 幂等性（重复执行安全）

`schema.ddl.sql` 里的 DDL 已被转换为再次执行也不会报错的形式。

| 原始 | 保存的形式 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 同一 DDL 跑两次也不会出 "already exists" 错误，而是以 `nothing was done` 跳过。
（但 `ALTER TABLE ... ADD CONSTRAINT` 这类约束不在幂等范围内，重复执行时可能重复。）

### 6-6. 恢复到其他服务器

若恢复目标与备份原始库不同，只在该次执行覆盖连接信息。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 运营场景（实用配方）

### 7-1. 生产 → 开发 DB 迁移若干特定表

```bash
# 1) 在生产备份（yaml = 生产连接）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 向开发 DB 恢复结构+数据（用 env 覆盖为开发连接）
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. 整个 schema 备份归档

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. 连同存储过程一起备份/恢复

```bash
# 备份：-t 备份会自动包含存储过程（默认）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 恢复：--with-ddl 会一并生成表+存储过程的 DDL 后再加载数据
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 临时巡检查询

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # 仅手动执行 DDL
```

---

## 8. 命令参考

### 8-1. 包装脚本（`v_dump-docker.sh`）

```
./v_dump-docker.sh <命令> [参数...]

命令:
  dump    <v_dump 参数>   备份。-o 自动（/backup）。结果在 ./backup/<schema>/<table|all>/
  restore <目录> [选项]   恢复。目录 = 相对于 backup 的相对路径（末端目录）
  vsql    <vsql 参数>     容器 vsql 原始执行（巡检/手动 SQL）
  help                    帮助
```

### 8-2. dump 参数

| 参数 | 说明 |
|---|---|
| `--schema, -n <S>` | 目标 schema（必需） |
| `--table, -t <T>` | 特定表。用重复/逗号给多个。省略则整个 schema（`all`） |
| `--schema-only` | 仅 DDL |
| `--data-only` | 仅数据 |
| `--with-procedures` / `--no-procedures` | 连同存储过程提取 ON/OFF（默认 ON） |
| `--compress` | 把 `.dat` 转为 gzip（`.dat.gz`）→ 传输/保管容量↓（恢复自动·无损） |

> `-o` 由包装脚本自动指定为 `/backup`，所以不需要给。
> 并行是自动的（基于工作负载）。用 `V_DUMP_JOBS` 调整（8-5）。

### 8-3. restore 选项

| 选项 | 说明 |
|---|---|
| `-t, --table <T>` | 只加载目录中指定表的 COPY（重复/逗号） |
| `--with-ddl` | 在数据加载前先执行 `schema.ddl.sql`（结构+存储过程） |

### 8-4. 连接信息（优先级：env > yaml）

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. 其他环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `V_DUMP_JOBS` | `auto` | 并行 worker 数。`auto`=min(核数,4)，整数=固定，`1`=顺序。**转储·恢复通用** |
| `V_DUMP_PROGRESS` | `auto` | 强制进度条 on(`1`)/off(`0`)。默认仅在终端时显示 |
| `ENGINE` | （自动） | 强制容器引擎（`docker`\|`podman`）。未指定时自动选用持有镜像的引擎 |
| `BACKUP_DIR` | 脚本旁的 `backup` | 强制指定备份目录位置 |
| `IMAGE` | `v_dump:latest` | 要使用的镜像标签 |
| `V_DUMP_YAML` | （自动探测） | 强制指定 yaml 路径 |

---

## 9. 故障排查

### 构建/镜像

**Q. 改了源码重新构建，可还是旧行为。**
→ 以前曾出现过 buildkit 缓存抓不到源码的事故。现在已应用 cache-bust，
只跑 `./docker/build-image.sh` 源码就总能反映。若仍有疑虑：
`docker run --rm --entrypoint grep v_dump:latest -c "<改动的部分代码>" /app/v_dump/dumper.py`
直接确认镜像里的代码，或用 `docker build --no-cache ...` 强制重建。

**Q. 在 `RUN vsql --version` 处构建失败。**
→ 是 vsql 链接的系统库缺失了。在 `Dockerfile` 的 apt 安装行里添加对应的 `.so`
包（默认：`libssl3`、`libreadline8`）。

### 连接

**Q. 容器里连不上 Vertica。**
→ 用 `./v_dump-docker.sh vsql -c "SELECT 1;"` 做隔离巡检。若是 host-only/私有网，确认容器的
默认网桥能否路由（必要时把 `--network host` 加到包装脚本里）。

### 备份

**Q. 存储过程没跟过来。**
→ ① 必须是表级（`-t`）备份（整个 schema 的 EXPORT 已包含）。② 存储过程名里必须确实
包含目标表名。③ 若在 `MANIFEST.txt` 的 `procedures:` 里标为 `skipped`，看其缘由
（无参存储过程可能因无法单独提取而被跳过）。

**Q. 文本里有换行/制表符的数据会不会损坏？**
→ 不会损坏。转义遵循 Vertica COPY 规约（反斜杠+原始字节），
换行、制表符、`|`、`\` 都能无损往返。

### 恢复

**Q. 恢复时 `COPY: Input record has different number of columns`。**
→ 是 `.dat` 的列数与目标表结构不一致。用同一备份的 `schema.ddl.sql`
对齐结构（`--with-ddl`），或确认目标表定义。

**Q. `--with-ddl` 执行中看到红色错误。**
→ 因为幂等，"already exists" 会以 `nothing was done` 跳过。不过由于关掉了 `ON_ERROR_STOP`，
约束重复等部分错误只会打印消息后继续。若结构真的没建成，后续的数据加载（COPY）会明确失败并告知。

---

## 10. 注意事项与限制

- **生产 DB 注意**：`restore` / `--with-ddl` 会对目标 DB 产生**写入**。向生产表
  恢复会导致数据累积或存储过程被替换。务必确认目标连接信息。
- **外部表**的数据在 Vertica 之外，会被自动排除（包含 DDL，无 `.dat`）。
- **无参存储过程**因无法单独提取 DDL 而可能被跳过（记录在 manifest）。
- **约束（ALTER ADD CONSTRAINT）** 不在幂等范围内，重复执行时可能重复。
- vsql 是 **amd64** 二进制。镜像/主机须为 x86_64。
- 密码是明文 yaml，请注意文件权限与 git 排除。

---

## 附录 A. 快速开始清单

```
[构建机]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  (填入连接信息)
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[隔离网主机]
□ tar xzf v_dump-bundle.tar.gz
□ docker load -i v_dump-image.tar
□ chmod +x v_dump-docker.sh
□ ./v_dump-docker.sh vsql -c "SELECT version();"   (连接确认)

[备份]
□ ./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
□ ls backup/MY_SCHEMA/TB_SAMPLE/

[恢复]
□ ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

## 附录 B. 常用单行命令

```bash
# 连接确认
./v_dump-docker.sh vsql -c "SELECT version();"

# 整个 schema 备份
./v_dump-docker.sh dump --schema MY_SCHEMA

# 多个表备份（+存储过程）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 单个表恢复（先建结构）
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 恢复到其他 DB
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — **v_dump** 的作者与版权持有人。All rights reserved.
