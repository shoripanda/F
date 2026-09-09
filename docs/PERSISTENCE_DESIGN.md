# Persistence Design — SQLiteによる取引履歴・保有残高・損益の永続化

## 1. 目的

現在の `FollowerEngine` は、処理済みイベントIDとペーパーポジションをPythonプロセスのメモリ上に保持している。

- `seen_event_ids: set[str]`
- `positions: dict[(trader_id, chain, token_address), PaperPosition]`

この方式ではUvicornの再起動、Macの再起動、プロセスクラッシュなどで以下が失われる。

- 処理済みイベントID
- 仮想保有残高
- 取引履歴
- 実現損益
- 含み損益算出に必要な価格情報

Phase 2では、これらをSQLiteへ永続化し、再起動後も状態を復元できる構成へ移行する。

本設計はペーパートレードと監査ログの永続化だけを対象とし、実資金の自動注文機能は追加しない。

---

## 2. 設計方針

### 2.1 EngineとStorageを分離する

売買ルールとDB処理を同じクラスに混在させない。

```text
POST /events
    ↓
FollowerEngine
    ↓
SQLiteStore
    ↓
SQLite
```

役割を次のように分離する。

### FollowerEngine

担当:

- フォロー対象判定
- BUY/SELL判定
- 売買数量計算
- 取得原価計算
- 実現損益計算
- alert/paperモード判定

担当しない:

- SQL
- DB接続管理
- テーブル作成
- DBトランザクション実装詳細

### SQLiteStore

担当:

- イベント重複確認
- イベント保存
- ポジション取得・更新
- ペーパー取引保存
- 最終観測価格保存
- 取引履歴取得
- PnL集計用データ取得
- トランザクション管理

この分離により、将来PostgreSQLへ移行する場合も `FollowerEngine` を変更せずStorage実装だけ交換できる形を目指す。

---

## 3. ファイル構成

Phase 2完了時の想定構成:

```text
src/fomo_follower/
├── __init__.py
├── app.py
├── config.py
├── models.py
├── engine.py
├── database.py
├── storage.py
└── pnl.py
```

### `database.py`

- SQLite接続
- PRAGMA設定
- スキーマ初期化
- トランザクション用context manager

### `storage.py`

- Repository/Store層
- CRUD
- 重複イベント確認
- 取引履歴・ポジション・価格保存

### `pnl.py`

- 実現損益
- 含み損益
- 合計損益
- trader別集計

### `engine.py`

- 既存の売買ルールを維持
- メモリの `seen_event_ids` / `positions` 依存を削除
- `SQLiteStore` を介して状態を取得・保存

### `app.py`

- FastAPI endpoint
- Store/Engine初期化
- 履歴・PnL参照API追加

---

## 4. SQLiteファイル

デフォルト:

```text
data/fomo_follower.sqlite3
```

環境変数で変更可能にする。

```text
FOMO_FOLLOWER_DB_PATH=data/fomo_follower.sqlite3
```

既存 `.gitignore` は `*.sqlite3` と `*.db` を除外しているため、DB本体はGitHubへcommitしない。

必要なら `data/.gitkeep` のみGitHubへ保存する。

---

## 5. SQLite接続設定

初期実装はPython標準ライブラリ `sqlite3` を使用する。

理由:

- 現状のコード量ではSQLAlchemy/Alembicは過剰
- 依存関係を増やさず実装可能
- SQLiteからPostgreSQLへ移行する場合もStorage interfaceを維持できる

接続時に以下を設定する。

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

目的:

- 外部キー有効化
- 読み取りと書き込みの競合を減らす
- FastAPI利用時のSQLite lock耐性を改善

---

## 6. 数値型の方針

暗号資産では小数桁が大きいため、長期的には `float` を避ける。

Python側:

```python
Decimal
```

SQLite側:

```text
TEXT
```

として保存する。

例:

```text
"0.00000000123456789"
"25.00"
"1.50000000"
```

対象:

- price_usd
- quantity
- notional_usd
- cost_basis_usd
- realized_pnl_usd

