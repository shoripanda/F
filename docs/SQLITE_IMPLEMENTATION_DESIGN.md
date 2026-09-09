# SQLite Implementation Design — `database.py` / `storage.py`

## 1. 目的

この文書は `docs/PERSISTENCE_DESIGN.md` を、実装担当者がそのままコードへ落とせる粒度まで具体化した実装設計書である。

対象は以下。

- `src/fomo_follower/database.py`
- `src/fomo_follower/storage.py`
- SQLite導入に伴う `engine.py` / `app.py` / `models.py` の変更境界
- 永続化テスト

本フェーズではペーパートレード、監査用イベント、保有残高、取引履歴、損益計算用データをSQLiteへ保存する。

実資金の自動注文、FOMOへの自動ログイン、スクレイピング、秘密鍵管理は対象外とする。

---

## 2. 現状からの変更点

現在の `FollowerEngine` は以下をメモリ上に保持する。

```python
seen_event_ids: set[str]
positions: dict[tuple[str, str, str], PaperPosition]
```

SQLite化後は、Engineからこの2つの状態を削除し、永続状態の唯一の正本をSQLiteにする。

```text
Before

FollowerEngine
 ├─ seen_event_ids
 └─ positions

After

FollowerEngine
 └─ SQLiteStore
      └─ SQLite database
```

`GET /paper/positions` も `engine.positions` を直接参照せず、`SQLiteStore.list_positions()` を利用する。

---

## 3. 実装後のファイル構成

```text
src/fomo_follower/
├── __init__.py
├── app.py
├── config.py
├── models.py
├── engine.py
├── database.py        # SQLite接続・スキーマ・transaction
├── storage.py         # 永続化API
└── pnl.py             # Phase 2後半で追加する損益集計

tests/
├── test_engine.py
├── test_storage.py
└── test_persistence.py

data/
└── .gitkeep           # DB本体はcommitしない
```

DBファイルのデフォルト位置:

```text
data/fomo_follower.sqlite3
```

環境変数:

```text
FOMO_FOLLOWER_DB_PATH=data/fomo_follower.sqlite3
```

既存 `.gitignore` の `*.sqlite3` / `*.db` を維持し、利用者の取引データをGitHubへ保存しない。

---

# 4. `database.py` 設計

## 4.1 責務

`database.py` はSQLiteの低レベル処理だけを担当する。

担当:

- DBパス解決
- 親ディレクトリ作成
- SQLite接続生成
- `sqlite3.Row` 設定
- PRAGMA設定
- スキーマ初期化
- schema version管理
- transaction context manager
- commit / rollback

担当しない:

- BUY/SELLロジック
- follower判定
- PnL計算
- FastAPIレスポンス生成

---

## 4.2 公開API

想定インターフェース:

```python
class Database:
    def __init__(self, path: str | Path): ...

    @property
    def path(self) -> Path: ...

    def initialize(self) -> None: ...

    def connect(self) -> sqlite3.Connection: ...

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]: ...
```

### `Database.__init__`

- `Path` へ正規化
- DBの親ディレクトリを必要に応じて作成
- この時点では長寿命connectionを保持しない

### `connect()`

各処理ごとに新しいconnectionを返す。

グローバルなSQLite connectionをFastAPI全体で共有しない。

理由:

- FastAPIのthread利用時にconnection共有を避ける
- request間のtransaction混線を防ぐ
- `check_same_thread=False` に依存しない設計にする

接続後に最低限以下を設定する。

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA synchronous = NORMAL;
```

DB初期化時に:

```sql
PRAGMA journal_mode = WAL;
```

を設定する。

Python側では:

```python
conn.row_factory = sqlite3.Row
```

とする。

---

## 4.3 transaction

1イベントの状態変更を必ず単一connection / 単一transactionで行う。

```python
with database.transaction() as conn:
    ...
```

成功:

```text
COMMIT
```

例外:

```text
ROLLBACK
raise
```

`storage.py` がtransaction中に別connectionを開かないことを重要な制約とする。

---

## 4.4 スキーマ初期化

`initialize()` は何度呼ばれても安全なidempotent処理にする。

Uvicorn `--reload` で複数回startupが発生しても壊れないこと。

```text
initialize()
  ↓
