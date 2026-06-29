# v_dump — Vertica データバックアップツール

[🇰🇷 한국어](README.md) · [🇬🇧 English](README.en.md) · **🇯🇵 日本語** · [🇨🇳 中文](README.zh.md)

Vertica のスキーマ/テーブルを **Vertica が再び最速で読み込める形式** でファイルに書き出し、
後で `vsql -f load.sql` の一行でリストアできるようにするバックアップユーティリティ。

`pg_dump` が SQL 一つのファイルに書き出すのとは異なり、v_dump は **データそのものを別の `.dat` ファイル** に保存する。
このファイルは Vertica `COPY` のデフォルト規約（pipe-delimited、`\N` NULL、バックスラッシュ escape）そのままなので、
`COPY ... FROM LOCAL` で最も効率的なロードが可能だ。

---

## 1. ディレクトリ構成

```
except/
├── v_dump.sh                  # 便利ショートカット（内部的に v_dump/run.sh に委譲）
└── v_dump/                    # ← 配布単位。このフォルダだけコピーすればよい
    ├── install.sh             # 冪等インストールスクリプト（venv 生成 + 依存関係）
    ├── run.sh                 # 正式ランチャー（独自 venv、位置非依存）
    ├── requirements.txt       # バージョン固定されたランタイム依存関係
    ├── v_dump.yaml.example    # 接続設定テンプレート（コミット対象）
    ├── v_dump.yaml            # 実際の接続設定（install が生成、git 除外）
    ├── .gitignore             # .venv / v_dump.yaml / backup / ビルド成果物を除外
    ├── .gitattributes         # 改行ポリシー（スクリプト LF 固定、バイナリ表示）
    ├── .venv/                 # install が作成した仮想環境（配布時には付いていかない）
    ├── __main__.py            # `python -m v_dump` のエントリポイント
    ├── cli.py                 # argparse CLI
    ├── config.py              # 接続設定ローダー（CLI > env > yaml）
    ├── connection.py          # vertica_python コネクションのコンテキストマネージャ
    ├── inspector.py           # v_catalog メタ照会（スキーマ/テーブル/カラム）
    ├── ddl.py                 # EXPORT_OBJECTS ベースの DDL 抽出
    ├── data.py                # テーブル → .dat シリアライズ + COPY 文生成
    ├── escape.py              # Vertica COPY 規約のエスケープ
    ├── dumper.py              # オーケストレーター（全体フロー）
    ├── Dockerfile             # ダンプ+リストア統合イメージ（python + vsql）
    ├── .dockerignore          # イメージビルドコンテキストの除外リスト
    ├── vertica-client-*.tar.gz# vsql クライアント（イメージへの vsql インストール用）
    ├── docker/
    │   ├── entrypoint.sh      # dump/restore/vsql ディスパッチャ
    │   ├── filter_load.py     # 選択的リストア用 load.sql フィルタ
    │   ├── build-image.sh     # ビルド（+--save tar）— インターネット接続ビルドマシン用
    │   └── v_dump-docker.sh   # 閉域網実行ラッパー（マウント/接続情報を自動）
    ├── GUIDE.md               # 運用ガイド（Docker/閉域網の最初から最後までのフロー）
    └── README.md              # ← 本ドキュメント（リファレンス）
```

> 初回導入/運用フローは **`GUIDE.md`** を先に見ると速い。本 README は項目別リファレンスだ。

> **2つの実行方式** がある。
> - **ネイティブ**（`install.sh` + `run.sh`）: 対象マシンに Python を直接インストールできる場合。下記 2〜6章。
> - **Docker**（`Dockerfile` + `docker/`）: 閉域網など Python のインストールが困難な場合。**11章** を参照。
>   Python・vsql・依存関係を1つのイメージに巻き込み、コンテナでダンプ/リストアする。

---

## 2. インストール（他環境への配布）

配布単位は **`v_dump/` フォルダ一つ** だ。何もインストールされていない新しい環境にそのままコピーした後、
`install.sh` だけ実行すれば独自 venv が作られ依存関係がインストールされる。**何度実行しても安全（冪等）** だ。