初回実装で既存API互換性を優先する場合は、Pydantic入力はfloatを受け付け、Storageへ渡す直前に `Decimal(str(value))` へ変換する。

将来の破壊的変更時にPydantic modelそのものをDecimalへ移行する。

---

## 7. テーブル設計

## 7.1 `events`

Fが受信したイベントの監査ログ。

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
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 目的

1. 再起動後も重複イベントを拒否する
2. 受信したイベントを監査できる
3. 拒否したイベントも理由付きで記録可能にする

### event_id

`PRIMARY KEY` とし、同一event_idの二重適用をDBレベルでも防止する。

---

## 7.2 `paper_positions`

現在の仮想保有残高。

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

現在のEngineが使っているキー:

```text
(trader_id, chain, token_address)
```

をそのまま複合主キーに採用する。

### BUY

既存数量に追加。

```text
new_quantity = current_quantity + bought_quantity
new_cost_basis = current_cost_basis + paper_buy_usd
```

### SELL

売却割合に応じて残数量と残取得原価を減らす。

```text
sold_quantity = current_quantity × sell_fraction
released_cost_basis = current_cost_basis × sell_fraction
remaining_quantity = current_quantity - sold_quantity
remaining_cost_basis = current_cost_basis - released_cost_basis
```

0数量になった行は削除せず残すか削除するかを実装時に選べるが、初期実装では履歴参照の簡潔さから0行を保持してもよい。

API側では `quantity > 0` だけ返すオプションを追加可能。

---

## 7.3 `paper_trades`

実際にペーパーポートフォリオへ適用された売買履歴。

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

### BUY時

```text
quantity = paper_buy_usd / price_usd
notional_usd = paper_buy_usd
cost_basis_released_usd = NULL
realized_pnl_usd = NULL
```

### SELL時

```text
quantity = sold_quantity
notional_usd = quantity × price_usd
cost_basis_released_usd = current_cost_basis × sell_fraction
realized_pnl_usd = notional_usd - cost_basis_released_usd
```

`event_id UNIQUE` により1イベントから複数回ペーパートレードが生成されることを防ぐ。

---

## 7.4 `token_prices`

最後に観測した価格を保存する。

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

Phase 2初期版では外部の価格APIへ接続しない。

したがってこのテーブルの価格は、

```text
現在価格
```

ではなく、

```text
last observed price
```

として扱う。

API名も `last_observed_price_usd` とし、リアルタイム価格と誤認させない。

将来的に公式価格APIを追加した場合、このStoreを拡張する。

---

## 8. トランザクション設計

1イベントの処理は必ず1つのDBトランザクション内で完結させる。

```text
BEGIN
  ↓
重複event_id確認
  ↓
フォロー対象確認
  ↓
events INSERT
  ↓
現在position SELECT
  ↓
Paper trade計算
  ↓
paper_trades INSERT
  ↓
paper_positions UPSERT
  ↓
token_prices UPSERT
  ↓
COMMIT
```

途中で1つでも失敗した場合:

```text
ROLLBACK
```

これにより、以下の不整合を防ぐ。

```text
trade historyには売却済み
しかしpositionは売却前
```

または、

```text
eventは処理済み
しかしpaper_tradeが保存されていない
```

---

## 9. event重複処理

現在は `seen_event_ids` setで重複を防いでいるが、これを廃止する。

新方式:

```text
SQLite events.event_id PRIMARY KEY
```

を唯一の永続的な重複判定ソースとする。

処理フロー:

```text
POST /events
   ↓
store.event_exists(event_id)
   ↓
YES → duplicate_event
NO  → 続行
```

最終的な安全網としてDBのPRIMARY KEY制約も利用する。

これによりサーバー再起動後にも同じevent_idを二重適用しない。

---

## 10. 損益計算

## 10.1 実現損益

SELL時に確定する。

例:

```text
BUY
20 token × $1.25 = $25.00

SELL 50%
10 token × $1.50 = $15.00
```

売却対象の取得原価:

```text
$25.00 × 0.5 = $12.50
```

実現損益:

```text
$15.00 - $12.50 = +$2.50
```

式:

```text
realized_pnl_usd
= sell_notional_usd
- cost_basis_released_usd
```

---

## 10.2 含み損益

残ポジションに対して計算する。

```text
market_value
= quantity × last_observed_price

unrealized_pnl
= market_value - cost_basis_usd
```

last observed priceが存在しない場合は、含み損益を `null` とする。

0として扱わない。

---

## 10.3 合計損益

```text
total_pnl
= realized_pnl + unrealized_pnl
```

unrealizedが算出不能の場合はtotalも `null` とするか、API上で `realized_pnl_usd` と `unrealized_pnl_usd` を明示的に分離する。

初期実装では後者を採用する。

---

## 11. PnL集計単位

最低限、以下をサポートする。

### 全体

```text
全followed trader
```

### trader別

```text
alice
bob
```

### trader + token別

```text
alice / solana / TOKEN
```

これにより将来、

```text
どのフォロー対象の成績がよかったか
```

をペーパートレード上で分析できる。

システムがフォロー対象を推薦する機能は追加しない。

---

## 12. API設計

現在:

```text
GET  /health
POST /events
GET  /paper/positions
```

Phase 2で以下を追加する。

---

### `GET /paper/positions`

DBから現在ポジションを取得するよう変更。

例:

```json
[
  {
    "trader_id": "alice",
    "chain": "solana",
    "token_address": "TEST_TOKEN",
    "quantity": 10,
    "cost_basis_usd": 12.5,
    "last_observed_price_usd": 1.5,
    "market_value_usd": 15,
    "unrealized_pnl_usd": 2.5
  }
]
```

---

### `GET /paper/trades`

取引履歴。

Query parameter候補:

```text
trader_id
chain
token_address
side
limit
offset
```

デフォルトは新しい順。

---

### `GET /paper/pnl`

損益集計。

例:

```json
{
  "realized_pnl_usd": 2.5,
  "unrealized_pnl_usd": 2.5,
  "market_value_usd": 15,
  "remaining_cost_basis_usd": 12.5
}
```

任意で `trader_id` フィルタを受け付ける。

---

### `GET /paper/summary`

ダッシュボード向け概要。

例:

```json
{
  "open_positions": 1,
  "trade_count": 2,
  "buy_notional_usd": 25,
  "sell_notional_usd": 15,
  "remaining_cost_basis_usd": 12.5,
  "market_value_usd": 15,
  "realized_pnl_usd": 2.5,
  "unrealized_pnl_usd": 2.5
}
```

---

### `GET /events`

監査用イベント履歴。

Query parameter候補:

```text
trader_id
accepted
reason
limit
offset
```

APIキーや秘密情報は保存・返却しない。

---

## 13. Config拡張

既存 `config.json` の互換性を維持する。

新しいDBパスは設定ファイルではなく、まず環境変数で指定する。

```text
FOMO_FOLLOWER_DB_PATH=data/fomo_follower.sqlite3
```

理由:

- ローカル/本番でパスを変更しやすい
- config schemaへの影響が小さい
- DBファイルをGitHubへ誤って固定しない

将来必要なら `AppConfig.storage` を追加する。

---

## 14. Engine interface変更案

現在:

```python
FollowerEngine(config)
```

変更後:

```python
FollowerEngine(config=config, store=store)
```

概念例:

```python
store = SQLiteStore(db_path)
engine = FollowerEngine(load_config(), store)
```

`engine.positions` と `engine.seen_event_ids` は削除する。

代わりに:

```python
store.get_position(...)
store.event_exists(...)
store.list_positions(...)
```

を利用する。

---

## 15. Storage interface案

```python
class Store(Protocol):
    def event_exists(self, event_id: str) -> bool: ...
    def save_event(self, ...): ...

    def get_position(self, trader_id, chain, token_address): ...
    def upsert_position(self, ...): ...
    def list_positions(self, ...): ...

    def save_paper_trade(self, ...): ...
    def list_paper_trades(self, ...): ...

    def upsert_last_price(self, ...): ...
    def get_last_price(self, chain, token_address): ...

    def pnl_summary(self, trader_id=None): ...
```