parent mkdir
  ↓
connect
  ↓
WAL設定
  ↓
CREATE TABLE IF NOT EXISTS
  ↓
CREATE INDEX IF NOT EXISTS
  ↓
schema version確認
```

---

## 4.5 schema version

Phase 2から将来のschema変更に備え、軽量なversion tableを追加する。

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

初期値:

```text
schema_version = 1
```

初期実装ではAlembic等を導入しない。

将来 `schema_version=2` が必要になった時点でmigration関数を追加する。

---

# 5. SQLite schema

## 5.1 `events`

Fが最初に受信したイベントのcanonical record。

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    sell_fraction TEXT,
    accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 重複イベントに関する仕様

Phase 2では安全側に倒し、`event_id` は「最初に受信した時点で消費済み」とする。

つまりフォロー対象外として拒否されたイベントも、同一 `event_id` を後から再利用しない。

これは現在のインメモリ実装（フォロー対象外は `seen_event_ids` に入らない）からの意図的な仕様変更である。

理由:

- replayによる後日適用を防ぐ
- event_idを監査IDとして不変にする
- DBレベルの一意制約を単純化する

同一イベントを再送する必要がある場合、送信側が新しいevent_idを発行する。

重複attempt自体の履歴保存はPhase 2初期版では行わない。

---

## 5.2 `paper_positions`

```sql
CREATE TABLE IF NOT EXISTS paper_positions (
    trader_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    quantity TEXT NOT NULL,
    cost_basis_usd TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (trader_id, chain, token_address)
);
```

Position keyは現在のEngineと同一:

```text
(trader_id, chain, token_address)
```

数量0になっても行は削除せず保持する。

理由:

- 最終状態の監査が容易
- UPSERTが単純
- 再度BUYした場合に同一rowを再利用できる

通常のpositions APIでは `quantity > 0` のみを返し、`include_closed=true` を将来追加できる構造にする。

---

## 5.3 `paper_trades`

```sql
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    notional_usd TEXT NOT NULL,
    cost_basis_released_usd TEXT,
    realized_pnl_usd TEXT,
    executed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
