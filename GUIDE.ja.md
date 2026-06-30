# v_dump 利用ガイド (Docker / 閉域網)

[🇰🇷 한국어](GUIDE.md) · [🇬🇧 English](GUIDE.en.md) · **🇯🇵 日本語** · [🇨🇳 中文](GUIDE.zh.md)

Vertica のスキーマ・テーブル・プロシージャをファイルにバックアップし、`vsql` で再ロード(復元)するツール
**v_dump** を **Docker コンテナ**として運用する実務ガイド。

> 本書は閉域網(インターネット不可)サーバに Python を直接インストールしづらい環境を想定する。
> Python・vsql・依存関係を一つのイメージにまとめ、コンテナでバックアップ/復元を行う。
> リファレンス的な詳細は `README.md`、運用フローは本書を参照。

---

## 目次

1. [概要 — 何を、どのように](#1-概要)
2. [事前準備物](#2-事前準備物)
3. [イメージビルド & 閉域網への搬入](#3-イメージビルド--閉域網への搬入)
4. [初回設定 — 接続情報](#4-初回設定--接続情報)
5. [バックアップ(ダンプ)](#5-バックアップダンプ)
6. [復元](#6-復元)
7. [運用シナリオ(レシピ)](#7-運用シナリオレシピ)
8. [コマンドリファレンス](#8-コマンドリファレンス)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [注意事項・限界](#10-注意事項限界)

---

## 1. 概要

### 1-1. v_dump がやること

| 段階 | 内容 | 産出/ツール |
|---|---|---|
| **バックアップ(dump)** | テーブルデータを `COPY` 互換の `.dat` ファイルに、構造を `schema.ddl.sql` に抽出 | `vertica-python`(純粋 Python ドライバ) |
| **復元(restore)** | `.dat` を `COPY ... FROM LOCAL` で再ロード | `vsql` |

`pg_dump` のように SQL 一塊ではなく、**データ(.dat)** と **構造(DDL)** を分離して出力する。
`.dat` は Vertica `COPY` 既定の規約(パイプ区切り、`\N` NULL、バックスラッシュ escape)そのままなので最速で再ロードできる。

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
        │ ホストの ./backup フォルダがコンテナ /backup へマウントされる
```

核心原則: **変わらないもの(コード・python・vsql)はイメージに、変わるもの(接続情報・バックアップファイル)はランタイムにマウント/注入。**
→ イメージは一度ビルドすれば再利用する。

### 1-3. 実行方式は二つ

- **Docker (本書)** — `v_dump-docker.sh` ラッパーでコンテナ実行。閉域網/Python インストール困難な環境向け。
- **ネイティブ** — ホストに Python を入れて `run.sh` 実行。`README.md` 2〜6章を参照。

本ガイドは **Docker 方式**のみを扱う。

---

## 2. 事前準備物

### ビルドマシン (インターネット O)
- Docker (または podman)
- インターネット接続 (base イメージ pull + pip 依存関係のダウンロード)
- アーキテクチャ: **x86_64(amd64)** — vsql バイナリが amd64 なのでイメージも amd64 に固定される。

### 閉域網ホスト (インターネット X)
- Docker (または podman) — **それ以外の Python/vsql 等は一切不要**
- 対象 Vertica へのネットワーク到達性 (例: `5433` ポート)

### 資材
- `v_dump/` ディレクトリ (ソース + `Dockerfile` + `docker/`)
- `vertica-client-*.tar.gz` (vsql クライアント、`v_dump/` 内に同梱)

---

## 3. イメージビルド & 閉域網への搬入

閉域網はレジストリ pull ができないので **イメージを tar にして運ぶ。**

### 3-1. ビルド (インターネットビルドマシン)

```bash
cd v_dump
./docker/build-image.sh            # ビルドのみ
./docker/build-image.sh --save     # ビルド + v_dump-image.tar 保存(搬入用)
```

- ビルドマシンがインターネットに繋がっているので pip が適切なホイールを取得する。
- 最後に `RUN vsql --version` で vsql のリンクを自己検証する。ここで失敗したら、欠けているシステム
  ライブラリを `Dockerfile` の apt 行に追加する。
- **ソースはビルドのたびに常に新しく反映される**(cache-bust 適用)。コードを直して再ビルドすればよい。

### 3-2. 搬入バンドルを作る

閉域網へ持っていくものはたった3つ:

| ファイル | 役割 | 必須 |
|---|---|---|
| `v_dump-image.tar` | イメージ本体 (python・vsql・コードを全部含む) | ✅ |
| `docker/v_dump-docker.sh` | 実行ラッパー (マウント/接続情報/`-o` 自動) | ✅ |
| `v_dump.yaml` | 接続情報 (env で与えるなら省略可) | △ |

```bash
# [ビルドマシン]
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml      # 接続情報を埋めるなら

tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml
```

### 3-3. 搬入 & 登録 (閉域網ホスト)

`v_dump-bundle.tar.gz` を USB/内部網などで移したのち:

```bash
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar         # イメージ登録
docker images | grep v_dump             # v_dump:latest を確認
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` は `podman` があれば podman、なければ `docker` を自動選択する。

---

## 4. 初回設定 — 接続情報

接続情報の与え方は二つあり、**どちらか一方だけ**やればよい。

### 4-1. (推奨) yaml ファイル

`v_dump.yaml` を埋めておけばラッパーが自動で見つけてコンテナにマウントする。一度埋めれば毎回与えなくてよい。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: MYDB
  tlsmode: disable
```

> ⚠️ パスワードが平文なのでイメージには焼き込まない(`.dockerignore` で除外)。ランタイムにのみマウントされる。
> 自動探索順: `実行位置/v_dump.yaml` → `実行位置/backup/v_dump.yaml` → スクリプトの親。

### 4-2. 環境変数 (一回限り・別 DB)

その実行の前にだけ付ければ yaml より優先される。

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
その下に **`<スキーマ>/<テーブル>`**、スキーマ全体は **`<スキーマ>/all`** 構造で出力される。

```
backup/                              # v_dump-docker.sh の隣 (docker/backup)
└── MY_SCHEMA/
    ├── all/                         # スキーマ全体ダンプ(-t なし)
    │   ├── MANIFEST.txt             # メタ情報(行数、失敗/プロシージャ一覧)
    │   ├── schema.ddl.sql           # 構造 DDL (冪等な形)
    │   ├── load.sql                 # 再ロード COPY 文
    │   └── MY_SCHEMA.<table>.dat    # テーブルごとのデータ
    ├── TB_SAMPLE/                # -t で指定したテーブル(テーブルごとに一フォルダ)
    │   ├── MANIFEST.txt
    │   ├── schema.ddl.sql           # このテーブル + マッチするプロシージャの DDL
    │   ├── load.sql
    │   └── MY_SCHEMA.TB_SAMPLE.dat
    └── TB_SAMPLE2/
        └── ...
```

各末端フォルダ(`all`、`TB_xxx`)は **それ自体で復元可能な自己完結単位**である。

### 5-2. スキーマ全体バックアップ

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA
#   → backup/MY_SCHEMA/all/  (スキーマの全一般テーブル + 構造 + プロシージャ)
```

実行するとこう出力される:
```
[run] backup dir: /現在パス/backup  →  コンテナ /backup
[v_dump] MY_SCHEMA → tables=69 rows=12345678 → /backup/MY_SCHEMA/all/
```

### 5-3. 特定テーブルのバックアップ (1個 / 複数個)

```bash
# 1個
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   → backup/MY_SCHEMA/TB_SAMPLE/

# 複数個 — 繰り返し(-t A -t B) またはカンマ(-t A,B,C)、混用可能
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE,TB_SAMPLE2
#   → テーブルごとに一フォルダずつ: backup/MY_SCHEMA/TB_SAMPLE/ , .../TB_SAMPLE2/
```

> 複数テーブルを与えても、コネクションは **1個だけ**開いて順次処理する。あるテーブルが失敗しても残りは続行される。

### 5-4. プロシージャ同伴バックアップ

テーブル単位(`-t`)のバックアップなら、**同じスキーマで名前にそのテーブル名を含むプロシージャ**を探して
DDL を `schema.ddl.sql` の末尾に一緒に収める(既定 ON)。命名規則を仮定しない単純な部分一致だ。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE
#   schema.ddl.sql の末尾に:
#     -- ===== stored procedures =====
#     CREATE OR REPLACE PROCEDURE MY_SCHEMA.PROC_TB_SAMPLE_1(...) ...
```

- 無効化するには `--no-procedures`。
- どのプロシージャが含まれた/スキップされたかは `MANIFEST.txt` の `procedures:` セクションに残る。
- (スキーマ全体ダンプはプロシージャが既に含まれるので、この追加動作は不要。)

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

`MANIFEST.txt` 例:
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

> 産出ファイルは **ホストユーザ所有**で出力される(コンテナ root ではない)。ホストからそのまま削除・移動できる。

### 5-7. 速度・容量 — 適応型並列 & 圧縮

**適応型並列(自動)。** ダンプはワークロードを見て自動で並列化する — 細かいものは順次、
テーブルが多ければテーブル単位で並列、巨大テーブルは行単位でシャーディングして並列。気にすることはない。
ワーカー数は既定で `min(コア, 4)`。調節は環境変数:

```bash
V_DUMP_JOBS=8 ./v_dump-docker.sh dump --schema MY_SCHEMA   # ワーカー 8個
V_DUMP_JOBS=1 ./v_dump-docker.sh dump --schema MY_SCHEMA   # 順次を強制
```

**圧縮(`--compress`)。** `.dat` を gzip(`.dat.gz`)で保存する。エアギャップ移管で **USB で運ぶ
容量を大幅に減らせる**(データによって 5〜10×)。復元は `load.sql` に `GZIP` フィルタが自動で埋まり、
**Vertica COPY が圧縮ファイルを直接読むので** 別途展開する必要がなく **無損失**だ。

```bash
./v_dump-docker.sh dump --schema MY_SCHEMA --compress      # → MY_SCHEMA.<table>.dat.gz
```

> 移管パイプライン(① ダンプ → ② 転送 → ③ ロード)の全段階が速くなる:
> ① ダンプ並列 · ② `--compress` で転送負荷↓ · ③ 復元も並列(下記6章)。

---

## 6. 復元

### 6-1. 復元モデルの理解

基本の復元は **データロード(`COPY`)** だ。つまり **対象テーブルが事前に存在**していなければならない。
構造がなければ `--with-ddl` で構造から作ってロードする。

復元パスは **backup 基準の相対パス**(末端フォルダ)で書く。

> **復元も並列(自動)。** テーブルが複数あれば COPY をセッション複数で同時にロードする
> (`V_DUMP_JOBS` で調節、`=1` なら順次)。圧縮バックアップ(`.dat.gz`)もそのまま復元される — `load.sql`
> に `GZIP` フィルタがあるので Vertica が自動で展開する。
> ただし並列は **テーブル単位コミット**(`=1` 順次は単一トランザクション)。全量の原子性が必要なら `V_DUMP_JOBS=1`。

### 6-2. 全体/単一フォルダの復元 (データ)

```bash
# スキーマ全体バックアップの復元
./v_dump-docker.sh restore MY_SCHEMA/all

# 単一テーブルフォルダの復元
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE
```

### 6-3. 選択復元 (フォルダ内の一部テーブルのみ)

`all` のように複数テーブルが入ったフォルダから一部だけロードする。指定テーブルの `COPY` 文だけ抜き出して実行し、
フォルダに無いテーブルを与えると **実行前に失敗**させて事故を防ぐ。

```bash
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_SAMPLE,TB_SAMPLE2
```

### 6-4. 構造から作成してロード — `--with-ddl`

空の対象(テーブルがまだ無い DB)に復元するとき。`schema.ddl.sql`(テーブル+プロシージャ)を先に実行してからデータをロードする。

```bash
./v_dump-docker.sh restore MY_SCHEMA/TB_SAMPLE --with-ddl
```

- DDL が **冪等**(下記6-5)なので既にあるオブジェクトがあってもそのまま飛ばす。
- `-t` と一緒に与えると **構造は全体** `schema.ddl.sql` で作り、**データは指定テーブルのみ** 入れる。

### 6-5. 冪等性 (再実行安全)

`schema.ddl.sql` の DDL は再実行してもエラーにならないよう変換されている。

| 原本 | 保存される形 |
|---|---|
| `CREATE SCHEMA / TABLE / SEQUENCE / PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE / VIEW` | `CREATE OR REPLACE ...` |

→ 同じ DDL を二度回しても "already exists" エラーなしに `nothing was done` で飛ばす。
(ただし `ALTER TABLE ... ADD CONSTRAINT` のような制約は冪等の対象ではなく、再実行時に重複し得る。)

### 6-6. 別サーバへの復元

復元対象がバックアップ原本と別 DB なら、その実行にだけ接続情報を上書きする。

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
# バックアップ: -t バックアップならプロシージャ自動同梱(既定)
./v_dump-docker.sh dump --schema MY_SCHEMA -t TB_SAMPLE

# 復元: --with-ddl がテーブル+プロシージャ DDL を一緒に作成後、データをロード
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
| `--with-procedures` / `--no-procedures` | プロシージャ同伴抽出 ON/OFF (既定 ON) |
| `--compress` | `.dat` を gzip(`.dat.gz`)に → 転送/保管容量↓ (復元自動・無損失) |

> `-o` はラッパーが `/backup` に自動指定するので与える必要はない。
> 並列は自動(ワークロードベース)。`V_DUMP_JOBS` で調節(8-5)。

### 8-3. restore オプション

| オプション | 説明 |
|---|---|
| `-t, --table <T>` | フォルダ内で指定テーブルの COPY のみロード (繰り返し/カンマ) |
| `--with-ddl` | データロード前に `schema.ddl.sql`(構造+プロシージャ)を先に実行 |

### 8-4. 接続情報 (優先順位: env > yaml)

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`

### 8-5. その他の環境変数

| 変数 | 既定 | 説明 |
|---|---|---|
| `V_DUMP_JOBS` | `auto` | 並列ワーカー数。`auto`=min(コア,4)、整数=固定、`1`=順次。**ダンプ・復元共通** |
| `V_DUMP_PROGRESS` | `auto` | 進捗バー強制 on(`1`)/off(`0`)。既定はターミナルのときのみ |
| `ENGINE` | (自動) | コンテナエンジン強制(`docker`\|`podman`)。未指定時はイメージを持つエンジンを自動 |
| `BACKUP_DIR` | スクリプトの隣 `backup` | バックアップフォルダ位置を強制指定 |
| `IMAGE` | `v_dump:latest` | 使用するイメージタグ |
| `V_DUMP_YAML` | (自動探索) | yaml パスを強制指定 |

---

## 9. トラブルシューティング

### ビルド/イメージ

**Q. ソースを直して再ビルドしたのに古い動作のままだ。**
→ 以前は buildkit キャッシュがソースを掴めない事故があった。現在は cache-bust が適用され
`./docker/build-image.sh` を回すだけでソースが常に反映される。それでも疑わしければ:
`docker run --rm --entrypoint grep v_dump:latest -c "<変えたコードの一部>" /app/v_dump/dumper.py`
でイメージ内のコードを直接確認するか、`docker build --no-cache ...` で強制再ビルド。

**Q. `RUN vsql --version` でビルドが失敗する。**
→ vsql がリンクするシステムライブラリが欠けている。`Dockerfile` の apt インストール行に該当の `.so`
パッケージを追加する(既定: `libssl3`、`libreadline8`)。

### 接続

**Q. コンテナから Vertica に繋がらない。**
→ `./v_dump-docker.sh vsql -c "SELECT 1;"` で隔離点検。ホストオンリー/私設網ならコンテナの
既定ブリッジでルーティングが通るか確認(必要なら `--network host` をラッパーに追加)。

### バックアップ

**Q. プロシージャが付いてこない。**
→ ① テーブル単位(`-t`)バックアップでなければならない(スキーマ全体は EXPORT が既に含む)。② プロシージャ名に
対象テーブル名が実際に含まれていなければならない。③ `MANIFEST.txt` の `procedures:` に `skipped` で
記録されていれば理由を見る(引数なしプロシージャは個別抽出ができずスキップされ得る)。

**Q. テキストに改行/タブのあるデータが壊れないか?**
→ 壊れない。escape が Vertica COPY 規約(バックスラッシュ+原本バイト)に合わせてあるので
改行・タブ・`|`・`\` が無損失でラウンドトリップする。

### 復元

**Q. 復元時に `COPY: Input record has different number of columns`。**
→ `.dat` のカラム数と対象テーブル構造が合わない場合。同じバックアップの `schema.ddl.sql` で
構造を合わせるか(`--with-ddl`)、対象テーブル定義を確認する。

**Q. `--with-ddl` 実行中に赤いエラーが見える。**
→ 冪等なので "already exists" は `nothing was done` で飛ばす。ただし `ON_ERROR_STOP` を切って
いるため、制約の重複など一部のエラーはメッセージだけ出力して進む。構造が本当に作られて
いなければ、続くデータロード(COPY)が明確に失敗して知らせてくれる。

---

## 10. 注意事項・限界

- **運用 DB 注意**: `restore` / `--with-ddl` は対象 DB に **書き込み**が発生する。運用テーブルに
  復元するとデータが累積したりプロシージャが置き換わる。対象の接続情報を必ず確認すること。
- **外部テーブル**はデータが Vertica の外にあるため自動的に除外される(DDL は含む、`.dat` なし)。
- **引数なしプロシージャ**は個別 DDL 抽出ができずスキップされ得る(マニフェストに記録)。
- **制約(ALTER ADD CONSTRAINT)** は冪等の対象ではなく、再実行時に重複し得る。
- vsql は **amd64** バイナリだ。イメージ/ホストが x86_64 でなければならない。
- パスワードは平文 yaml なので、ファイル権限・git 除外に留意する。

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

## 付録 B. よく使う一行

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

© 2026 염기승 (Gibseung Yeom) <duarltmd1@naver.com> — **v_dump** の作者および著作権者。All rights reserved.