初期実装ではProtocolを必須にしなくてもよいが、責務としてこの境界を維持する。

---

## 16. Paper BUY処理

```text
1. event重複確認
2. follower確認
3. 現在position取得
4. quantity = paper_buy_usd / price
5. 新position計算
6. events保存
7. paper_trades保存
8. position UPSERT
9. last observed price UPSERT
10. COMMIT
```

BUYは実現PnLを発生させない。

---

## 17. Paper SELL処理

```text
1. event重複確認
2. follower確認
3. 現在position取得
4. positionなし → quantity 0 action
5. fraction決定
6. sold quantity計算
7. released cost basis計算
8. proceeds計算
9. realized PnL計算
10. 残position計算
11. events保存
12. paper_trades保存
13. position UPSERT
14. last observed price UPSERT
15. COMMIT
```

式:

```text
fraction = sell_fraction or 1
sold_quantity = current_quantity × fraction
released_cost_basis = current_cost_basis × fraction
proceeds = sold_quantity × price
realized_pnl = proceeds - released_cost_basis
```

---

## 18. Alert mode

`mode=alert` でもイベント監査ログは保存する。

ただし以下は作成しない。

- `paper_trades`
- `paper_positions` 更新

イベントには:

```text
accepted = true
reason = followed_trader_signal_requires_user_review
```

を保存する。

これによりalert modeでも再起動後の重複防止が可能になる。

---

## 19. 拒否イベント

フォロー対象外や不正な処理についても、可能な範囲で監査ログを残す。

例:

```text
reason = trader_not_followed
reason = duplicate_event
```

ただし `duplicate_event` は同じevent_idで新しい行をINSERTできないため、初期実装では既存eventを返して終了する。

将来、全リクエスト履歴が必要なら別の `event_attempts` テーブルを追加する。

Phase 2初期版では不要。

---

## 20. DB初期化

アプリ起動時:

```text
FastAPI startup
   ↓
DB directory作成
   ↓
SQLite connect
   ↓
CREATE TABLE IF NOT EXISTS
   ↓
engine/store初期化
```

既存データを削除する処理は起動時に行わない。

---

## 21. スキーマバージョン

将来のmigrationに備え、最初からSQLiteの `PRAGMA user_version` を利用する。

例:

```sql
PRAGMA user_version = 1;
```

初期実装:

```text
schema version 1
```

将来:

```text
1 → 2
```

へ移行する場合は `database.py` に小規模migrationを追加する。

Alembic導入はDB構造が複雑化した時点で再検討する。

---

## 22. 既存データの扱い

現在メモリ上にあるポジションは、アプリ終了時に消える。

Phase 2導入時に既存メモリ状態を自動移行する機能は作らない。

理由:

- 現在のデータはテスト用
- 安全に再現可能
- 不完全なメモリ状態を永続DBへ混入させない

Phase 2導入後に新しく投入されたeventからSQLiteを正とする。

---

## 23. テスト設計

既存テスト:

- 未フォロー拒否
- duplicate event拒否
- trader別position分離
- partial sell
- alert mode

を維持し、SQLite版テストを追加する。

### 必須テスト

#### 1. 再起動後position保持

```text
Engine A
BUY
↓
DB close
↓
Engine B
↓
同じDBをopen
↓
positionが残る
```

#### 2. 再起動後duplicate拒否

```text
event-001処理
↓
再起動
↓
event-001再送
↓
duplicate_event
```

#### 3. BUY → 50% SELL PnL

```text
BUY $25 @ $1.25
quantity = 20

SELL 10 @ $1.50
proceeds = 15
released cost = 12.5
realized PnL = +2.5
remaining quantity = 10
remaining cost = 12.5
```

を厳密に検証する。

#### 4. 全売却

```text
sell_fraction = 1.0
```

