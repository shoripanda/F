# Architecture

## Goal

FOMOで利用者自身がフォローしたトレーダーの取引イベントだけを受け取り、同方向の売買シグナルをペーパートレードまたは確認用アラートとして処理する。

## Why live auto-trading is intentionally absent

2026-09-09時点で確認したFOMOの公開仕様では、フォローしたトレーダーの取引をリアルタイムで確認・通知できる一方、利用者が最終的に取引を判断する方式になっている。またFOMO Terms of Serviceは、許可されていないボット・自動スクリプトによるアカウント操作、取引実行、スクレイピング等を禁止している。

このため本プロジェクトでは以下を禁止する。

- FOMOのログイン情報・セッションCookieの取得
- Playwright/Selenium等によるFOMO画面の自動操作
- FOMO内部APIのリバースエンジニアリング
- FOMOページ/フィードの無許可スクレイピング
- FOMO上での実資金注文の自動確定
- シードフレーズ・秘密鍵の保存

FOMOが将来、第三者アプリ向けの公式API/SDKと自動取引権限を公開した場合は、利用規約と権限範囲を再確認してから別アダプタとして追加する。

## Components

### 1. Signal Source

現在は `POST /events` に正規化済みイベントを投入する。

想定される安全な入力元:

- 利用者が手動で入力した取引
- 利用者自身が管理する通知転送
- FOMOが明示的に許可した公式API/SDK（将来）

### 2. Follower Filter

`config.json` の `followed_traders` と完全一致する `trader_id` のみ受理する。

フォローしていないユーザーのシグナルは `trader_not_followed` としてペーパーポートフォリオへ反映しない。拒否イベント自体は監査ログとしてSQLiteへ保存する。

### 3. Deduplication

`event_id` を一度処理したら、同じイベントを再度適用しない。

v0.2.0以降はSQLiteの `events.event_id` をPRIMARY KEYとして永続的に重複防止する。UvicornやMacの再起動後も重複判定は維持される。

### 4. Paper Engine

BUY:

- `paper_buy_usd` をイベント価格で割り、仮想数量を計算
- `(trader_id, chain, token_address)` 単位で仮想ポジションをSQLiteへ保存
- `paper_trades` に取引履歴を保存

SELL:

- `sell_fraction` があればその割合だけ仮想売却
- 未指定ならそのトレーダー由来の仮想ポジションを100%売却
- 売却額、売却対象取得原価、実現損益を保存

これはシミュレーション専用であり実注文を送信しない。

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

`mode=alert` の場合は注文を行わず、次の情報だけを返す。

- trader
- BUY / SELL
- chain
- token address
- observed price

利用者が内容を確認し、実際の取引は自分で行う。フォロー対象のalertイベントと最終観測価格は監査用にSQLiteへ保存する。

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

### Phase 2 — in progress

SQLite永続化の詳細設計は [`PERSISTENCE_DESIGN.md`](PERSISTENCE_DESIGN.md)、実装設計は [`SQLITE_IMPLEMENTATION_DESIGN.md`](SQLITE_IMPLEMENTATION_DESIGN.md) を参照する。

実装済み:

- SQLite persistence
- restart-safe event deduplication
- audit log
- persistent paper positions
- paper trade history
- realized PnL storage
- last observed token price storage
- database schema versioning
- transaction rollback foundation
- `GET /events`
- `GET /paper/trades`

次の対象:

- unrealized PnL calculation
- `GET /paper/pnl`
- per-trader paper performance analytics
- webhook authentication
- notification delivery (Telegram/Discord/email)
- stale-signal detection

### Phase 3 — only if officially permitted

- official FOMO API adapter
- official OAuth/API authentication
- official webhook ingestion

実資金の注文機能は、FOMO側の公式な第三者自動取引手段と利用条件が確認できるまでは追加しない。
