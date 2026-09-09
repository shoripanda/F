# Architecture

## Goal

FOMO profile `https://fomo.family/profile/Hiroki` を利用者の基準アカウントとして扱い、利用者自身が選んだフォロー対象トレーダーの取引シグナルを受け取り、`paper`、`alert`、`live` の3モードで処理する。

`live` では、FOMO画面を自動操作するのではなく、利用者が接続した公式の取引所・DEX・ウォレットAPIを `LiveExecutor` 経由で利用して実注文へ接続する。

## FOMO integration boundary

FOMOからのfollow一覧・取引シグナル自動取得は、FOMOが公式に許可したAPI/Webhook/SDK等が利用可能な場合だけ実装する。

実装しないもの:

- FOMOログイン情報・session cookieの取得/保存
- Playwright/Selenium等によるFOMO画面の自動操作
- FOMO内部APIのリバースエンジニアリング
- FOMOページ/フィードの無許可スクレイピング
- FOMO上でのbotによる実注文
- seed phrase/private keyのGitHub保存

FOMO側に公式integrationが提供された場合、SignalSource adapterとして追加する。

## Target flow

```text
FOMO profile: Hiroki
        ↓
Officially permitted signal source
        ↓
OpenClaw / agent / manual input
        ↓
POST /events
        ↓
Follower Filter
        ↓
FollowerEngine
   ├─ paper → SQLite paper portfolio
   ├─ alert → review candidate
   └─ live  → Risk Gate → LiveExecutor
                              ↓
                    official trading provider
                              ↓
                         real order
```

## Components

### 1. Signal Source

現在は `POST /events` に正規化済みイベントを投入する。

想定入力元:

- 利用者が手動で入力した取引
- 利用者自身が管理する通知転送
- OpenClaw等が生成する正規化イベント
- FOMOが明示的に許可した公式API/Webhook/SDK（将来）

### 2. Follower Filter

`config.json` の `followed_traders` と完全一致する `trader_id` のみ処理対象とする。

将来FOMO公式API等でfollow一覧取得が許可された場合は `FollowerSyncAdapter` で同期できる構成にする。

### 3. Deduplication

SQLiteの `events.event_id` をPRIMARY KEYとして永続的に重複防止する。

再起動後も同一event IDを二重適用しない。

### 4. Paper Engine

BUY:

- `paper_buy_usd` をイベント価格で割り仮想数量を計算
- `(trader_id, chain, token_address)` 単位で仮想ポジションを保存

SELL:

- `sell_fraction` があればその割合を売却
- 未指定なら100%売却

SQLiteへ取引履歴、取得原価、実現損益、最終観測価格を保存する。

### 5. Persistence

状態の正本はSQLiteとする。

```text
FollowerEngine
      ↓
SQLiteStore
      ↓
Database
      ↓
data/fomo_follower.sqlite3
```

保存テーブル:

- `events`
- `paper_positions`
- `paper_trades`
- `token_prices`
- `schema_meta`

SQLiteの書き込みは1イベント単位のtransactionで行い、途中失敗時はrollbackする。

### 6. Alert Mode

注文せずreview候補だけ返す。

### 7. Live Mode

`Mode.LIVE` では `Risk Gate` を通過したイベントだけ `LiveExecutor` へ渡す。

現時点のcore risk checks:

- live enabled
- kill switch
- executor configured
- stale signal rejection
- token allowlist
- max order USD
- max slippage parameter

初期状態は必ずfail-closed。

```text
live.enabled = false
kill_switch = true
executor = null
```

実際のprovider adapterを接続しない限り注文を送らない。

### 8. LiveExecutor

`src/fomo_follower/executor.py` にprovider-independent interfaceを置く。

```text
FollowerEngine
      ↓
LiveExecutor
      ↓
Provider Adapter
      ↓
Official exchange / DEX / wallet API
```

FOMOブラウザ画面をexecutorとして利用しない。

### 9. OpenClaw

OpenClawはオーケストレーション層として利用できる。

責務候補:

- 許可されたsignal sourceからイベントを受信
- TradeEventへ正規化
- `/events` へ送信
- execution result監視
- kill switch操作
- 通知

サーバー側のRisk GateはOpenClawから独立して強制し、agentが金額上限等を迂回できないようにする。

## Event schema

```json
{
  "event_id": "evt-001",
  "trader_id": "alice",
  "side": "buy",
  "chain": "solana",
  "token_address": "TOKEN_MINT",
  "price_usd": 1.25,
  "executed_at": "2026-09-09T00:00:00Z",
  "source": "manual",
  "sell_fraction": null
}
```

## Roadmap

### Phase 1 — complete

- follower allowlist
- normalized event schema
- paper BUY/SELL
- alert mode
- REST API

### Phase 2 — persistence

実装済み:

- SQLite persistence
- restart-safe event deduplication
- audit log
- persistent paper positions
- paper trade history
- realized PnL storage
- last observed token price storage
- schema versioning
- transaction rollback foundation
- `GET /events`
- `GET /paper/trades`

詳細:

- [`PERSISTENCE_DESIGN.md`](PERSISTENCE_DESIGN.md)
- [`SQLITE_IMPLEMENTATION_DESIGN.md`](SQLITE_IMPLEMENTATION_DESIGN.md)

### Phase L1 — live core

実装済み:

- `Mode.LIVE`
- FOMO profile metadata
- live risk config
- `LiveExecutor` interface
- fail-closed default executor
- basic Risk Gate

### Phase L2 — next

- 実行providerを選定
- provider official API adapter
- environment/secret manager credentials
- `live_orders` / `live_executions` / `live_positions`
- daily notional enforcement
- idempotent order submit
- fill reconciliation
- sandbox/testnet validation where available

### Phase L3

- authenticated OpenClaw endpoint
- webhook authentication
- kill-switch command
- monitoring/notification

### Phase L4 — FOMO official integration only

- official FOMO signal adapter
- official follow-list sync
- official webhook/API ingestion

詳細:

- [`LIVE_TRADING_DESIGN.md`](LIVE_TRADING_DESIGN.md)