```bash
# 1) フォルダごと新しい環境にコピー（例）
scp -r v_dump user@newhost:/opt/

# 2) インストール
cd /opt/v_dump
./install.sh

# 3) 接続情報の入力（install が v_dump.yaml.example をコピーして作成しておく）
vi v_dump.yaml

# 4) 実行
./run.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

`install.sh` が行うこと:
- `python3`（>=3.8、`venv`+`ensurepip` が可能なもの）を自動検出 — `PYTHON=/path/to/python3 ./install.sh` で指定も可能
- `v_dump/.venv` を生成（ない場合のみ、壊れた venv は自動再生成）
- `requirements.txt` をインストール
- `v_dump.yaml` がなければ `v_dump.yaml.example` をコピーして生成（**既存の設定は絶対に上書きしない**）
- `run.sh` に実行権限を付与 + import/CLI 検証

> **事前要件**: 対象マシンに `python3` と `python3-venv`（Debian/Ubuntu）または同等のものが必要だ。
> ない場合は `install.sh` がどのパッケージをインストールすべきか案内して停止する。
>
> インターネットのない閉域網の場合は、同じ OS/Python バージョンのマシンで `pip download -r requirements.txt -d wheels`
> で取得した wheel を一緒にコピーし、`./install.sh` の代わりに
> `.venv/bin/pip install --no-index --find-links wheels -r requirements.txt` でインストールする。

### 実行インターフェース — `run.sh` vs `v_dump.sh`

- `v_dump/run.sh` — **正式ランチャー**。独自 venv（`v_dump/.venv`）を使用、位置非依存。配布環境ではこれを使う。
- `except/v_dump.sh` — このプロジェクト内での便利ショートカット。内部的に `v_dump/run.sh` に委譲するだけだ。

どちらも同じように動作する。エイリアスはどちらに張ってもよい。

```bash
echo 'alias v_dump=/opt/v_dump/run.sh' >> ~/.bashrc && source ~/.bashrc
```

### 基本接続設定

`v_dump/v_dump.yaml` に入る（install が example から生成）。他の環境に接続するには値だけ変えればよい。

```yaml
vertica:
  host: 10.0.0.5
  port: 5433
  username: dbadmin
  password: "********"
  dbname: VMart
  tlsmode: disable
```

> パスワードが平文で保存されるので、`.gitignore` に
> ```
> v_dump/v_dump.yaml
> ```
> の項目を必ず追加すること。

### 2-3. 設定の優先順位

同じ項目が複数の場所にある場合、次の順序で決定される。

1. **CLI 引数** — `--host`、`--user`、`--password`、`--database`、`--port`、`--tlsmode`
2. **環境変数** — `VERTICA_HOST`、`VERTICA_PORT`、`VERTICA_USER`、`VERTICA_PASSWORD`、`VERTICA_DATABASE`、`VERTICA_TLSMODE`
3. **`--config` で指定した yaml**
4. **デフォルト yaml**（`v_dump/v_dump.yaml`）

### 2-4. プロシージャの同伴抽出

テーブル単位（`-t`）のバックアップ時、**同じスキーマのプロシージャのうち名前にそのテーブル名が含まれるもの** を
探し、DDL を一緒に書き出す（`schema.ddl.sql` に追加）。命名規則（接頭辞/接尾辞）を仮定しない
単純な部分一致なので、別途設定が不要だ。

- 例: テーブル `TB_SAMPLE` → プロシージャ `PROC_TB_SAMPLE_1`（名前にテーブル名を含む）を自動マッチング。
- プロシージャ一覧は `v_catalog.user_procedures` から読み込み、引数のあるプロシージャはシグネチャまで
  含めて `EXPORT_OBJECTS` で抽出する。
- マッチングがない、または抽出に失敗したものはスキップし `MANIFEST.txt` に理由を残す（ダンプは継続）。
- 全体をオフにするには CLI で `--no-procedures`。

---

## 3. 実行

### 3-1. 最速の一行

```bash
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

`v_dump.sh` は venv の python + PYTHONPATH を自動的に設定してくれる。**どのディレクトリから呼び出しても** 動作する。

エイリアス登録を推奨:

```bash
echo 'alias v_dump=/home/duarl/KRWay/except/v_dump.sh' >> ~/.bashrc
source ~/.bashrc
v_dump --schema MY_SCHEMA -o ./MY_SCHEMA_backup
```

### 3-2. よく使うパターン

```bash
# （すべての例で -o はベースパス。その下に <schema>/<table|all> で生成される。）

# スキーマ全体（74個のテーブルを一度に）        → ./backup/MY_SCHEMA/all/
v_dump --schema MY_SCHEMA -o ./backup

# 特定のテーブルのみ                            → ./backup/MY_SCHEMA/TB_SAMPLE/
v_dump --schema MY_SCHEMA --table TB_SAMPLE -o ./backup

# テーブル複数（繰り返し/カンマ/混在）— テーブルごとに1フォルダ
v_dump --schema MY_SCHEMA -t TB_SAMPLE -t TB_SAMPLE2 -o ./backup
v_dump --schema MY_SCHEMA -t TB_A,TB_B,TB_C -o ./backup

# テーブル単位のバックアップ時、名前にそのテーブル名が含まれるプロシージャ DDL も自動で含む（デフォルト ON）
v_dump --schema MY_SCHEMA -t TB_SAMPLE -o ./backup            # +プロシージャ DDL
v_dump --schema MY_SCHEMA -t TB_SAMPLE --no-procedures -o ./backup  # プロシージャ除外

# DDL のみ（.dat を作らない）
v_dump --schema MY_SCHEMA --schema-only -o ./backup

# データのみ（DDL/load.sql を作らない）
v_dump --schema MY_SCHEMA --data-only -o ./backup

# 他の環境に接続
v_dump --host 10.0.0.5 --user readonly --password secret \
       --database VMart --schema PUBLIC -o ./backup

# 別の設定ファイルを使用
v_dump --config /etc/v_dump/prod.yaml --schema MY_SCHEMA -o ./backup

# ヘルプ
v_dump --help
```

---

## 4. 出力物

`-o` は **ベースパス** であり、その下に `<schema>/<table>`（スキーマ全体は `<schema>/all`）構造が作られる。
各末端フォルダはそれ自体でリストア可能な **自己完結型ダンプ単位** だ。

```
<ベース>/                          # -o で渡したパス（Docker は /backup = 固定 backup）
├── <schema>/
│   ├── all/                      # スキーマ全体ダンプ（-t なし）
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql
│   │   ├── load.sql
│   │   └── <schema>.<table>.dat  # すべてのテーブル
│   ├── <table_A>/                # -t で指定したテーブル（テーブルごとに1フォルダ）
│   │   ├── MANIFEST.txt
│   │   ├── schema.ddl.sql        # そのテーブル（+マッチしたプロシージャ）DDL
│   │   ├── load.sql
│   │   └── <schema>.<table_A>.dat
│   └── <table_B>/
│       └── ...
```

各フォルダ内のファイル構成:

| ファイル | 内容 |
|---|---|
| `MANIFEST.txt` | ダンプメタ情報（ホスト、scope、行数、失敗/プロシージャ一覧） |
| `schema.ddl.sql` | `CREATE SCHEMA / TABLE ...`（+テーブル単位ならマッチしたプロシージャ DDL） |
| `load.sql` | そのフォルダの `.dat` を再ロードする `COPY` 文の集合 |
| `<schema>.<table>.dat` | テーブルごとのデータファイル |

### 4-1. `.dat` ファイル形式

Vertica `COPY` のデフォルト規約と正確に一致する。