```

BUY:

```text
notional_usd = paper_buy_usd
cost_basis_released_usd = NULL
realized_pnl_usd = NULL
```

SELL:

```text
sold_quantity = current_quantity × sell_fraction
sell_notional = sold_quantity × event.price
released_cost_basis = current_cost_basis × sell_fraction
realized_pnl = sell_notional - released_cost_basis
```

Positionが存在しないSELLは `paper_trades` を作成しない。

イベント自体は `events` にaccepted recordとして残し、reasonまたはaction側で `no_position` を明示する。

---

## 5.4 `token_prices`

```sql
CREATE TABLE IF NOT EXISTS token_prices (
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (chain, token_address)
);
```

この価格はリアルタイム市場価格ではない。

Phase 2初期版では最後に受信したイベント価格を保存するため、APIでは必ず:

```text
last_observed_price_usd
```

という名称を使う。

---

## 5.5 index

最低限以下を追加する。

```sql
CREATE INDEX IF NOT EXISTS idx_events_trader_time
ON events (trader_id, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_trades_trader_time
ON paper_trades (trader_id, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_trades_token_time
ON paper_trades (chain, token_address, executed_at DESC);
```

---

# 6. 金額・数量の型

既存APIとの互換性のため、Phase 2最初の実装ではPydantic入力は現状の `float` を維持してよい。

ただしDBへ渡す前に必ず:

```python
Decimal(str(value))
```

へ変換する。

SQLiteではDecimal値を `TEXT` として保存する。

Storage内部では:

```python
Decimal
```

を使用し、SQL rowとの境界だけ文字列化する。

禁止:

```python
Decimal(value)  # valueがfloatの場合
```

推奨:

```python
Decimal(str(value))
```

これによりbinary floatの誤差をDBへ固定化しない。

---

# 7. 日時の方針

DB内の日時はすべてUTCのISO 8601文字列で保存する。

例:

```text
2026-09-09T00:10:00Z
```

対象:

- `executed_at`
- `created_at`
- `updated_at`
- `observed_at`

内部でtimezone-naive datetimeを作らない。

---

# 8. `storage.py` 設計

## 8.1 原則

Storageは「1メソッド=1connection」にしない。

イベント処理では、同じtransaction objectを通して複数のDB操作を実行する。

推奨構造:

```python
class SQLiteStore:
    def __init__(self, database: Database): ...

    @contextmanager
    def transaction(self) -> Iterator[SQLiteTransaction]: ...


class SQLiteTransaction:
    def __init__(self, conn: sqlite3.Connection): ...
```

Engineは:

```python
with self.store.transaction() as tx:
    ...
```

を使用する。

これによりイベント、trade、position、priceを1つのtransactionにまとめる。

---

## 8.2 `SQLiteStore` 公開API

読み取り系はStore自身から呼べるようにする。

```python
class SQLiteStore:
    def initialize(self) -> None: ...

    @contextmanager
    def transaction(self) -> Iterator[SQLiteTransaction]: ...

    def list_positions(
        self,
        *,
        trader_id: str | None = None,
        include_closed: bool = False,
    ) -> list[StoredPosition]: ...

    def list_trades(
        self,
        *,
        trader_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoredTrade]: ...

    def list_events(
        self,
        *,
        trader_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoredEvent]: ...

    def get_last_price(
        self,
        chain: str,
        token_address: str,
    ) -> StoredPrice | None: ...
```

`limit` は最大1000等の上限を設け、無制限取得を避ける。

---

## 8.3 `SQLiteTransaction` 公開API

```python
class SQLiteTransaction:
    def event_exists(self, event_id: str) -> bool: ...

    def insert_event(
        self,
        event: TradeEvent,
        *,
        accepted: bool,
        reason: str,
    ) -> None: ...

    def get_position(
        self,
        trader_id: str,
        chain: str,
        token_address: str,
    ) -> StoredPosition | None: ...

    def upsert_position(self, position: StoredPosition) -> None: ...

    def insert_trade(self, trade: StoredTrade) -> None: ...

    def upsert_last_price(
        self,
        *,
        chain: str,
        token_address: str,
        price_usd: Decimal,
        observed_at: datetime,
        source: str,
    ) -> None: ...
```

EngineからSQL文を直接呼ばない。

---

# 9. Storage model

API request/response modelとDB row modelを分離する。

`models.py` または新しい `storage_models.py` に以下の内部modelを置く。

```text
StoredEvent
StoredTrade
StoredPosition
StoredPrice
```

外部APIの `TradeEvent` をSQLite rowそのものとして扱わない。

理由:

- API schema変更とDB schema変更を分離
- Decimalを内部型として使える
- DB固有の `id`, `created_at`, `updated_at` を安全に保持できる

初期段階ではファイル数を増やしすぎないため `models.py` に置いてもよい。

---

# 10. `FollowerEngine` の変更設計

現在:

```python
FollowerEngine(config)
```

変更後:

```python
FollowerEngine(config, store)
```

イメージ:

```python
@dataclass
class FollowerEngine:
    config: AppConfig
    store: SQLiteStore
```

以下を削除する。

```python
seen_event_ids
positions
```

---

## 10.1 `handle()` 処理順

```text
POST /events
   ↓
with store.transaction() as tx
   ↓
tx.event_exists(event_id)
   ├─ YES → duplicate_event
   └─ NO
        ↓
Follower rule確認
        ↓
拒否の場合 eventsへ accepted=0 で保存
        ↓ COMMIT
        ↓ response

followedの場合
        ↓
mode確認
        ├─ alert
        │    ↓
        │  events INSERT
        │    ↓
        │  price UPSERT
        │    ↓
        │  COMMIT
        │
        └─ paper
             ↓
           現position SELECT
             ↓
           trade計算
             ↓
           events INSERT
             ↓
           paper_trades INSERT（成立時）
             ↓
           paper_positions UPSERT（変更時）
             ↓
           token_prices UPSERT
             ↓
           COMMIT
```

### duplicate時

既存イベントを変更しない。

新しいtradeもposition変更も行わない。

---

## 10.2 BUY

```text
current = tx.get_position(...)
```

存在しない場合:

```text
quantity = 0
cost_basis = 0
```

計算:

```text
buy_quantity = paper_buy_usd / price
new_quantity = current_quantity + buy_quantity
new_cost_basis = current_cost_basis + paper_buy_usd
```

同一transactionで:

```text
insert_event
insert_trade
upsert_position
upsert_last_price
```

---

## 10.3 SELL

position無しまたはquantity=0:

```text
event accepted
paper trade無し
position変更無し
price更新あり
```

positionあり:

```text
fraction = sell_fraction or 1
sold_quantity = current_quantity × fraction
released_cost_basis = current_cost_basis × fraction
proceeds = sold_quantity × price
realized_pnl = proceeds - released_cost_basis
remaining_quantity = current_quantity - sold_quantity
remaining_cost_basis = current_cost_basis - released_cost_basis
```

浮動小数誤差対策のためDecimalを使う。

`fraction=1` の場合は最終値を明示的に:

```text
remaining_quantity = 0
remaining_cost_basis = 0
```

とする。

---

# 11. `app.py` 設計

## 11.1 startup

FastAPIのlifespanでDBを初期化する。

概念:

```text
app startup
  ↓
load_config()
  ↓
resolve DB path
  ↓
Database(...)
  ↓
SQLiteStore(...)
  ↓
store.initialize()
  ↓
FollowerEngine(config, store)
```

module import時にDB mutationを極力行わない。

テストでStore差し替えをしやすくする。

---

## 11.2 `/health`

現在の情報に追加可能:

```json
{
  "status": "ok",
  "mode": "paper",
  "storage": "sqlite",
  "database_ready": true,
  "followed_traders": ["alice", "bob"]
}
```

DBの絶対pathはAPIへ返さない。

---

## 11.3 `/paper/positions`

Storeから読む。

```text
GET /paper/positions
```

必要に応じてquery:

```text
?trader_id=alice
?include_closed=false
```

を追加する。

既存レスポンス互換を優先し、初期実装では現在のfield名を維持する。

---

## 11.4 新規API

### 取引履歴

```text
GET /paper/trades
```

query:

```text
trader_id
limit
offset
```

### イベント履歴

```text
GET /events
```

### 損益

PnL実装後:

```text
GET /paper/pnl
```

初回の `database.py` / `storage.py` 実装では、まずpositions/trades/events永続化を完成させ、PnL endpointは次の小ステップに分けてもよい。

---

# 12. PnLへの接続点

Storage実装時点で `paper_trades.realized_pnl_usd` を保存する。

これにより後からPnL集計を追加しても過去tradeを再計算する必要がない。

実現損益:

```sql
SUM(realized_pnl_usd)
```

※ TEXTのためSQLで直接SUMするよりPython側でDecimal集計することを初期実装では推奨する。

含み損益:

```text
position.quantity × last_price - position.cost_basis
```

をPython Decimalで算出する。

---

# 13. エラー処理

## DB lock

`busy_timeout=5000` で短時間待つ。

5秒以内に解消しない場合は例外としてrollbackする。

APIでは内部SQLite例外をそのまま返さない。

本番向けには:

```text
storage_error
```

等の一般化したエラーへ変換する。

---

## integrity error

`event_id` / `paper_trades.event_id` のUNIQUE違反は、競合によるduplicateの可能性がある。

可能なら `sqlite3.IntegrityError` を判定し:

```text
duplicate_event
```

として扱う。

ただし他のforeign key違反等と混同しない。

---

## transaction failure

以下のどこで失敗しても全てrollbackする。

```text
event insert
trade insert
position upsert
price upsert
```

partial commitは禁止する。

---

# 14. concurrency方針

Phase 2初期版の正式サポート:

```text
Uvicorn single process / single worker
```

WALとshort transactionにより同一プロセス内の通常利用へ対応する。

複数worker運用はSQLiteでも可能だが、書き込み競合が増えるためPhase 2の受入条件には含めない。

将来高頻度入力や複数workerが必要になった場合はPostgreSQLへ移行する。

Storage interfaceを分離するのはこの移行を容易にするためでもある。

---

# 15. テスト設計

## `tests/test_storage.py`

### DB初期化

- 空のtmp directoryからDBを生成できる
- initializeを2回呼んでも成功
- 必要テーブルが存在する

### event

- event insert/read
- duplicate event検出
- rejected event保存

### position

- position insert
- position upsert
- trader/token分離
- closed position保持

### trade

- BUY history保存
- SELL history保存
- realized PnL保存

### prices

- last price insert
- newer observationでupsert

---

## `tests/test_persistence.py`

### 再起動相当テスト

```text
store1 = SQLiteStore(db_path)
BUY
store1破棄

store2 = SQLiteStore(同じdb_path)
position取得
```

期待:

```text
quantityが残る
cost basisが残る
```

### duplicate after restart

```text
store1でevent-001処理
store2へ作り直す
同じevent-001送信
```

期待:

```text
accepted = false
reason = duplicate_event
position変化なし
trade増加なし
```

### partial sell after restart

```text
BUY 20
restart
SELL 50%
```

期待:

```text
remaining = 10
```

### realized PnL

```text
BUY 20 @ 1.25 = cost 25
SELL 10 @ 1.50
```

期待:

```text
released_cost = 12.50
proceeds = 15.00
realized_pnl = 2.50
remaining_quantity = 10
remaining_cost = 12.50
```

### transaction rollback

trade insert後・position update前に意図的に例外を発生させる。

期待:

```text
event/trade/positionすべて前状態
```

---

# 16. 既存テストとの互換

既存 `tests/test_engine.py` はインメモリEngine前提なので、Storeを注入する形式へ変更する。

各testは `tmp_path` を使った一時SQLite DBを利用する。

例:

```text
pytest temp DB
↓
Database(tmp_path / "test.sqlite3")
↓
SQLiteStore
↓
FollowerEngine(config, store)
```

テストが利用者の `data/fomo_follower.sqlite3` に触れないことを保証する。

---

# 17. 実装順序

## Step 1

`database.py`

- Database class
- connection factory
- PRAGMA
- transaction
- schema v1

## Step 2

`storage.py`

- SQLiteStore
- SQLiteTransaction
- events
- positions
- trades
- prices

## Step 3

内部storage model追加

- Decimal
- UTC日時

## Step 4

`engine.py` をStore注入方式へ変更

- memory set削除
- memory dict削除
- atomic event handling

## Step 5

`app.py` をDB初期化対応

- lifespan
- `/paper/positions` をStore経由へ変更

## Step 6

参照API

- `/paper/trades`
- `/events`

## Step 7

永続化テスト

- restart
- duplicate after restart
- partial sell
- rollback

## Step 8

PnL endpoint

- realized
- unrealized
- total
- trader別

---

# 18. 完了条件（Acceptance Criteria）

SQLite永続化Phase 2の最低完了条件を以下とする。

1. `POST /events` のBUYがSQLiteへ保存される
2. `GET /paper/positions` がSQLiteから現在残高を返す
3. Uvicorn再起動後もpositionが残る
4. 再起動後も同じevent_idを二重適用しない
5. 部分SELL後のquantity / cost basisが再起動後も残る
6. BUY/SELL履歴をSQLiteから取得できる
7. SELL時のrealized PnLがtrade recordに保存される
8. event/trade/position/price更新が1transactionで完結する
9. transaction途中失敗時にpartial dataが残らない
10. SQLiteファイルがGitHubへcommitされない
11. 既存のpaper/alert動作を壊さない
12. APIキー、秘密鍵、シードフレーズをDBへ保存しない

---

# 19. 今回の実装で意図的に行わないもの

- SQLAlchemy
- Alembic
- PostgreSQL
- Redis
- 外部リアルタイム価格API
- FOMO非公式API解析
- FOMOスクレイピング
- FOMOブラウザ自動操作
- 実資金注文
- ウォレット秘密鍵保存

これらをPhase 2初期実装へ混ぜず、まずローカルSQLiteによる状態永続化を完成させる。

---

# 20. 実装後のユーザー体験

現状:

```text
Alice BUY
↓
Fが20個paper保有
↓
Uvicorn停止
↓
20個が消える
```

Phase 2後:

```text
Alice BUY
↓
SQLiteへ保存
↓
Fが20個paper保有
↓
Uvicorn停止
↓
Mac再起動
↓
F再起動
↓
GET /paper/positions
↓
20個が残っている
```

さらに同じイベントを再送しても:

```text
duplicate_event
```

となり、取引が二重計上されない。

この状態を `database.py` / `storage.py` 実装の最初の到達点とする。
