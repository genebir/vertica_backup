# v_dump 利用ガイド (Docker / 閉域網)

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · **🇯🇵 日本語** · [🇨🇳 中文](GUIDE.zh.md)

Vertica のスキーマ・テーブル・プロシージャをファイルにバックアップし、`vsql` で再度ロード(リストア)するツール
**v_dump** を **Docker コンテナ**として運用するための実務ガイド。

> 本ドキュメントは、閉域網(インターネット不可)のサーバーに Python を直接インストールしにくい環境を前提とする。
> Python・vsql・依存関係を 1 つのイメージにまとめ、コンテナとしてバックアップ/リストアを実行する。
> リファレンス的な詳細は `README.md`、運用の流れは本ドキュメントを参照する。

---

## 目次

1. [概要 — 何を、どのように](#1-概要)
2. [事前準備物](#2-事前準備物)
3. [イメージのビルド & 閉域網への搬入](#3-イメージのビルド--閉域網への搬入)
4. [初期設定 — 接続情報](#4-初期設定--接続情報)
5. [バックアップ(ダンプ)](#5-バックアップダンプ)
6. [リストア](#6-リストア)
7. [運用シナリオ(レシピ)](#7-運用シナリオレシピ)
8. [コマンドリファレンス](#8-コマンドリファレンス)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [注意事項・制限](#10-注意事項制限)

---

## 1. 概要

### 1-1. v_dump が行うこと

| 段階 | 内容 | 成果物/ツール |
|---|---|---|
| **バックアップ(dump)** | テーブルデータを `COPY` 互換の `.dat` ファイルに、構造を `schema.ddl.sql` に抽出 | `vertica-python`(純粋な Python ドライバ) |
| **リストア(restore)** | `.dat` を `COPY ... FROM LOCAL` で再ロード | `vsql` |

`pg_dump` のように SQL を一塊にするのではなく、**データ(.dat)** と **構造(DDL)** を分離して出力する。
`.dat` は Vertica `COPY` の標準コンベンション(パイプ区切り、`\N` NULL、バックスラッシュ escape)そのままなので、最速で再ロードできる。

### 1-2. 動作原理をひと目で

```
┌─────────────────────────┐        バックアップ    ┌──────────────────────┐
│  v_dump コンテナ          │  vertica-python →    │  Vertica (運用 DB)    │
│  (python + vsql)         │  SELECT / EXPORT     │                      │
│                          │  ───────────────────▶│                      │
│  /backup (マウント)       │                      │                      │
│   └ <schema>/<table>/    │◀───────────────────  │                      │
│       .dat / .sql        │  リストア vsql COPY   │                      │
└─────────────────────────┘  ───────────────────▶└──────────────────────┘
        ▲
        │ ホストの ./backup フォルダがコンテナの /backup へマウントされる
```

中核となる原則: **変わらないもの(コード・python・vsql)はイメージに、変わるもの(接続情報・バックアップファイル)はランタイムにマウント/注入。**
→ イメージは一度ビルドすれば再利用する。

### 1-3. 実行方式は 2 通り

- **Docker (本ドキュメント)** — `v_dump-docker.sh` ラッパーでコンテナを実行。閉域網/Python のインストールが困難な環境向け。
- **ネイティブ** — ホストに Python を入れて `run.sh` を実行。`README.md` 2~6章を参照。

本ガイドは **Docker 方式**のみを扱う。

---

## 2. 事前準備物

### ビルドマシン (インターネット O)
- Docker (または podman)
- インターネット接続 (base イメージの pull + pip 依存関係のダウンロード)
- アーキテクチャ: **x86_64(amd64)** — vsql バイナリが amd64 なので、イメージも amd64 に固定される。

### 閉域網ホスト (インターネット X)
- Docker (または podman) — **それ以外の Python/vsql などは一切不要**
- 対象 Vertica へのネットワーク到達性 (例: `5433` ポート)

### 資料
- `v_dump/` ディレクトリ (ソース + `Dockerfile` + `docker/`)
- `vertica-client-*.tar.gz` (vsql クライアント、`v_dump/` 内に同梱)

---

## 3. イメージのビルド & 閉域網への搬入

閉域網ではレジストリからの pull ができないため、**イメージを tar に書き出して移送する。**

### 3-1. ビルド (インターネット接続のビルドマシン)

```bash
cd v_dump
./docker/build-image.sh            # ビルドのみ
./docker/build-image.sh --save     # ビルド + v_dump-image.tar 保存(搬入用)
```

- ビルドマシンがインターネットに接続されているため、pip が適切なホイールを取得する。
- 最後に `RUN vsql --version` で vsql のリンクを自己検証する。ここで失敗する場合は、不足しているシステム
  ライブラリを `Dockerfile` の apt の行に追加する。
- **ソースはビルドのたびに常に新しく反映される**(cache-bust 適用)。コードを直して再ビルドすれば完了。

### 3-2. 搬入バンドルを作る

閉域網へ持ち込むものは、たった 3 つ:

| ファイル | 役割 | 必須 |
|---|---|---|
| `v_dump-image.tar` | イメージ本体 (python・vsql・コードをすべて含む) | ✅ |
| `docker/v_dump-docker.sh` | 実行ラッパー (マウント/接続情報/`-o` 自動) | ✅ |
| `v_dump.yaml` | 接続情報 (env で渡すなら省略可) | △ |

```bash
# [ビルドマシン]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 接続情報を記入するなら

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 搬入 & 登録 (閉域網ホスト)

`v_dump-bundle.tar.gz` を USB/内部網などで移送した後:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # イメージ登録
docker images | grep v_dump             # v_dump:latest を確認
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` は `podman` があれば podman、なければ `docker` を自動選択する。

---

## 4. 初期設定 — 接続情報

接続情報を渡す方法は 2 通りで、**どちらか一方だけ**を行えばよい。

### 4-1. (推奨) yaml ファイル

`v_dump.yaml` に記入しておけば、ラッパーが自動で見つけてコンテナにマウントする。一度記入すれば毎回渡す必要がない。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ パスワードが平文なのでイメージに焼き込まない(`.dockerignore` で除外)。ランタイム時のみマウントされる。
> 自動探索の順序: `実行位置/v_dump.yaml` → `実行位置/backup/v_dump.yaml` → スクリプトの親。

### 4-2. 環境変数 (一回限り・別の DB)

その実行の直前にだけ付ければ、yaml より優先される。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh dump --schema PUBLIC
```

利用可能な変数: `VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 4-3. 接続確認

```bash
./v_dump-docker.sh vsql -c "SELECT version();"
# Vertica Analytic Database v24.1.0-0  のように表示されれば OK
```

---

## 5. バックアップ(ダンプ)

### 5-1. まずは出力構造を理解する

バックアップフォルダは **`v_dump-docker.sh` があるパスの `backup/`** に作られる(どこで実行してもスクリプトの隣、`-o` 不要)。
その下に **`<スキーマ>/<テーブル>`**、スキーマ全体は **`<スキーマ>/all`** という構造で出力される。

```
backup/                              # v_dump-docker.sh の隣 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # スキーマ全体のダンプ(-t なし)
    │   ├── MANIFEST.txt             # メタ情報(行数、失敗/プロシージャ一覧)
    │   ├── schema.ddl.sql           # 構造 DDL (冪等な形)
    │   ├── load.sql                 # 再ロード用 COPY 文
    │   └── MY_SCHEMA.<table>.dat    # テーブルごとのデータ
    ├── TB_SAMPLE/                # -t で指定したテーブル(テーブルごとに 1 フォルダ)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # このテーブル + 一致したプロシージャの DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

各末端フォルダ(`all`、`TB_xxx`)は **それ自体でリストア可能な自己完結した単位**である。

### 5-2. スキーマ全体のバックアップ

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (スキーマのすべての一般テーブル + 構造 + プロシージャ)
```

実行すると、このように出力される:
```
[run] backup dir: /現在のパス/backup  →  コンテナ /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 特定テーブルのバックアップ (1 個 / 複数個)

```bash
# 1 個
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 複数個 — 繰り返し(-t A -t B) またはカンマ(-t A,B,C)、併用も可
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → テーブルごとに 1 フォルダずつ: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 複数のテーブルを渡しても、コネクションは **1 個だけ**開いて順次処理する。1 つのテーブルが失敗しても、残りは続行される。

### 5-4. プロシージャの同梱バックアップ

テーブル単位(`-t`)のバックアップの場合、**同じスキーマ内で、名前にそのテーブル名を含むプロシージャ**を探し、
DDL を `schema.ddl.sql` の末尾に一緒に格納する(デフォルト ON)。命名規則を仮定しない単純な部分一致である。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql の末尾に:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 無効にするには `--no-procedures`。
- どのプロシージャが含まれ/スキップされたかは `MANIFEST.txt` の `procedures:` セクションに記録される。
- (スキーマ全体のダンプはプロシージャがすでに含まれるため、この追加動作は不要。)

### 5-5. モード — 構造のみ / データのみ

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --schema-only   # DDL のみ (.dat なし)
./v_dump-docker.sh dump --schema MY_SCHEMA --data-only     # データのみ (DDL/load.sql なし)
```

### 5-6. 結果確認

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

> 成果物ファイルは **ホストユーザーの所有**として出力される(コンテナの root ではない)。ホスト側でそのまま削除・移動できる。

---

## 6. リストア

### 6-1. リストアモデルの理解

基本のリストアは **データロード(`COPY`)** である。つまり **対象テーブルが事前に存在**している必要がある。
構造がない場合は `--with-ddl` で構造から作成してロードする。

リストアパスは **backup 基準の相対パス**(末端フォルダ)で記述する。

### 6-2. 全体/単一フォルダのリストア (データ)

```bash
# スキーマ全体のバックアップをリストア
./v_dump-docker.sh restore MY_SCHEMA/all

# 単一テーブルフォルダをリストア
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 選択リストア (フォルダ内の一部テーブルのみ)

`all` のように複数テーブルを含むフォルダから一部だけをロードする。指定したテーブルの `COPY` 文のみを抽出して実行し、
フォルダに存在しないテーブルを渡すと **実行前に失敗**させて事故を防ぐ。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 構造から生成してからロード — `--with-ddl`

空の対象(テーブルがまだない DB)へリストアするとき。`schema.ddl.sql`(テーブル+プロシージャ)を先に実行してからデータをロードする。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- DDL が **冪等**(下記 6-5)なので、すでに存在するオブジェクトがあってもそのままスキップされる。
- `-t` と併用すると、**構造は全体**の `schema.ddl.sql` で作成し、**データは指定テーブルのみ**を投入する。

### 6-5. 冪等性 (再実行の安全性)

`schema.ddl.sql` の DDL は、再実行してもエラーにならないよう変換されている。

| 元 | 保存される形 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 同じ DDL を 2 回実行しても "already exists" エラーなしに `nothing was done` でスキップされる。
(ただし `ALTER TABLE ... ADD CONSTRAINT` のような制約は冪等の対象ではないため、再実行時に重複する可能性がある。)

### 6-6. 別サーバーへのリストア

リストア対象がバックアップ元と異なる DB の場合、その実行に限り接続情報を上書きする。

```bash
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

---

## 7. 運用シナリオ(レシピ)

### 7-1. 運用 → 開発 DB へ特定テーブルを数個移管

```bash
# 1) 運用でバックアップ (yaml = 運用接続)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2

# 2) 開発 DB へ構造+データをリストア (env で開発接続を上書き)
for T in TB_SAMPLE TB_SAMPLE2; do
  VERTICA_HOST=dev-host VERTICA_DATABASE=MYDB_DEV \
    ./v_dump-docker.sh restore MY_SCHEMA/$T --with-ddl
done
```

### 7-2. スキーマをまるごとバックアップ保管

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA2        # → backup/MY_SCHEMA2/all/
tar czf MY_SCHEMA2_$(date +%Y%m%d).tar.gz -C backup MY_SCHEMA2
```

### 7-3. プロシージャまで含めてバックアップ/リストア

```bash
# バックアップ: -t バックアップならプロシージャを自動で含む(デフォルト)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# リストア: --with-ddl がテーブル+プロシージャの DDL を一緒に生成してからデータをロード
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

### 7-4. 任意の点検クエリ

```bash
./v_dump-docker.sh vsql -c "SELECT COUNT(*) FROM MY_SCHEMA.TB_SAMPLE;"
./v_dump-docker.sh vsql -f /backup/MY_SCHEMA/TB_SAMPLE/schema.ddl.sql   # DDLのみ手動実行
```

---

## 8. コマンドリファレンス

### 8-1. ラッパー (`v_dump-docker.sh`)

```
./v_dump-docker.sh <コマンド> [引数...]

コマンド:
  dump    <v_dump 引数>   バックアップ。-o は自動(/backup)。結果は ./backup/<schema>/<table|all>/
  restore <フォルダ> [オプション]   リストア。フォルダ = backup 基準の相対パス(末端フォルダ)
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
| `--with-procedures` / `--no-procedures` | プロシージャの同梱抽出 ON/OFF (デフォルト ON) |

> `-o` はラッパーが `/backup` に自動指定するため、渡す必要はない。

### 8-3. restore オプション

| オプション | 説明 |
|---|---|
| `-t, --table <T>` | フォルダ内で指定テーブルの COPY のみをロード (繰り返し/カンマ) |
| `--with-ddl` | データロードの前に `schema.ddl.sql`(構造+プロシージャ)を先に実行 |

### 8-4. 接続情報 (優先順位: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. その他の環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `BACKUP_DIR` | `$PWD/backup` | バックアップフォルダの位置を強制指定 |
| `IMAGE` | `v_dump:latest` | 使用するイメージタグ |
| `V_DUMP_YAML` | (自動探索) | yaml パスを強制指定 |

---

## 9. トラブルシューティング

### ビルド/イメージ

**Q. ソースを直して再ビルドしたのに、古い動作のままだ。**
→ 以前は buildkit のキャッシュがソースを捉えられない事故があった。現在は cache-bust が適用されており、
`./docker/build-image.sh` を実行するだけでソースが常に反映される。それでも疑わしい場合は:
`docker run --rm --entrypoint grep v_dump:latest -c "<変更したコードの一部>" /app/v_dump/dumper.py`
でイメージ内のコードを直接確認するか、`docker build --no-cache ...` で強制的に再ビルドする。

**Q. `RUN vsql --version` でビルドが失敗する。**
→ vsql がリンクするシステムライブラリが不足している。`Dockerfile` の apt インストールの行に該当する `.so`
パッケージを追加する(デフォルト: `libssl3`, `libreadline8`)。

### 接続

**Q. コンテナから Vertica に接続できない。**
→ `./v_dump-docker.sh vsql -c "SELECT 1;"` で切り分け点検。host-only/プライベートネットワークなら、コンテナの
デフォルトブリッジでルーティングできるか確認(必要に応じて `--network host` をラッパーに追加)。

### バックアップ

**Q. プロシージャが付いてこない。**
→ ① テーブル単位(`-t`)のバックアップである必要がある(スキーマ全体は EXPORT がすでに含む)。② プロシージャ名に
対象テーブル名が実際に含まれている必要がある。③ `MANIFEST.txt` の `procedures:` に `skipped` と
記録されていたら理由を確認する(引数なしのプロシージャは個別抽出ができずスキップされることがある)。

**Q. テキストに改行/タブを含むデータが壊れないか?**
→ 壊れない。escape が Vertica COPY の規約(バックスラッシュ+元のバイト)に合わせてあるため、
改行・タブ・`|`・`\` が無損失でラウンドトリップする。

### リストア

**Q. リストア時に `COPY: Input record has different number of columns`。**
→ `.dat` のカラム数と対象テーブルの構造が一致しない場合。同じバックアップの `schema.ddl.sql` で
構造を合わせるか(`--with-ddl`)、対象テーブルの定義を確認する。

**Q. `--with-ddl` の実行中に赤いエラーが見える。**
→ 冪等なので "already exists" は `nothing was done` でスキップされる。ただし `ON_ERROR_STOP` を無効に
してあるため、制約の重複など一部のエラーはメッセージだけ出力して進行する。構造が本当に
作られなかった場合は、続くデータロード(COPY)が明確に失敗して知らせてくれる。

---

## 10. 注意事項・制限

- **運用 DB への注意**: `restore` / `--with-ddl` は対象 DB に **書き込み**が発生する。運用テーブルに
  リストアするとデータが累積したりプロシージャが置き換わったりする。対象の接続情報を必ず確認すること。
- **外部テーブル**はデータが Vertica の外にあるため自動的に除外される(DDL は含む、`.dat` なし)。
- **引数なしプロシージャ**は個別の DDL 抽出ができずスキップされることがある(マニフェストに記録)。
- **制約(ALTER ADD CONSTRAINT)** は冪等の対象ではないため、再実行時に重複する可能性がある。
- vsql は **amd64** バイナリである。イメージ/ホストが x86_64 でなければならない。
- パスワードは平文の yaml なので、ファイル権限・git からの除外に留意する。

---

## 付録 A. クイックスタート チェックリスト

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

[リストア]
□ ./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

## 付録 B. よく使うワンライナー

```bash
# 接続確認
./v_dump-docker.sh vsql -c "SELECT version();"

# スキーマ全体のバックアップ
./v_dump-docker.sh dump --schema MY_SCHEMA

# テーブルを複数バックアップ (+プロシージャ)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C

# 単一テーブルのリストア (構造から)
./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl

# 別の DB へリストア
VERTICA_HOST=dev VERTICA_DATABASE=MYDB_DEV \
  ./v_dump-docker.sh restore MY_SCHEMA/TB_A --with-ddl
```

---

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — author & copyright holder of **v_dump**. All rights reserved.