| 項目 | 値 | 備考 |
|---|---|---|
| 区切り文字 | `|` | Vertica COPY default |
| NULL 表現 | `\N` | `NULL AS '\N'` |
| ESCAPE | `\` | Vertica COPY default |
| 行終端 | LF (`\n`) | |
| エンコーディング | UTF-8 | |
| 引用符処理 | ENCLOSED BY なし | 区切り文字/終端子/escape のみ `\` でエスケープ |

エスケープ対象は **escape char（`\`）、区切り文字（`|`）、行終端子（LF/CR）** のみだ。
Vertica COPY の escape は「`\` の次の1バイトをデータとしてそのまま扱う」ので、
エスケープは **バックスラッシュ + 元のバイト** で書く（例: 改行 → `\`+LF）。`\n` のように文字 `n` を
付けるとロード時に文字 `n` として壊れる。タブ・垂直タブ・フォームフィードなどは区切り文字でも終端子でもないので
エスケープせずそのまま残す。

型別の表現:
- BOOLEAN: `t` / `f`
- INT, NUMERIC, FLOAT: 文字列そのまま
- DATE: `YYYY-MM-DD`
- TIMESTAMP: `YYYY-MM-DD HH:MM:SS[.ffffff]`
- TIME: `HH:MM:SS`
- VARBINARY: `\xNN` シーケンス
- NULL: `\N`

例:
```
1|Store1|1|16 Elm St|Concord|CA|West|Plan1|Premium|None|1000|2000|2007-03-01|\N|18|12576|39|2284
```

### 4-2. `schema.ddl.sql`

Vertica の `EXPORT_OBJECTS()` が生成する DDL を **再実行安全（冪等）** な形に変換して格納する。
`CREATE SCHEMA`、`CREATE SEQUENCE`、`CREATE TABLE`、`CREATE PROJECTION` などを含む。

冪等変換（既存のオブジェクトに再実行してもエラーなく通過する）:

| 元 | 変換 |
|---|---|
| `CREATE SCHEMA` / `TABLE` / `SEQUENCE` / `PROJECTION` | `... IF NOT EXISTS` |
| `CREATE PROCEDURE` / `VIEW` | `CREATE OR REPLACE ...` |

> 行頭（`^`）基準でのみ置換するので、プロシージャ本体内部のテキストには手を付けない。
> （制約 `ALTER TABLE ... ADD CONSTRAINT` は冪等対象ではない — 再実行時に重複エラーの可能性あり。）

テーブル単位（`-t`）のバックアップで名前にそのテーブル名が含まれるプロシージャがあれば、ファイル末尾に
`-- ===== stored procedures =====` セクションとしてマッチしたプロシージャの `CREATE PROCEDURE` DDL が付く。
存在しない、または抽出に失敗したプロシージャはスキップし `MANIFEST.txt` に理由が残る（ダンプは継続）。
（スキーマ全体ダンプは `EXPORT_OBJECTS(schema)` がプロシージャまで既に含むので、このセクションは別途ない。）

### 4-3. `load.sql`

各 `.dat` を再ロードするための `COPY FROM LOCAL` 文の集合。1つのトランザクションで包まれており、途中失敗時は全体ロールバック。

```sql
\set ON_ERROR_STOP on
BEGIN;

COPY "MY_SCHEMA"."TB_SAMPLE" (col1, col2, ...) FROM LOCAL 'MY_SCHEMA.TB_SAMPLE.dat'
  DELIMITER '|' NULL AS '\N' ENCLOSED BY '' ABORT ON ERROR DIRECT;
...

COMMIT;
```

> `DIRECT` が付いているので WOS を経由せず ROS に直接ロード → 大容量で最速。

### 4-4. `MANIFEST.txt`

```
v_dump manifest
  host        : 10.0.0.5:5433
  database    : VMart
  scope       : DS.{TB_A,TB_B}          # 複数テーブルなら {..} で表記
  dumped at   : 2026-05-20 10:34:40
  schema_only : False
  data_only   : False
  tables      : 2
  rows total  : 3953181
  procedures  : 2 included              # 含まれたプロシージャ数
  ddl file    : schema.ddl.sql
  load script : load.sql

files:
  DS.TB_A.dat	1200 rows
  DS.TB_B.dat	980 rows

failed:                                  # （失敗があるときのみ）
  DS.SOME_TABLE	<エラー最初の行>

procedures:                              # （プロシージャ抽出を試みたときのみ）
  PROC_TB_SAMPLE_1	included
  PROC_TB_SAMPLE_2	skipped (export 結果が空)
