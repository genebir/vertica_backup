# v_dump 利用ガイド (Docker / 閉域網)

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · **🇯🇵 日本語** · [🇨🇳 中文](GUIDE.zh.md)

Vertica のスキーマ・テーブル・プロシージャをファイルにバックアップし、`vsql` で再ロード(復元)するツール
**v_dump** を **Docker コンテナ**で運用する実務ガイド。

> 本ドキュメントは、閉域網(インターネット不可)サーバーに Python を直接インストールしづらい環境を想定する。
> Python・vsql・依存関係を 1 つのイメージにまとめ、コンテナでバックアップ/復元を実行する。
> リファレンス的な詳細は `README.md`、運用フローは本ドキュメントを参照。

---

## 目次

1. [概要 — 何を、どのように](#1-概要)
2. [事前準備物](#2-事前準備物)
3. [イメージビルド & 閉域網への持ち込み](#3-イメージビルド--閉域網への持ち込み)
4. [初回設定 — 接続情報](#4-初回設定--接続情報)
5. [バックアップ(ダンプ)](#5-バックアップダンプ)
6. [復元](#6-復元)
7. [運用シナリオ(レシピ)](#7-運用シナリオレシピ)
8. [コマンドリファレンス](#8-コマンドリファレンス)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [注意事項・制限](#10-注意事項制限)

---

## 1. 概要

### 1-1. v_dump が行うこと

| 段階 | 内容 | 産出/ツール |
|---|---|---|
| **バックアップ(dump)** | テーブルデータを `COPY` 互換の `.dat` ファイルへ、構造を `schema.ddl.sql` へ抽出 | `vertica-python`(純粋 Python ドライバ) |
| **復元(restore)** | `.dat` を `COPY ... FROM LOCAL` で再ロード | `vsql` |

`pg_dump` のように SQL を一塊にするのではなく、**データ(.dat)** と **構造(DDL)** を分離して出力する。
`.dat` は Vertica `COPY` の標準コンベンション(パイプ区切り、`\N` NULL、バックスラッシュ escape)そのままなので、最も高速に再ロードできる。

### 1-2. 動作原理を一目で

```
┌─────────────────────────┐        バックアップ   ┌──────────────────────┐
│  v_dump コンテナ         │  vertica-python →    │  Vertica (運用 DB)    │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup (マウント)      │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │   復元   vsql COPY    │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ ホストの ./backup フォルダがコンテナ /backup へマウント
```

中核となる原則: **変わらないもの(コード・python・vsql)はイメージに、変わるもの(接続情報・バックアップファイル)はランタイムにマウント/注入。**
→ イメージは一度ビルドすれば再利用する。

### 1-3. 実行方式は 2 つ

- **Docker (本ドキュメント)** — `v_dump-docker.sh` ラッパーでコンテナを実行。閉域網/Python インストールが困難な環境。
- **ネイティブ** — ホストに Python を入れて `run.sh` を実行。`README.md` 2〜6 章を参照。

本ガイドは **Docker 方式**のみを扱う。

---

## 2. 事前準備物

### ビルドマシン (インターネット O)
- Docker (または podman)
- インターネット接続 (base イメージ pull + pip 依存関係のダウンロード)
- アーキテクチャ: **x86_64(amd64)** — vsql バイナリが amd64 なので、イメージも amd64 に固定される。

### 閉域網ホスト (インターネット X)
- Docker (または podman) — **それ以外の Python/vsql などは一切不要**
- 対象 Vertica へのネットワーク到達性 (例: `5433` ポート)

### 資料
- `v_dump/` ディレクトリ (ソース + `Dockerfile` + `docker/`)
- `vertica-client-*.tar.gz` (vsql クライアント、`v_dump/` 内に同梱)

---

## 3. イメージビルド & 閉域網への持ち込み

閉域網はレジストリからの pull ができないため、**イメージを tar に固めて運ぶ。**

### 3-1. ビルド (インターネット接続ビルドマシン)

```bash
cd v_dump
./docker/build-image.sh            # ビルドのみ
./docker/build-image.sh --save     # ビルド + v_dump-image.tar 保存(持ち込み用)
```

- ビルドマシンがインターネットに接続されているので、pip が適切な wheel を取得する。
- 最後に `RUN vsql --version` で vsql のリンキングを自己検証する。ここで失敗したら、欠けているシステム
  ライブラリを `Dockerfile` の apt 行に追加する。
- **ソースはビルドのたびに常に新しく反映される**(cache-bust 適用)。コードを直して再ビルドすればそれで終わり。

### 3-2. 持ち込みバンドルの作成

閉域網へ持っていくのは、たった 3 つ:

| ファイル | 役割 | 必須 |
|---|---|---|
| `v_dump-image.tar` | イメージ本体 (python・vsql・コードすべて含む) | ✅ |
| `docker/v_dump-docker.sh` | 実行ラッパー (マウント/接続情報/`-o` 自動) | ✅ |
| `v_dump.yaml` | 接続情報 (env で渡すなら省略可) | △ |

```bash
# [ビルドマシン]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 接続情報を埋めるなら

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 持ち込み & 登録 (閉域網ホスト)

`v_dump-bundle.tar.gz` を USB/内部網などで移したあと:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # イメージ登録
docker images | grep v_dump             # v_dump:latest を確認
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` は `podman` があれば podman、なければ `docker` を自動選択する。

---

## 4. 初回設定 — 接続情報

接続情報を渡す方法は 2 つあり、**どちらか一方だけ**行えばよい。

### 4-1. (推奨) yaml ファイル

`v_dump.yaml` を埋めておくと、ラッパーが自動で見つけてコンテナにマウントする。一度埋めれば毎回渡す必要はない。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ パスワードが平文なので、イメージには焼き込まない(`.dockerignore` で除外)。ランタイムにのみマウントされる。
> 自動探索順: `実行位置/v_dump.yaml` → `実行位置/backup/v_dump.yaml` → スクリプトの親。

**マルチノードクラスタ。** `host` にノードを **カンマで複数** 記述すると:
- 前のノードが落ちると次のノードへ **自動 failover** (ノードあたりの接続タイムアウト 8 秒)
- 並列ダンプワーカーと復元 COPY セッションが **各ノードに分散**され、単一ノード initiator のボトルネックを解消する

```yaml
vertica:
  host: node1,node2,node3      # 最も近くて速いノードを先頭に
  port: 5433                   # 全ノード共通ポート
  ...
```

> ノードを 1 つだけ書けば、従来とまったく同じ動作になる(後方互換)。

### 4-2. 環境変数 (一時的・別 DB)

その実行の直前にだけ付ければ、yaml より優先される。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

使用可能な変数: `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 接続確認

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# Vertica Analytic Database v24.1.0-0  のように出れば OK
```

---

## 5. バックアップ(ダンプ)

### 5-1. まず出力構造を理解する

バックアップフォルダは **`v_dump-docker.sh` があるパスの `backup/`** に作られる(どこで実行してもスクリプトの隣、`-o` 不要)。
その下に **`<スキーマ>/<テーブル>`**、スキーマ全体は **`<スキーマ>/all`** の構造で出力される。

```
backup/                              # v_dump-docker.sh の隣 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # スキーマ全体ダンプ(-t なし)
    │   ├── MANIFEST.txt             # メタ情報(行数、失敗/プロシージャ一覧)
    │   ├── schema.ddl.sql           # 構造 DDL (冪等な形)
    │   ├── load.sql                 # 再ロード COPY 文
    │   └── MY_SCHEMA.<table>.dat    # テーブルごとのデータ
    ├── TB_SAMPLE/                # -t で指定したテーブル(テーブルごとに 1 フォルダ)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # このテーブル + マッチするプロシージャ DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

各末端フォルダ(`all`、`TB_xxx`)は、**それ自体で復元可能な自己完結ユニット**である。

### 5-2. スキーマ全体のバックアップ

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (スキーマの全一般テーブル + 構造 + プロシージャ)
```

実行すると、このように出力される:
```
[run] backup dir: /現在のパス/backup  →  コンテナ /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 特定テーブルのバックアップ (1 個 / 複数)

```bash
# 1 個
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 複数 — 繰り返し(-t A -t B) またはカンマ(-t A,B,C)、混在可
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → テーブルごとに 1 フォルダずつ: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 複数テーブルを渡しても、コネクションは **1 個だけ** 開いて順次処理する。1 つのテーブルが失敗しても残りは続行する。

### 5-4. プロシージャの同伴バックアップ

テーブル単位(`-t`)のバックアップなら、**同じスキーマで名前にそのテーブル名を含むプロシージャ**を探し、
DDL を `schema.ddl.sql` の末尾に一緒に格納する(デフォルト ON)。命名規則を仮定しない単純な部分一致である。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql の末尾に:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- オフにするには `--no-procedures`。
- どのプロシージャが含まれた/スキップされたかは `MANIFEST.txt` の `procedures:` セクションに残る。
- (スキーマ全体ダンプはプロシージャがすでに含まれるので、この追加動作は不要。)

### 5-5. モード — 構造のみ / データのみ

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # DDL のみ (.dat なし)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # データのみ (DDL/load.sql なし)
```

### 5-6. 結果の確認

```bash
ls -R backup/MY_SCHEMA/TB_SAMPLE/
cat  backup/MY_SCHEMA/TB_SAMPLE/MANIFEST.txt
```

`MANIFEST.txt` の例:
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

> 産出ファイルは **ホストユーザー所有**で出力される(コンテナ root ではない)。ホスト上でそのまま削除・移動できる。

### 5-7. 速度・容量 — 適応型並列 & 圧縮

**適応型並列(自動)。** ダンプはワークロードを見て自動で並列化する — 細かければ順次、
テーブルが多ければテーブル単位の並列、巨大テーブルは行単位でシャーディングして並列。気にすることはない。
ワーカー数はデフォルト `min(コア数, 4)`。調整は環境変数で:

```bash
V_DUMP_JOBS=8 ./v_dump-docker.sh dump --schema MY_SCHEMA   # ワーカー 8 個
V_DUMP_JOBS=1 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 順次を強制
```

**マルチノードならさらに速い。** `host` にノードをカンマで列挙すると(4-1)、並列ワーカー/COPY セッションが
各ノードに分散され、1 ノードにデータが集中するボトルネックが解消される。3 ノードならその分スループットが上がる。

**圧縮(`--compress`)。** `.dat` を gzip(`.dat.gz`)で保存する。エアギャップ移送で **USB に移す
容量を大きく削減してくれる**(データによって 5〜10×)。復元は `load.sql` に `GZIP` フィルタが自動で埋め込まれ、
**Vertica COPY が圧縮ファイルを直接読むので**別途展開する必要がなく **無損失**である。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --compress      # → MY_SCHEMA.<table>.dat.gz
```

> 移送パイプライン(① ダンプ → ② 転送 → ③ ロード)の全段階が速くなる:
> ① ダンプ並列 · ② `--compress` で転送負荷↓ · ③ 復元も並列(下の 6 章)。

---

## 6. 復元

### 6-1. 復元モデルの理解

デフォルトの復元は **データロード(`COPY`)** である。つまり **対象テーブルが事前に存在**している必要がある。
構造がなければ `--with-ddl` で構造から作って復元する。

復元パスは **backup 基準の相対パス**(末端フォルダ)で記述する。

> **復元も並列(自動)。** テーブルが複数あれば COPY を複数セッションで同時にロードする
> (`V_DUMP_JOBS` で調整、`=1` なら順次)。圧縮バックアップ(`.dat.gz`)もそのまま復元される — `load.sql`
> に `GZIP` フィルタがあるので Vertica が自動で展開する。
> ただし並列は **テーブル単位のコミット**(`=1` 順次は単一トランザクション)。全量の原子性が必要なら `V_DUMP_JOBS=1`。

### 6-2. 全体/単一フォルダの復元 (データ)

```bash
# スキーマ全体バックアップ分を復元
./v_dump-docker.sh restore MY_SCHEMA/all

# 単一テーブルフォルダを復元
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 選択復元 (フォルダ内の一部テーブルのみ)

`all` のように複数テーブルが入ったフォルダから一部だけをロードする。指定テーブルの `COPY` 文だけを抽出して実行し、
フォルダにないテーブルを渡すと **実行前に失敗**させて事故を防ぐ。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 構造から生成してロード — `--with-ddl`

空の対象(テーブルがまだない DB)に復元するとき。`schema.ddl.sql`(テーブル+プロシージャ)を先に実行してからデータをロードする。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- DDL が **冪等**(下の 6-5)なので、既に存在するオブジェクトがあってもそのままスキップする。
- `-t` と一緒に渡すと、**構造は全体** `schema.ddl.sql` で作り、**データは指定テーブルのみ**を入れる。

### 6-5. 冪等性 (再実行が安全)

`schema.ddl.sql` の DDL は、再実行してもエラーが出ないように変換されている。

| 元 | 保存される形 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 同じ DDL を 2 回実行しても「already exists」エラーなしに `nothing was done` でスキップする。
(ただし `ALTER TABLE ... ADD CONSTRAINT` のような制約は冪等の対象ではなく、再実行時に重複する可能性がある。)

### 6-6. 別サーバーへの復元

復元対象がバックアップ元と別の DB なら、その実行にだけ接続情報を上書きする。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 運用シナリオ(レシピ)

### 7-1. 運用 → 開発 DB へ特定テーブルを数個移送

```bash
# 1) 運用でバックアップ (yaml = 運用接続)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 開発 DB へ構造+データを復元 (env で開発接続を上書き)
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. スキーマ丸ごとバックアップ保管

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. プロシージャまで含めてバックアップ/復元

```bash
# バックアップ: -t バックアップならプロシージャ自動包含(デフォルト)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 復元: --with-ddl がテーブル+プロシージャ DDL を一緒に生成してからデータをロード
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 任意の点検クエリ

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # DDL のみ手動実行
```

---

## 8. コマンドリファレンス

### 8-1. ラッパー (`v_dump-docker.sh`)

```
./v_dump-docker.sh <コマンド> [引数...]

コマンド:
  dump    <v_dump 引数>   バックアップ。-o は自動(/backup)。結果は ./backup/<schema>/<table|all>/
  restore <フォルダ> [オプション]   復元。フォルダ = backup 基準の相対パス(末端フォルダ)
  vsql    <vsql 引数>     コンテナ vsql の生実行(点検/手動 SQL)
  help                    ヘルプ
```

### 8-2. dump 引数

| 引数 | 説明 |
|---|---|
| `--schema, -n <S>` | 対象スキーマ (必須) |
| `--table, -t <T>` | 特定テーブル。繰り返し/カンマで複数。省略時はスキーマ全体(`all`) |
| `--schema-only` | DDL のみ |
| `--data-only` | データのみ |
| `--with-procedures` / `--no-procedures` | プロシージャの同伴抽出 ON/OFF (デフォルト ON) |
| `--compress` | `.dat` を gzip(`.dat.gz`)へ → 転送/保管容量↓ (復元は自動・無損失) |

> `-o` はラッパーが `/backup` に自動指定するので渡す必要はない。
> 並列は自動(ワークロードベース)。`V_DUMP_JOBS` で調整(8-5)。

### 8-3. restore オプション

| オプション | 説明 |
|---|---|
| `-t, --table <T>` | フォルダ内で指定テーブルの COPY のみをロード (繰り返し/カンマ) |
| `--with-ddl` | データロード前に `schema.ddl.sql`(構造+プロシージャ)を先に実行 |

### 8-4. 接続情報 (優先順位: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. その他の環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `V_DUMP_JOBS` | `auto` | 並列ワーカー数。`auto`=min(コア数,4)、整数=固定、`1`=順次。**ダンプ・復元共通** |
| `V_DUMP_PROGRESS` | `auto` | 進捗バーを強制 on(`1`)/off(`0`)。デフォルトは端末のときのみ |
| `ENGINE` | (自動) | コンテナエンジンを強制(`docker`\|`podman`)。未指定時はイメージを持つエンジンを自動 |
| `BACKUP_DIR` | スクリプト隣の `backup` | バックアップフォルダの位置を強制指定 |
| `IMAGE` | `v_dump:latest` | 使用するイメージタグ |
| `V_DUMP_YAML` | (自動探索) | yaml パスを強制指定 |

---

## 9. トラブルシューティング

### ビルド/イメージ

**Q. ソースを直して再ビルドしたのに、古い動作のままだ。**
→ 以前は buildkit キャッシュがソースを掴めない事故があった。現在は cache-bust が適用され、
`./docker/build-image.sh` を回すだけでソースが常に反映される。それでも疑わしければ:
`docker run --rm --entrypoint grep v_dump:latest -c "<変更したコードの一部>" /app/v_dump/dumper.py`
でイメージ内のコードを直接確認するか、`docker build --no-cache ...` で強制的に再ビルドする。

**Q. `RUN vsql --version` でビルドが失敗する。**
→ vsql がリンクするシステムライブラリが欠けている。`Dockerfile` の apt インストール行に該当する `.so`
パッケージを追加する(デフォルト: `libssl3`、`libreadline8`)。

### 接続

**Q. コンテナから Vertica に接続できない。**
→ `./v_dump-docker.sh vsql -c "SELECT 1;"` で隔離点検する。ホストオンリー/プライベート網なら、コンテナの
デフォルトブリッジでルーティングが通るか確認する(必要なら `--network host` をラッパーに追加)。

### バックアップ

**Q. プロシージャが付いてこない。**
→ ① テーブル単位(`-t`)のバックアップである必要がある(スキーマ全体は EXPORT が既に含む)。② プロシージャ名に
対象テーブル名が実際に含まれている必要がある。③ `MANIFEST.txt` の `procedures:` に `skipped` と
記録されていれば理由を見る(引数なしプロシージャは個別抽出ができずスキップされることがある)。

**Q. テキストに改行/タブが入ったデータが壊れないか?**
→ 壊れない。escape が Vertica COPY 規約(バックスラッシュ+元バイト)に合わせてあるので、
改行・タブ・`|`・`\` が無損失でラウンドトリップする。

### 復元

**Q. 復元時に `COPY: Input record has different number of columns`。**
→ `.dat` のカラム数と対象テーブル構造が合わない場合。同じバックアップ分の `schema.ddl.sql` で
構造を合わせるか(`--with-ddl`)、対象テーブル定義を確認する。

**Q. `--with-ddl` 実行中に赤いエラーが見える。**
→ 冪等なので「already exists」は `nothing was done` でスキップされる。ただし `ON_ERROR_STOP` をオフに
してあるため、制約の重複など一部のエラーはメッセージだけ出力して進む。構造が本当に
作られなかった場合は、続くデータロード(COPY)が明確に失敗して知らせる。

---

## 10. 注意事項・制限

- **運用 DB 注意**: `restore` / `--with-ddl` は対象 DB に **書き込み**が発生する。運用テーブルに
  復元するとデータが累積したりプロシージャが置き換わったりする。対象の接続情報を必ず確認すること。
- **外部テーブル**はデータが Vertica の外にあるため自動で除外される(DDL は含む、`.dat` なし)。
- **引数なしプロシージャ**は個別 DDL 抽出ができずスキップされることがある(マニフェストに記録)。
- **制約(ALTER ADD CONSTRAINT)** は冪等の対象ではなく、再実行時に重複する可能性がある。
- vsql は **amd64** バイナリである。イメージ/ホストが x86_64 である必要がある。
- パスワードは平文 yaml なので、ファイル権限・git 除外に注意する。

---

## 付録 A. クイックスタートチェックリスト

```
[ビルドマシン]
□ cd v_dump
□ ./docker/build-image.sh --save
□ cp v_dump.yaml.example v_dump.yaml  (接続情報を入力)
□ tar czf v_dump-bundle.tar.gz v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

[閉域網ホスト]
□ tar xzf v_dump-bundle.tar.gz
□ docker load -i v_dump-image.tar
□ chmod +x v_dump-docker.sh
□ ./v_dump-docker.sh vsql -c "SELECT version();"   (接続確認)

[バックアップ]
□ ./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
□ ls backup/MY_SCHEMA/TB_SAMPLE/

[復元]
□ ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

## 付録 B. よく使うワンライナー

```bash
# 接続確認
./v_dump-docker.sh vsql -c "SELECT version();"

# スキーマ全体バックアップ
./v_dump-docker.sh dump --schema MY_SCHEMA

# テーブル複数バックアップ (+プロシージャ)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 単一テーブル復元 (構造から)
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 別 DB へ復元
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — **v_dump** の作者および著作権者。Licensed under the Apache License 2.0 (see LICENSE).
