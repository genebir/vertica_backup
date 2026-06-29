# v_dump 使用指南（Docker / 隔离网）

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · [🇯🇵 日本語](GUIDE.ja.md) · **🇨🇳 中文**

将 Vertica 的模式、表、存储过程备份为文件，并用 `vsql` 重新加载（恢复）的工具
**v_dump**，以 **Docker 容器** 方式运维的实战指南。

> 本文档假设在隔离网（无法联网）服务器上难以直接安装 Python 的环境。
> 将 Python、vsql、依赖打包进一个镜像，以容器方式执行备份/恢复。
> 参考性细节见 `README.md`，运维流程见本文档。

---

## 目录

1. [概述 — 是什么、怎么做](#1-概述)
2. [前置准备](#2-前置准备)
3. [镜像构建 & 隔离网导入](#3-镜像构建--隔离网导入)
4. [初次配置 — 连接信息](#4-初次配置--连接信息)
5. [备份（转储）](#5-备份转储)
6. [恢复](#6-恢复)
7. [运维场景（实用范例）](#7-运维场景实用范例)
8. [命令参考](#8-命令参考)
9. [故障排查](#9-故障排查)
10. [注意事项·限制](#10-注意事项限制)

---

## 1. 概述

### 1-1. v_dump 的作用

| 阶段 | 内容 | 产出/工具 |
|---|---|---|
| **备份（dump）** | 将表数据导出为 `COPY` 兼容的 `.dat` 文件，将结构导出为 `schema.ddl.sql` | `vertica-python`（纯 Python 驱动） |
| **恢复（restore）** | 用 `COPY ... FROM LOCAL` 将 `.dat` 重新加载 | `vsql` |

与 `pg_dump` 那样的单一 SQL 整块不同，它将 **数据（.dat）** 与 **结构（DDL）** 分离输出。
`.dat` 沿用 Vertica `COPY` 的默认约定（管道符分隔、`\N` 表示 NULL、反斜杠转义），因此能以最快速度重新加载。

### 1-2. 工作原理一览

```
┌─────────────────────────┐        备份          ┌──────────────────────┐
│  v_dump 容器            │  vertica-python →    │  Vertica（生产 DB）  │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup（挂载）        │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │   恢复   vsql COPY    │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ 主机的 ./backup 目录挂载到容器的 /backup
```

核心原则：**不变的东西（代码、python、vsql）放进镜像，变化的东西（连接信息、备份文件）在运行时挂载/注入。**
→ 镜像只需构建一次即可复用。

### 1-3. 两种运行方式

- **Docker（本文档）** — 用 `v_dump-docker.sh` 包装脚本运行容器。适用于隔离网/难以安装 Python 的环境。
- **原生** — 在主机上安装 Python 并运行 `run.sh`。参见 `README.md` 第 2~6 章。

本指南仅涉及 **Docker 方式**。

---

## 2. 前置准备

### 构建机（可联网）
- Docker（或 podman）
- 互联网连接（pull base 镜像 + 下载 pip 依赖）
- 架构：**x86_64(amd64)** — vsql 二进制为 amd64，因此镜像也固定为 amd64。

### 隔离网主机（不可联网）
- Docker（或 podman） — **此外无需任何 Python/vsql 等**
- 到目标 Vertica 的网络可达性（例如 `5433` 端口）

### 资料
- `v_dump/` 目录（源码 + `Dockerfile` + `docker/`）
- `vertica-client-*.tar.gz`（vsql 客户端，随附于 `v_dump/` 内）

---

## 3. 镜像构建 & 隔离网导入

隔离网无法从 registry pull，因此 **将镜像导出为 tar 再搬运。**

### 3-1. 构建（联网构建机）

```bash
cd v_dump
./docker/build-image.sh            # 仅构建
./docker/build-image.sh --save     # 构建 + 保存 v_dump-image.tar（用于导入）
```

- 构建机已联网，pip 能获取合适的 wheel。
- 最后通过 `RUN vsql --version` 自检 vsql 的链接。若此处失败，则将缺失的系统
  库添加到 `Dockerfile` 的 apt 行中。
- **每次构建都会始终重新反映源码**（已应用 cache-bust）。改完代码重新构建即可。

### 3-2. 制作导入包

需要带入隔离网的只有 3 样：

| 文件 | 作用 | 必需 |
|---|---|---|
| `v_dump-image.tar` | 镜像本体（包含 python、vsql、代码全部） | ✅ |
| `docker/v_dump-docker.sh` | 运行包装脚本（自动处理挂载/连接信息/`-o`） | ✅ |
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

用 USB/内网等方式搬运 `v_dump-bundle.tar.gz` 后：

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # 注册镜像
docker images | grep v_dump             # 确认 v_dump:latest
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` 在有 `podman` 时选用 podman，否则自动选用 `docker`。

---

## 4. 初次配置 — 连接信息

提供连接信息有两种方法，**二者只需其一** 即可。

### 4-1. （推荐）yaml 文件

填好 `v_dump.yaml` 后，包装脚本会自动找到并挂载到容器。填写一次后，之后每次都无需再提供。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ 由于密码是明文，不会烧录进镜像（通过 `.dockerignore` 排除）。仅在运行时挂载。
> 自动探查顺序：`执行位置/v_dump.yaml` → `执行位置/backup/v_dump.yaml` → 脚本父目录。

### 4-2. 环境变量（一次性·其他 DB）

只在该次执行前加上即可，优先级高于 yaml。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

可用变量：`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 连接确认

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# 出现类似 Vertica Analytic Database v24.1.0-0 即 OK
```

---

## 5. 备份（转储）

### 5-1. 先理解输出结构

备份目录会生成在 **`v_dump-docker.sh` 所在路径下的 `backup/`**（无论从哪里执行都在脚本旁边，无需 `-o`）。
其下按 **`<模式>/<表>`** 结构输出，整个模式则为 **`<模式>/all`** 结构。

```
backup/                              # v_dump-docker.sh 旁边 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # 模式整体转储（不带 -t）
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

每个末端目录（`all`、`TB_xxx`）都是 **本身即可恢复的自完备单元**。

### 5-2. 备份整个模式

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  （模式的所有普通表 + 结构 + 存储过程）
```

执行后会输出如下：
```
[run] backup dir: /当前路径/backup  →  容器 /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 备份特定的表（1 个 / 多个）

```bash
# 1 个
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 多个 — 重复（-t A -t B）或逗号（-t A,B,C），可混用
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → 每个表一个目录：backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 即使指定多个表，也只打开 **1 个** 连接顺序处理。即使某个表失败，其余表仍会继续。

### 5-4. 连同存储过程一起备份

若是按表（`-t`）备份，则会查找 **同一模式中名称包含该表名的存储过程**，并将其
DDL 一并写入 `schema.ddl.sql` 末尾（默认 ON）。这是不假设命名规则的简单部分匹配。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql 末尾：
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 如需关闭，使用 `--no-procedures`。
- 哪些存储过程被包含/跳过会记录在 `MANIFEST.txt` 的 `procedures:` 部分。
- （整个模式转储已包含存储过程，因此无需此附加动作。）

### 5-5. 运行模式 — 仅结构 / 仅数据

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # 仅 DDL（无 .dat）
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # 仅数据（无 DDL/load.sql）
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

> 产出文件归 **主机用户所有**（而非容器 root）。可直接在主机上删除和移动。

---

## 6. 恢复

### 6-1. 理解恢复模型

默认恢复是 **数据加载（`COPY`）**。也就是说 **目标表必须事先存在**。
若没有结构，则用 `--with-ddl` 先创建结构再加载。

恢复路径写成 **相对于 backup 的相对路径**（末端目录）。

### 6-2. 整体/单个目录恢复（数据）

```bash
# 恢复整个模式的备份
./v_dump-docker.sh restore MY_SCHEMA/all

# 恢复单个表目录
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 选择性恢复（仅恢复目录中的部分表）

从像 `all` 这样含多个表的目录中只加载部分。仅挑出指定表的 `COPY` 语句执行；
若指定了目录中不存在的表，则 **在执行前直接失败** 以防止事故。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 先创建结构再加载 — `--with-ddl`

向空目标（尚无表的 DB）恢复时使用。先执行 `schema.ddl.sql`（表+存储过程），再加载数据。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- 由于 DDL 是 **幂等的**（见下方 6-5），即使对象已存在也会直接跳过。
- 与 `-t` 一起使用时，**结构按完整的** `schema.ddl.sql` 创建，而 **数据仅写入指定的表**。

### 6-5. 幂等性（重复执行安全）

`schema.ddl.sql` 中的 DDL 已被转换为重复执行也不会报错的形式。

| 原始 | 保存的形式 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 即使同一 DDL 执行两次，也不会出现 "already exists" 错误，而是以 `nothing was done` 跳过。
（但像 `ALTER TABLE ... ADD CONSTRAINT` 这样的约束不在幂等范围内，重复执行时可能重复。）

### 6-6. 恢复到其他服务器

若恢复目标与备份源是不同的 DB，则仅在该次执行时覆盖连接信息。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 运维场景（实用范例）

### 7-1. 将若干特定表从生产迁移到开发 DB

```bash
# 1) 在生产上备份（yaml = 生产连接）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 恢复结构+数据到开发 DB（用 env 覆盖为开发连接）
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. 整库模式备份留存

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. 连同存储过程一起备份/恢复

```bash
# 备份：按 -t 备份时自动包含存储过程（默认）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 恢复：--with-ddl 一并创建表+存储过程 DDL 后加载数据
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 任意检查查询

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # 仅手动执行 DDL
```

---

## 8. 命令参考

### 8-1. 包装脚本（`v_dump-docker.sh`）

```
./v_dump-docker.sh <命令> [参数...]

命令：
  dump    <v_dump 参数>   备份。-o 自动（/backup）。结果在 ./backup/<schema>/<table|all>/
  restore <目录> [选项]   恢复。目录 = 相对于 backup 的相对路径（末端目录）
  vsql    <vsql 参数>     容器内 vsql 原始执行（检查/手动 SQL）
  help                    帮助
```

### 8-2. dump 参数

| 参数 | 说明 |
|---|---|
| `--schema, -n <S>` | 目标模式（必需） |
| `--table, -t <T>` | 特定表。用重复/逗号指定多个。省略时为整个模式（`all`） |
| `--schema-only` | 仅 DDL |
| `--data-only` | 仅数据 |
| `--with-procedures` / `--no-procedures` | 连同存储过程一起提取 ON/OFF（默认 ON） |

> `-o` 由包装脚本自动指定为 `/backup`，因此无需提供。

### 8-3. restore 选项

| 选项 | 说明 |
|---|---|
| `-t, --table <T>` | 仅加载目录内指定表的 COPY（重复/逗号） |
| `--with-ddl` | 加载数据前先执行 `schema.ddl.sql`（结构+存储过程） |

### 8-4. 连接信息（优先级：env > yaml）

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. 其他环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BACKUP_DIR` | `$PWD/backup` | 强制指定备份目录位置 |
| `IMAGE` | `v_dump:latest` | 要使用的镜像标签 |
| `V_DUMP_YAML` | （自动探查） | 强制指定 yaml 路径 |

---

## 9. 故障排查

### 构建/镜像

**Q. 改了源码重新构建，但行为还是旧的。**
→ 以前曾出现过 buildkit 缓存抓不到源码的问题。现在已应用 cache-bust，只要运行
`./docker/build-image.sh` 源码就总会被反映。若仍有疑虑：
`docker run --rm --entrypoint grep v_dump:latest -c "<改动后的部分代码>" /app/v_dump/dumper.py`
用上述命令直接确认镜像内的代码，或用 `docker build --no-cache ...` 强制重新构建。

**Q. 在 `RUN vsql --version` 处构建失败。**
→ vsql 链接的系统库缺失了。在 `Dockerfile` 的 apt 安装行中添加相应的 `.so`
包（默认：`libssl3`、`libreadline8`）。

### 连接

**Q. 容器无法连接到 Vertica。**
→ 用 `./v_dump-docker.sh vsql -c "SELECT 1;"` 做隔离排查。若是 host-only/私有网络，确认容器的
默认网桥能否路由（必要时在包装脚本中添加 `--network host`）。

### 备份

**Q. 存储过程没有被带上。**
→ ① 必须是按表（`-t`）备份（整个模式的 EXPORT 已包含）。② 存储过程名称中必须确实包含
目标表名。③ 若在 `MANIFEST.txt` 的 `procedures:` 中标记为 `skipped`，则查看
原因（无参数的存储过程无法单独提取，可能被跳过）。

**Q. 文本中含有换行/制表符的数据会损坏吗？**
→ 不会损坏。转义遵循 Vertica COPY 规约（反斜杠+原始字节），因此
换行、制表符、`|`、`\` 都能无损往返。

### 恢复

**Q. 恢复时出现 `COPY: Input record has different number of columns`。**
→ `.dat` 的列数与目标表结构不一致的情况。用同一备份的 `schema.ddl.sql`
对齐结构（`--with-ddl`），或确认目标表定义。

**Q. 执行 `--with-ddl` 时看到红色错误。**
→ 由于幂等，"already exists" 会以 `nothing was done` 跳过。不过由于关闭了 `ON_ERROR_STOP`，
约束重复等部分错误只会输出消息并继续。如果结构确实没有被
创建，后续的数据加载（COPY）会明确失败并告知。

---

## 10. 注意事项·限制

- **注意生产 DB**：`restore` / `--with-ddl` 会对目标 DB 产生 **写入**。恢复到生产表会
  导致数据累积或存储过程被替换。务必确认目标连接信息。
- **外部表** 的数据在 Vertica 之外，会自动排除（包含 DDL，无 `.dat`）。
- **无参数存储过程** 无法单独提取 DDL，可能被跳过（记录在清单中）。
- **约束（ALTER ADD CONSTRAINT）** 不在幂等范围内，重复执行时可能重复。
- vsql 是 **amd64** 二进制。镜像/主机必须是 x86_64。
- 密码是明文 yaml，请注意文件权限和 git 排除。

---

## 附录 A. 快速开始检查清单

```
[构建机]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  （输入连接信息）
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[隔离网主机]
□ tar xzf v_dump-bundle.tar.gz
□ docker load -i v_dump-image.tar
□ chmod +x v_dump-docker.sh
□ ./v_dump-docker.sh vsql -c "SELECT version();"   （连接确认）

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

# 备份整个模式
./v_dump-docker.sh dump --schema MY_SCHEMA

# 备份多个表（+存储过程）
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 恢复单个表（从结构开始）
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 恢复到其他 DB
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. All rights reserved.