```

---

## 5. リストア

```bash
# リストアする末端フォルダに移動（スキーマ全体は <schema>/all、単一テーブルは <schema>/<table>）
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql   # テーブル生成
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql         # データロード
```

`load.sql` の `COPY ... FROM LOCAL` は **vsql クライアントの現在のディレクトリ** を基準に
`.dat` ファイルを探す。そのため必ずバックアップディレクトリで実行しなければならない。

> 空の DB ではなく既存のデータに累積したい場合は、`schema.ddl.sql` の実行はスキップし
> `load.sql` だけ実行すればよい。ただしカラム変更があった場合は COPY 文が失敗する可能性がある。

- **プロシージャ** も一緒にリストアするには `schema.ddl.sql` を実行すればよい（テーブル DDL と同じファイルに入っている）。
  DDL が冪等（`IF NOT EXISTS` / `OR REPLACE`）なので **既存のオブジェクトがあってもそのまま通過する**（再実行安全）。
- **Docker でリストア** する際は、上記2ステップを1コマンドで処理するオプションがある — `--with-ddl`（構造を先に生成）、
  `-t`（一部テーブルのみロード）。**11章** のリストア節を参照。

---

## 6. CLI オプション全体

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
  --schema, -n SCHEMA    ダンプするスキーマ名（必須）
  --table, -t TABLE      特定のテーブルのみ。繰り返し（-t A -t B）またはカンマ（-t A,B）で
                         複数指定可能。省略時はスキーマ全体。

mode:
  --schema-only          DDL のみ（データファイル X）
  --data-only            データファイルのみ（DDL/load.sql X）

procedures:
  --with-procedures      テーブル単位（-t）のバックアップ時にマッチしたプロシージャ DDL も抽出（デフォルト ON）
  --no-procedures        プロシージャ DDL 抽出をオフ

output:
  --output, -o OUTPUT    出力ディレクトリ。なければ生成。（必須）

connection:
  --host HOST
  --port PORT
  --user, -U USER
  --password, -W PASSWORD
  --database, -d DATABASE
  --tlsmode {disable,prefer,require}
  --config CONFIG        yaml 設定ファイル（デフォルト: v_dump/v_dump.yaml）
```

---

## 7. 動作特性 / 注意事項

### 7-1. 外部テーブルは自動スキップ

`CREATE EXTERNAL TABLE ... AS COPY FROM 's3://...'` のように実際のデータが Vertica の外にある
テーブルは `SELECT` 自体が外部ファイルアクセスを試みて失敗する。v_dump は
`v_catalog.tables.table_definition` が埋まっている項目を外部テーブルとみなし
**`list_tables_in_schema()` の段階で自動除外** する。

→ 外部テーブルの DDL は `schema.ddl.sql` にはそのまま含まれるが `.dat` は生成されない。
リストア後も外部データソースが生きていないとデータは見えない。

### 7-2. 単一テーブルの失敗隔離

あるテーブルの処理が例外で終わっても、残りのテーブルは継続して進行する。失敗理由は

- stderr: `[v_dump] WARN skip <schema>.<table>: <reason>`
- `MANIFEST.txt` の `failed:` セクション

の両方に記録される。部分生成された `.dat` は自動削除。

### 7-3. UTF-8 の壊れたバイトの寛容な処理

`vertica_python` の unicode デコードがデフォルト `strict` なので、一部のカラムに壊れたマルチバイトが
混ざっていると fetch が中断される。v_dump はコネクションオプション `unicode_error='replace'` で開くため、
破損箇所が `U+FFFD` に置換され、row 単位の損失なくダンプされる。

> バックアップ優先なので lenient がデフォルト。データ整合性の検証がより重要なら `config.py` の
> `unicode_error` を `'strict'` に変えればよい（失敗テーブルは manifest に表示）。

### 7-4. メモリ

サーバ側カーソルで 10,000 行ずつ fetch（`data._FETCH_SIZE`）。数億行のテーブルもメモリ OOM なく
ストリーミング処理が可能。出力は一行単位で即座にディスクに flush。

### 7-5. シーケンス/ビュー/プロシージャ

DDL 抽出は `EXPORT_OBJECTS('', 'scope')` で行う。**スキーマ全体** ダンプならスコープがスキーマなので
**Vertica が export してくれるすべてのオブジェクト**（スキーマ、シーケンス、テーブル、プロジェクション、プロシージャなど）がそのまま
`schema.ddl.sql` に入る。