でquantityとcost basisが0になる。

#### 5. Alice/Bob分離

同じtokenでもtrader_id別に混ざらない。

#### 6. Transaction rollback

paper_trade INSERT後に意図的な例外を発生させ、positionやeventが中途半端にcommitされないことを確認。

#### 7. Alert mode

イベントは保存されるが、paper_trade/positionは作成されない。

#### 8. DBファイル除外

`.gitignore` が `.sqlite3` / `.db` を除外していることを維持する。

---

## 24. API互換性

Phase 2では現在の利用方法を壊さない。

維持する:

```text
POST /events
GET /health
GET /paper/positions
```

特にSwagger `/docs` から現在と同じ方法でイベントを投入できることを必須とする。

レスポンスの既存フィールド:

```text
accepted
mode
reason
event
paper_action
review_action
```

も維持する。

新しいPnLフィールドを追加する場合も、既存クライアントを壊さない追加形式にする。

---

## 25. 監査性

すべての時刻はUTC ISO 8601で保存する。

例:

```text
2026-09-09T00:10:00Z
```

区別する時刻:

- `executed_at`: 元イベントの取引時刻
- `created_at`: FがDBへ保存した時刻
- `updated_at`: positionの最終更新時刻
- `observed_at`: 価格を観測した時刻

---

## 26. セキュリティ

SQLiteには以下を保存しない。

- FOMOパスワード
- セッションCookie
- 取引所API secret
- ウォレット秘密鍵
- シードフレーズ

DBにはペーパートレード・監査データだけを保存する。

またSQLiteファイルそのものもGitHubへpushしない。

---

## 27. 実装順序

### Step 1

`database.py`

- DB path
- connection
- PRAGMA
- schema creation
- user_version

### Step 2

`storage.py`

- event exists/save
- position get/upsert/list

### Step 3

`engine.py`

- `seen_event_ids` を削除
- `positions` を削除
- Store経由へ変更

### Step 4

`paper_trades`

- BUY/SELL履歴保存

### Step 5

実現PnL

- released cost basis
- realized PnL

### Step 6

`token_prices`

- last observed price
- unrealized PnL

### Step 7

API追加

```text
GET /paper/trades
GET /paper/pnl
GET /paper/summary
GET /events
```

### Step 8

SQLite再起動テストとrollbackテスト

---

## 28. 完了条件

Phase 2 SQLite persistenceは、以下をすべて満たした時点で完了とする。

- サーバー再起動後もポジションが残る
- サーバー再起動後も同一event_idを二重適用しない
- BUY履歴が残る
- SELL履歴が残る
- 部分売却後の残数量が正しい
- 実現PnLが正しい
- last observed priceを使った含みPnLを取得できる
- trader別に履歴・position・PnLが分離される
- SQLite transactionにより部分的な書き込みが起きない
- 現在のSwagger `/docs` からの操作方法が維持される
- DBファイルがGitHubへcommitされない

---

## 29. 今回のテストケースを基準データにする

手動で確認済みの以下のケースをPhase 2の基準テストとする。

```text
Trader: alice
Token: TEST_TOKEN
Chain: solana

BUY
price = $1.25
paper notional = $25
quantity = 20

SELL 50%
price = $1.50
sold quantity = 10
proceeds = $15
released cost basis = $12.50
realized PnL = +$2.50

Remaining
quantity = 10
cost basis = $12.50
last observed price = $1.50
market value = $15
unrealized PnL = +$2.50
```

このケースを自動テスト化し、SQLite導入後も現在確認済みの挙動を維持する。

---

## 30. Phase 2後の拡張候補

SQLite永続化完了後に検討する。

- trader別パフォーマンス表示
- 日次・週次・月次PnL
- equity curve
- 勝率
- 平均利益/平均損失
- stale signal検出
- Telegram/Discord/email通知
- webhook認証
- DBバックアップ

FOMO側の公式な第三者API/SDKが提供されない限り、FOMOアカウントの自動操作・スクレイピング・実資金自動売買は対象外のままとする。
