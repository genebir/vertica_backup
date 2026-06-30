# v_dump 使用指南 (Docker / 封闭网络)

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · [🇯🇵 日本語](GUIDE.ja.md) · **🇨🇳 中文**

将 Vertica 的模式、表、存储过程备份为文件，并用 `vsql` 重新加载(恢复)的工具
**v_dump**，以 **Docker 容器**方式运维的实战指南。

> 本文假设的是难以在封闭网络(无法联网)服务器上直接安装 Python 的环境。
> 将 Python、vsql、依赖项打包进一个镜像，以容器方式执行备份/恢复。
> 参考性的详细内容见 `README.md`，运维流程见本文。

---

## 目录

1. [概述 — 做什么、怎么做](#1-概述)
2. [准备工作](#2-准备工作)
3. [镜像构建 & 引入封闭网络](#3-镜像构建--引入封闭网络)
4. [初始设置 — 连接信息](#4-初始设置--连接信息)
5. [备份(转储)](#5-备份转储)
6. [恢复](#6-恢复)
7. [运维场景(实用方案)](#7-运维场景实用方案)
8. [命令参考](#8-命令参考)
9. [故障排查](#9-故障排查)
10. [注意事项·限制](#10-注意事项限制)

---

## 1. 概述

### 1-1. v_dump 做的事

| 阶段 | 内容 | 产出/工具 |
|---|---|---|
| **备份(dump)** | 将表数据抽取为 `COPY` 兼容的 `.dat` 文件，将结构抽取为 `schema.ddl.sql` | `vertica-python`(纯 Python 驱动) |
| **恢复(restore)** | 用 `COPY ... FROM LOCAL` 将 `.dat` 重新加载 | `vsql` |

它不像 `pg_dump` 那样是一整块 SQL，而是把 **数据(.dat)** 和 **结构(DDL)** 分离落盘。
`.dat` 沿用 Vertica `COPY` 的默认约定(竖线分隔、`\N` 表示 NULL、反斜杠转义)，因此重新加载最快。

### 1-2. 工作原理一览

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
        │ 主机的 ./backup 目录被挂载到容器的 /backup
```

核心原则: **不变的东西(代码·python·vsql)放进镜像，变化的东西(连接信息·备份文件)在运行时挂载/注入。**
→ 镜像只需构建一次即可复用。

### 1-3. 两种执行方式

- **Docker (本文)** — 用 `v_dump-docker.sh` 包装脚本执行容器。适用于封闭网络/难以安装 Python 的环境。
- **原生** — 在主机上安装 Python 并执行 `run.sh`。参见 `README.md` 第 2~6 章。

本指南仅讲解 **Docker 方式**。

---

## 2. 准备工作

### 构建机 (有网)
- Docker (或 podman)
- 互联网连接 (拉取 base 镜像 + 下载 pip 依赖)
- 架构: **x86_64(amd64)** — vsql 二进制为 amd64，因此镜像也固定为 amd64。

### 封闭网络主机 (无网)
- Docker (或 podman) — **除此之外 Python/vsql 等什么都不需要**
- 对目标 Vertica 的网络可达性 (例如 `5433` 端口)

### 资料
- `v_dump/` 目录 (源码 + `Dockerfile` + `docker/`)
- `vertica-client-*.tar.gz` (vsql 客户端，随附于 `v_dump/` 内)

---

## 3. 镜像构建 & 引入封闭网络

封闭网络无法从镜像仓库 pull，因此 **把镜像导出为 tar 再搬运。**

### 3-1. 构建 (有网的构建机)

```bash
cd v_dump
./docker/build-image.sh            # 仅构建
./docker/build-image.sh --save     # 构建 + 保存 v_dump-image.tar(供引入用)
```

- 构建机已联网，pip 会下载合适的 wheel。
- 最后通过 `RUN vsql --version` 自检 vsql 链接。若此处失败，把缺失的系统
  库追加到 `Dockerfile` 的 apt 行中。
- **源码每次构建都会重新反映**(已应用 cache-bust)。改完代码重新构建即可。

### 3-2. 制作引入包

要带进封闭网络的只有 3 样:

| 文件 | 作用 | 必需 |
|---|---|---|
| `v_dump-image.tar` | 镜像本体 (python·vsql·代码全部包含) | ✅ |
| `docker/v_dump-docker.sh` | 执行包装脚本 (挂载/连接信息/`-o` 自动) | ✅ |
| `v_dump.yaml` | 连接信息 (若用 env 提供则可省略) | △ |

```bash
# [构建机]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 如果要填写连接信息

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 引入 & 注册 (封闭网络主机)

把 `v_dump-bundle.tar.gz` 通过 USB/内网等搬运过去后:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # 注册镜像
docker images | grep v_dump             # 确认 v_dump:latest
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 在有 `podman` 时选用 podman，没有则自动选用 `docker`。

---

## 4. 初始设置 — 连接信息

提供连接信息有两种方法，**二选一**即可。

### 4-1. (推荐) yaml 文件

填好 `v_dump.yaml`，包装脚本会自动找到并挂载到容器。填一次后每次都无需再提供。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ 密码是明文，所以不烤进镜像(通过 `.dockerignore` 排除)。仅在运行时挂载。
> 自动查找顺序: `执行位置/v_dump.yaml` → `执行位置/backup/v_dump.yaml` → 脚本父目录。

**多节点集群。** 在 `host` 中**用逗号写多个**节点时:
- 前一个节点宕机会**自动 failover** 到下一个节点 (每个节点连接超时 8 秒)
- 并行转储 worker 与恢复 COPY 会话会**分散到各节点**，从而化解单节点 initiator 瓶颈。

```yaml
vertica:
  host: node1,node2,node3      # 把最近、最快的节点放在前面
  port: 5433                   # 所有节点通用端口
  ...
```

> 只写一个节点时，与以往行为完全相同(向后兼容)。

### 4-2. 环境变量 (一次性·其他 DB)

只加在那一次执行前面，优先级高于 yaml。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

可用变量: `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 连接确认

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# 出现类似 Vertica Analytic Database v24.1.0-0 就 OK
```

---

## 5. 备份(转储)

### 5-1. 先理解输出结构

备份目录会生成在 **`v_dump-docker.sh` 所在路径的 `backup/`** 下(无论从哪里执行，都在脚本旁边，无需 `-o`)。
其下按 **`<模式>/<表>`** 落盘，整个模式则为 **`<模式>/all`** 结构。

```
backup/                              # v_dump-docker.sh 旁边 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # 整个模式的转储(不带 -t)
    │   ├── MANIFEST.txt             # 元信息(行数、失败/存储过程清单)
    │   ├── schema.ddl.sql           # 结构 DDL (幂等形式)
    │   ├── load.sql                 # 重新加载的 COPY 语句
    │   └── MY_SCHEMA.<table>.dat    # 按表的数据
    ├── TB_SAMPLE/                # 用 -t 指定的表(每张表一个目录)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # 该表 + 匹配的存储过程 DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

每个末端目录(`all`、`TB_xxx`)都是 **本身即可恢复的自完备单元**。

### 5-2. 整个模式备份

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (该模式的所有普通表 + 结构 + 存储过程)
```

执行后会输出如下:
```
[run] backup dir: /当前路径/backup  →  容器 /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 指定表备份 (1 张 / 多张)

```bash
# 1 张
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 多张 — 重复(-t A -t B) 或逗号(-t A,B,C)，可混用
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → 每张表一个目录: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 即使指定多张表，也只开 **1 个**连接顺序处理。某张表失败，其余仍继续。

### 5-4. 连同存储过程备份

若为表级(`-t`)备份，会在**同一模式中查找名称包含该表名的存储过程**，
把其 DDL 一并追加到 `schema.ddl.sql` 末尾(默认 ON)。这是不假设命名规则的简单部分匹配。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql 末尾:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 要关闭则用 `--no-procedures`。
- 哪些存储过程被包含/跳过，会记录在 `MANIFEST.txt` 的 `procedures:` 区段。
- (整个模式转储已包含存储过程，因此无需此附加动作。)

### 5-5. 模式 — 仅结构 / 仅数据

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # 仅 DDL (无 .dat)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # 仅数据 (无 DDL/load.sql)
```

### 5-6. 查看结果

```bash
ls -R backup/MY_SCHEMA/TB_SAMPLE/
cat  backup/MY_SCHEMA/TB_SAMPLE/MANIFEST.txt
```

`MANIFEST.txt` 示例:
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

> 产出文件以 **主机用户所有**落盘(非容器 root)。在主机上可直接删除、移动。

### 5-7. 速度·容量 — 自适应并行 & 压缩

**自适应并行(自动)。** 转储会观察工作负载自动并行化 — 量小则顺序，
表多则按表并行，超大表则按行分片并行。无需操心。
worker 数默认为 `min(核心数, 4)`。通过环境变量调整:

```bash
V_DUMP_JOBS=8 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 8 个 worker
V_DUMP_JOBS=1 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 强制顺序
```

**多节点更快。** 在 `host` 中用逗号列出多个节点时(见 4-1)，并行 worker/COPY 会话
会分散到各节点，化解数据集中到单一节点的瓶颈。3 节点则吞吐量相应提升。

**压缩(`--compress`)。** 把 `.dat` 保存为 gzip(`.dat.gz`)。在物理隔离迁移中能**大幅减小
用 USB 搬运的容量**(视数据而定 5~10×)。恢复时 `load.sql` 会自动带上 `GZIP` 过滤器，
**Vertica COPY 直接读取压缩文件**，因此无需另行解压且**无损**。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --compress      # → MY_SCHEMA.<table>.dat.gz
```

> 迁移流水线(① 转储 → ② 传输 → ③ 加载)的各阶段都会加速:
> ① 转储并行 · ② `--compress` 减轻传输负担↓ · ③ 恢复也并行(见第 6 章)。

---

## 6. 恢复

### 6-1. 理解恢复模型

默认恢复是**数据加载(`COPY`)**。也就是说**目标表必须事先存在**。
若没有结构，用 `--with-ddl` 先建结构再加载。

恢复路径以 **相对于 backup 的相对路径**(末端目录)填写。

> **恢复也并行(自动)。** 表有多张时，会用多个会话同时执行 COPY 加载
> (用 `V_DUMP_JOBS` 调整，`=1` 则顺序)。压缩备份(`.dat.gz`)也能原样恢复 —
> `load.sql` 中有 `GZIP` 过滤器，Vertica 会自动解压。
> 但并行是**按表提交**(`=1` 顺序则为单一事务)。若需要全量原子性，请用 `V_DUMP_JOBS=1`。

### 6-2. 整体/单个目录恢复 (数据)

```bash
# 恢复整个模式的备份
./v_dump-docker.sh restore MY_SCHEMA/all

# 恢复单张表目录
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 选择性恢复 (目录中仅部分表)

从像 `all` 这样含多张表的目录中只加载一部分。仅挑出指定表的 `COPY` 语句执行，
若指定了目录中不存在的表，会**在执行前失败**以防止事故。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 先建结构再加载 — `--with-ddl`

向空目标(尚无表的 DB)恢复时。先执行 `schema.ddl.sql`(表+存储过程)，再加载数据。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- DDL 是**幂等**的(见下文 6-5)，即使已有对象也会直接跳过。
- 与 `-t` 同时给出时，**结构用完整** `schema.ddl.sql` 创建，**数据只放指定的表**。

### 6-5. 幂等性 (可安全重跑)

`schema.ddl.sql` 中的 DDL 已被转换为可再次执行而不报错。

| 原始 | 保存的形式 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 同一 DDL 跑两次也不会出现 "already exists" 错误，而是以 `nothing was done` 跳过。
(但 `ALTER TABLE ... ADD CONSTRAINT` 这类约束不在幂等范围内，重跑时可能重复。)

### 6-6. 恢复到其他服务器

若恢复目标与备份源是不同的 DB，仅对那一次执行覆盖连接信息。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 运维场景(实用方案)

### 7-1. 从生产 → 开发 DB 迁移几张特定表

```bash
# 1) 在生产备份 (yaml = 生产连接)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 向开发 DB 恢复结构+数据 (用 env 覆盖为开发连接)
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. 整个模式备份归档

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. 连同存储过程一起备份/恢复

```bash
# 备份: -t 备份时自动包含存储过程(默认)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 恢复: --with-ddl 会一并创建表+存储过程 DDL 后再加载数据
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 临时检查查询

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # 仅手动执行 DDL
```

---

## 8. 命令参考

### 8-1. 包装脚本 (`v_dump-docker.sh`)

```
./v_dump-docker.sh <命令> [参数...]

命令:
  dump    <v_dump 参数>   备份。-o 自动(/backup)。结果在 ./backup/<schema>/<table|all>/
  restore <目录> [选项]   恢复。目录 = 相对于 backup 的相对路径(末端目录)
  vsql    <vsql 参数>     容器内 vsql 原始执行(检查/手动 SQL)
  help                    帮助
```

### 8-2. dump 参数

| 参数 | 说明 |
|---|---|
| `--schema, -n <S>` | 目标模式 (必需) |
| `--table, -t <T>` | 指定表。重复/逗号可多张。省略则整个模式(`all`) |
| `--schema-only` | 仅 DDL |
| `--data-only` | 仅数据 |
| `--with-procedures` / `--no-procedures` | 连同存储过程抽取 ON/OFF (默认 ON) |
| `--compress` | 把 `.dat` 转为 gzip(`.dat.gz`) → 传输/保管容量↓ (恢复自动·无损) |

> `-o` 由包装脚本自动指定为 `/backup`，无需提供。
> 并行为自动(基于工作负载)。用 `V_DUMP_JOBS` 调整(见 8-5)。

### 8-3. restore 选项

| 选项 | 说明 |
|---|---|
| `-t, --table <T>` | 在目录中仅加载指定表的 COPY (重复/逗号) |
| `--with-ddl` | 数据加载前先执行 `schema.ddl.sql`(结构+存储过程) |

### 8-4. 连接信息 (优先级: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. 其他环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `V_DUMP_JOBS` | `auto` | 并行 worker 数。`auto`=min(核心数,4)，整数=固定，`1`=顺序。**转储·恢复通用** |
| `V_DUMP_PROGRESS` | `auto` | 强制进度条 on(`1`)/off(`0`)。默认仅在终端时显示 |
| `ENGINE` | (自动) | 强制容器引擎(`docker`\|`podman`)。未指定时自动选用持有镜像的引擎 |
| `BACKUP_DIR` | 脚本旁的 `backup` | 强制指定备份目录位置 |
| `IMAGE` | `v_dump:latest` | 要使用的镜像标签 |
| `V_DUMP_YAML` | (自动查找) | 强制指定 yaml 路径 |

---

## 9. 故障排查

### 构建/镜像

**Q. 改了源码重新构建，行为却还是老样子。**
→ 以前曾有 buildkit 缓存抓不到源码的问题。现在已应用 cache-bust，
只跑 `./docker/build-image.sh` 源码就总会反映。若仍存疑:
`docker run --rm --entrypoint grep v_dump:latest -c "<改动代码片段>" /app/v_dump/dumper.py`
直接确认镜像内代码，或用 `docker build --no-cache ...` 强制重建。

**Q. `RUN vsql --version` 处构建失败。**
→ vsql 链接的系统库缺失。把对应的 `.so` 包追加到 `Dockerfile` 的 apt 安装行
(默认: `libssl3`、`libreadline8`)。

### 连接

**Q. 容器连不上 Vertica。**
→ 用 `./v_dump-docker.sh vsql -c "SELECT 1;"` 做隔离检查。若是 host-only/私有网，确认
容器默认网桥是否能路由(必要时在包装脚本中加 `--network host`)。

### 备份

**Q. 存储过程没跟着出来。**
→ ① 必须是表级(`-t`)备份(整个模式的 EXPORT 已包含)。② 存储过程名称中
必须确实包含目标表名。③ 若 `MANIFEST.txt` 的 `procedures:` 中标为 `skipped`，看其
原因(无参存储过程无法单独抽取，可能被跳过)。

**Q. 文本中含换行/制表符的数据会损坏吗?**
→ 不会。转义已对齐 Vertica COPY 规约(反斜杠+原始字节)，
换行、制表符、`|`、`\` 都能无损往返。

### 恢复

**Q. 恢复时报 `COPY: Input record has different number of columns`。**
→ `.dat` 的列数与目标表结构不匹配。用同一备份的 `schema.ddl.sql` 对齐
结构(`--with-ddl`)，或确认目标表定义。

**Q. `--with-ddl` 执行中看到红色错误。**
→ 因为幂等，"already exists" 会以 `nothing was done` 跳过。不过由于关闭了
`ON_ERROR_STOP`，约束重复等部分错误只会输出消息并继续。若结构确实没建成，
后续的数据加载(COPY)会明确失败并提示你。

---

## 10. 注意事项·限制

- **生产 DB 注意**: `restore` / `--with-ddl` 会对目标 DB 产生**写入**。若向生产表
  恢复，数据会累加或存储过程被替换。务必确认目标连接信息。
- **外部表**因数据在 Vertica 之外而自动排除(包含 DDL，无 `.dat`)。
- **无参存储过程**无法单独抽取 DDL，可能被跳过(记录在 manifest 中)。
- **约束(ALTER ADD CONSTRAINT)** 不在幂等范围内，重跑时可能重复。
- vsql 是 **amd64** 二进制。镜像/主机必须为 x86_64。
- 密码是明文 yaml，请注意文件权限与 git 排除。

---

## 附录 A. 快速开始检查清单

```
[构建机]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  (输入连接信息)
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[封闭网络主机]
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

## 附录 B. 常用一行命令

```bash
# 连接确认
./v_dump-docker.sh vsql -c "SELECT version();"

# 整个模式备份
./v_dump-docker.sh dump --schema MY_SCHEMA

# 备份多张表 (+存储过程)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 单张表恢复 (从结构开始)
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 恢复到其他 DB
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — **v_dump** 的作者及版权所有者。All rights reserved.