**テーブル単位（`-t`）** ダンプはスコープが指定テーブルに絞られるのでプロシージャが自動的に外れる。
このときのために v_dump は `v_catalog.user_procedures` からそのスキーマのプロシージャ一覧を読み込み
**名前に対象テーブル名が含まれるもの**（`2-4` 参照）だけを選び、引数シグネチャまで付けて
`EXPORT_OBJECTS` で抽出し `schema.ddl.sql` の末尾に追記する。
マッチング/抽出に失敗したものはスキップし manifest に理由を残す（セッションはロールバックされダンプ全体は壊れない）。

> 部分一致なので同じ名前の断片を共有するプロシージャは一緒に拾われることがある（意図された動作）。
> 抽出結果が空なら manifest に `skipped` と記録されるのですぐ分かる。

データダンプ（`.dat`）は依然として **テーブルのみ** を対象とする。

### 7-6. 権限

ダンプ実行者は次の権限が必要だ。

- 対象スキーマの `USAGE`
- 対象テーブルの `SELECT`
- `EXPORT_OBJECTS` 呼び出し権限（通常すべてのユーザーが可能）
- `v_catalog.*` 照会権限（デフォルト付与）

リストア実行者は追加で `CREATE` 権限と `COPY ... FROM LOCAL` 使用権限。

---

## 8. トラブルシューティング

### `No module named v_dump`

`python -m v_dump` は `v_dump/` の **親ディレクトリ**（つまり `except/`）が cwd / PYTHONPATH にある必要がある。
簡単には `v_dump.sh` ランチャーを使えば解決。

### `dump failed: Cannot expand glob pattern due to error: Access Denied`

外部テーブルが指す S3 などの外部ファイルにアクセス権限がないときに発生。
**現在のバージョンは外部テーブルを自動除外するので** 発生しないはずだ。それでも出る場合は
inspector のフィルタ条件が環境に合っていないので manifest の `failed:` を確認。

### `unicode_error` 関連のデコードエラー

デフォルトの `replace` が処理してくれるはずだが、それでも別のエンコーディングならカラムが LATIN1 などで入っている
可能性がある。Vertica サーバ側の `SET SESSION CHARACTERSET` またはカラムエンコーディングを確認。

### vsql リストア時に `ERROR: COPY: Input record N has different number of columns`

`.dat` シリアライズと実際のカラム数が合わない場合。ほぼ常にデータ自体に壊れた escape
（例: `\` が単独で入った値）があるという意味。v_dump の escape は入力のすべての `\` を
`\\` に変換するので正常には発生しない。変換ロジックに手を入れたことがあるなら
`escape.py` の `_ESCAPES` マッピングを点検。

### load.sql が長すぎてメモリ負担

`load.sql` は 73 個の `COPY` 文程度なら数十 KB レベル。テーブル数が数千個なら分割が必要だが、
その場合はスキーマ別に別々にダンプする方が運用上より安全。

---

## 9. 内部モジュール一行要約

| モジュール | 責任 |
|---|---|
| `config.py` | `ConnectionConfig` dataclass + CLI/env/yaml 優先順位の決定 |
| `connection.py` | `vertica_python.connect` コンテキストマネージャ |
| `inspector.py` | `v_catalog` 照会: スキーマ/テーブル/カラム一覧、外部テーブル判別 |
| `ddl.py` | `EXPORT_OBJECTS()` 呼び出しラッパー |
| `escape.py` | 単一値/行 → Vertica COPY 本文文字列への変換 |
| `data.py` | テーブル行ストリーミング + COPY 文ビルダー |
| `dumper.py` | 全体フローのオーケストレーション + manifest/失敗処理 |
| `cli.py` | argparse + 終了コード |
| `__main__.py` | `python -m v_dump` のエントリ |

---

## 10. クイックリファレンス — 1ページ要約

```bash
# バックアップ（→ ./backup/MY_SCHEMA/all/ に生成）
/home/duarl/KRWay/except/v_dump.sh --schema MY_SCHEMA -o ./backup

