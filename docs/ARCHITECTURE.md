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

フォローしていないユーザーのシグナルは `trader_not_followed` として無視する。

### 3. Deduplication

`event_id` を一度処理したら、同じイベントを再度適用しない。

本番運用ではインメモリSetではなくSQLite/PostgreSQL等へ移行する。

### 4. Paper Engine

BUY:

- `paper_buy_usd` をイベント価格で割り、仮想数量を計算
- `(trader_id, chain, token_address)` 単位で仮想ポジションを保存

SELL:

- `sell_fraction` があればその割合だけ仮想売却
- 未指定ならそのトレーダー由来の仮想ポジションを100%売却

これはシミュレーション専用であり実注文を送信しない。

### 5. Alert Mode

`mode=alert` の場合は注文を行わず、次の情報だけを返す。

- trader
- BUY / SELL
- chain
- token address
- observed price

利用者が内容を確認し、実際の取引は自分で行う。

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

### Phase 1 — current

- follower allowlist
- normalized event schema
- event deduplication
- paper BUY/SELL
- alert mode
- REST API

### Phase 2

- SQLite persistence
- audit log
- webhook authentication
- notification delivery (Telegram/Discord/email)
- per-trader paper performance analytics
- stale-signal detection

### Phase 3 — only if officially permitted

- official FOMO API adapter
- official OAuth/API authentication
- official webhook ingestion

実資金の注文機能は、FOMO側の公式な第三者自動取引手段と利用条件が確認できるまでは追加しない。