# リストア（末端フォルダに cd）
cd ./backup/MY_SCHEMA/all
vsql -h 10.0.0.5 -U dbadmin -d VMart -f schema.ddl.sql
vsql -h 10.0.0.5 -U dbadmin -d VMart -f load.sql
```

---

## 11. Docker（閉域網 / Python インストール困難環境）

対象マシンに Python を直接インストールしにくいときに使う方式。**Python + vsql + 依存関係** を1つのイメージに巻き込み、
コンテナでダンプとリストアの両方を実行する。接続情報・バックアップファイル・ダンプ対象のような「変わる値」はすべて
ランタイムにボリューム/環境変数で注入するので **イメージは一度だけビルド** すれば再利用する。

```
イメージ（不変: python・vsql・コード）  +  ランタイムマウント（接続情報・バックアップファイル）  =  コンテナ
```

### 11-1. ビルド（インターネット接続できるマシンで）

```bash
cd v_dump
./docker/build-image.sh            # イメージビルドのみ
./docker/build-image.sh --save     # ビルド + v_dump-image.tar 保存（閉域網持ち込み用）
```

- ビルドマシンがインターネットに接続されているので、pip が base Python に合う wheel を自動的に取得する
  （バンドル wheel のバージョン固定問題を回避）。
- `vsql` は x86_64 バイナリなので、イメージも **amd64 に固定** されている（`Dockerfile` の `--platform=linux/amd64`）。
  arm64 の Mac でビルドしても amd64 イメージで出力される。
- ビルド最後に `RUN vsql --version` でライブラリリンクを自己検証する。万一別の `.so` が
  ないと失敗する場合は、そのパッケージだけ `Dockerfile` の apt 行に追加すればよい。

### 11-2. 閉域網持ち込み — docker のみあるという前提

閉域網はレジストリ pull ができないので **イメージを tar に書き出して移す。** 持っていく束はわずか3つ:

| 持っていくもの | 何 | 備考 |
|---|---|---|
| `v_dump-image.tar` | イメージ本体 | `--save` 成果物。python・vsql・コード・`filter_load.py` がすべて入っている |
| `docker/v_dump-docker.sh` | 実行ラッパー | マウント/接続情報の組み立て自動化（なくても raw `docker run` 可能、11-6） |
| `v_dump.yaml` | 接続情報 | または env で渡すなら省略可能 |

```bash
# [インターネット接続ビルドマシン] 一つの塊にまとめる
cd v_dump
./docker/build-image.sh --save
cp v_dump.yaml.example v_dump.yaml          # 接続情報を埋めておくなら
tar czf v_dump-bundle.tar.gz \
    v_dump-image.tar docker/v_dump-docker.sh v_dump.yaml

# ── v_dump-bundle.tar.gz を閉域網に持ち込み（USB/内部網など）──

# [閉域網、docker のみインストール済み]
tar xzf v_dump-bundle.tar.gz
docker load -i v_dump-image.tar             # イメージ登録
docker images | grep v_dump                 # v_dump:latest 確認
vi v_dump.yaml                              # 接続情報入力（または env）
chmod +x v_dump-docker.sh
```

> `v_dump-docker.sh` は `podman` があれば podman、なければ `docker` を自動選択する。
> docker のみの環境でもそのまま動作する。

### 11-3. ダンプ（コンテナ）

バックアップフォルダは **`v_dump-docker.sh` があるパスの `backup/`** に作られ、コンテナの `/backup` に
マウントされる（どこで実行しても常にスクリプトの隣に作られる）。**`-o` を渡す必要はない** —
ラッパーが出力ベースを常に `/backup` に設定し、その下に `<schema>/<table>`（全体は `<schema>/all`）構造で書き出される。

```bash
# 接続情報は v_dump.yaml 自動探索または env。-o 省略。
./v_dump-docker.sh dump --schema MY_SCHEMA        # → backup/MY_SCHEMA/all/

# テーブル複数 + プロシージャ（DS/DM 自動）— テーブルごとに1フォルダ
./v_dump-docker.sh dump --schema DS -t TB_A,TB_B  # → backup/DS/TB_A/ , backup/DS/TB_B/
```

→ 結果がスクリプトの隣 `backup/<schema>/...` に書き出される。
実行時にそのパスが `[run] backup dir: ...` として出力される。別の場所に置くには `BACKUP_DIR=/パス` で上書きするだけでよい。

### 11-4. リストア（コンテナ）

リストアパスは **backup 基準の相対パス**（`<schema>/<table>` または `<schema>/all`）で書けばよい。
`COPY ... FROM LOCAL` がクライアント相対パスなので、ラッパーが該当フォルダに自動で移動する。

```bash
# スキーマ全体リストア（末端フォルダを指す）
./v_dump-docker.sh restore MY_SCHEMA/all

# 単一テーブルフォルダのリストア
./v_dump-docker.sh restore DS/TB_A

# 構造から生成後ロード — 空の対象にテーブル+プロシージャ DDL を先に実行
./v_dump-docker.sh restore DS/TB_A --with-ddl

# all フォルダから一部テーブルのみ選んでロード
./v_dump-docker.sh restore MY_SCHEMA/all -t TB_A,TB_B

# 任意点検: vsql の生実行
./v_dump-docker.sh vsql -c "SELECT version()"
```

- `--with-ddl` は `schema.ddl.sql`（テーブル+プロシージャ）をデータロードの **前に** 実行する。
  DDL が冪等（`IF NOT EXISTS` / `OR REPLACE`）なので **既存のオブジェクトにはそのまま通過する**。
  構造生成が実際に失敗したなら、続く COPY が明確に失敗して知らせてくれる。
- `-t` と `--with-ddl` を一緒に渡すと、**構造は全体** `schema.ddl.sql` で作り、**データは指定テーブルのみ** 入れる。

### 11-5. 接続情報の注入（優先順位: env > yaml）

| 方法 | 使用 | 備考 |
|---|---|---|
| yaml（推奨） | `v_dump.yaml` を一度埋めておけば自動探索 | 毎回渡さなくてよい。平文パスワードなのでイメージには焼き込まない |
| 環境変数 | その実行の前に `VERTICA_*=...` | 一回限りで別の DB に接続するとき。yaml より優先 |

```bash
# 通常: yaml そのまま → 何も渡さない
./v_dump-docker.sh dump --schema DS -t TB_A

# リストアだけ別サーバに: その実行にのみ env で上書き
VERTICA_HOST=10.0.0.9 VERTICA_USER=dbadmin VERTICA_PASSWORD=*** VERTICA_DATABASE=VMart \
  ./v_dump-docker.sh restore DS/TB_A --with-ddl
```

`VERTICA_HOST · VERTICA_PORT · VERTICA_USER · VERTICA_PASSWORD · VERTICA_DATABASE · VERTICA_TLSMODE`
のうち設定されたものだけがコンテナに渡される。yaml パスを直接指定するには `V_DUMP_YAML=/path/v_dump.yaml`。

### 11-6. ラッパーなしの raw `docker run`

`v_dump-docker.sh` が展開するコマンドは結局これだけだ（直接書いてもよい）:

```bash
docker run --rm \
  -v "$PWD/backup:/backup" \
  -v "$PWD/v_dump.yaml:/app/v_dump/v_dump.yaml:ro" \
  v_dump:latest \
  dump --schema DS -t TB_A,TB_B -o /backup       # → /backup/DS/TB_A , /backup/DS/TB_B

# 接続情報を env で（リストアは末端フォルダを指す）:
docker run --rm -v "$PWD/backup:/backup" \
  -e VERTICA_HOST=10.0.0.5 -e VERTICA_USER=dbadmin \
  -e VERTICA_PASSWORD=*** -e VERTICA_DATABASE=VMart \
  v_dump:latest restore /backup/DS/TB_A --with-ddl
```

コンテナのエントリポイントコマンド: `dump` / `restore` / `vsql` / `shell` / `help`。
`docker run --rm v_dump:latest help` でヘルプ出力。

### 11-7. イメージを再び巻き直す必要があるとき

接続情報・バックアップファイル・ダンプ対象はすべてランタイム注入なので **イメージとは無関係** だ。再ビルドが必要なのは
**イメージの内容物が変わるとき** のみ:

- `v_dump` コード（`*.py`）の修正
- `requirements.txt` の依存関係変更
- vsql（vertica-client）のバージョン交換

このときだけ 11-1 を再び実行して新しい `v_dump-image.tar` を持ち込む。
