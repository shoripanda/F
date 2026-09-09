# Live Trading Design — FOMO signals + OpenClaw + official execution adapters

## 1. Goal

FOMO profile `https://fomo.family/profile/Hiroki` を利用者の基準アカウントとして扱い、利用者がフォロー対象として指定したトレーダーの売買シグナルをFへ取り込み、条件を満たす場合に実資金の暗号資産売買まで到達できる構成を定義する。

OpenClaw等のAIエージェントは、シグナルの受け渡し、状態確認、監視、実行要求のオーケストレーションに利用できる。

ただし、FOMO自体への自動ログイン、無許可スクレイピング、無許可の自動アカウント操作・自動取引は行わない。FOMOからのデータ取得は、FOMOが公式に許可したAPI/Webhook/SDK等が存在する場合のみ自動接続する。

実売買は、利用者が明示的に接続した、第三者自動取引を許可する公式の取引所・DEX・ウォレットAPIアダプタを通じて行う。

---

## 2. Target architecture

```text
FOMO profile: Hiroki
        ↓
followed trader list / trade signal
        ↓
Officially permitted signal source
(API / webhook / user-managed forwarding)
        ↓
OpenClaw / other agent
        ↓
POST /events
        ↓
FollowerEngine
        ↓
Follower allowlist
        ↓
Risk Gate
 ├─ kill switch
 ├─ max order USD
 ├─ daily notional limit
 ├─ token allowlist
 ├─ signal freshness
 └─ max slippage
        ↓
LiveExecutor
        ↓
Official exchange / DEX / wallet API
        ↓
On-chain / exchange order
        ↓
execution result
        ↓
SQLite audit log
```

FOMOは「シグナルの出所」として扱い、注文執行先とは分離する。

---

## 3. Modes

### paper

実資金を使用しない。既存のSQLiteペーパーポートフォリオへBUY/SELLを反映する。

### alert

売買候補だけを返し、注文を送信しない。

### live

Risk Gateを通過したシグナルを `LiveExecutor` へ渡し、実際の注文送信を試みる。

`live` は設定ファイルに書いただけでは動作しない。以下がすべて必要。

1. `mode = live`
2. `live.enabled = true`
3. `live.kill_switch = false`
4. `live.executor` が設定済み
5. 実装済みLiveExecutorが注入されている
6. token allowlist等のリスク条件を通過

どれか1つでも満たさなければfail-closedで注文を送信しない。

---

## 4. FOMO account

基準プロフィール:

```text
https://fomo.family/profile/Hiroki
```

設定:

```json
{
  "fomo_profile_url": "https://fomo.family/profile/Hiroki"
}
```

このURLは利用者の基準アカウントを示すメタデータとして保持する。

FはFOMOのパスワード、セッションCookie、秘密鍵等を保存しない。

### Followed trader synchronization

将来FOMOが公式API等でfollow一覧を提供した場合:

```text
FOMO official follow list
        ↓
FollowerSyncAdapter
        ↓
local followed_traders
```

へ同期できるようにする。

現時点で公式の第三者向けfollow APIが利用できない場合は、`followed_traders` を利用者が設定する。

---

## 5. OpenClaw role

OpenClawは取引所APIキーの保管場所そのものにはしない。

想定責務:

- 許可されたsignal sourceの確認
- 正規化したTradeEventの生成
- `POST /events` への送信
- `/health`、`/events`、execution結果の監視
- 異常時のkill switch有効化
- 利用者への通知

OpenClawから送るイベント例:

```json
{
  "event_id": "provider-unique-id",
  "trader_id": "followed-trader-id",
  "side": "buy",
  "chain": "solana",
  "token_address": "TOKEN_MINT",
  "price_usd": 1.25,
  "executed_at": "2026-09-09T00:00:00Z",
  "source": "official-fomo-adapter"
}
```

F側でfollow対象、鮮度、金額、allowlist、slippage等を再検証する。AIエージェントの判断だけでリスク制約を迂回できない構造にする。

---

## 6. LiveExecutor

`src/fomo_follower/executor.py` に実行境界を置く。

```python
class LiveExecutor(Protocol):
    name: str
    def execute(self, request: LiveOrderRequest) -> LiveOrderResult: ...
```

プロバイダ固有実装例:

```text
executors/
├── coinbase.py
├── kraken.py
├── jupiter.py
└── other_official_provider.py
```

実際に利用するプロバイダは、利用者が利用可能で、公式にAPI自動取引を許可しているものから選択する。

FOMO画面をPlaywright/Seleniumで操作するexecutorは作らない。

---

## 7. Secrets

禁止:

- API keyをconfig.jsonへ保存
- private keyをGitHubへ保存
- seed phraseをFへ保存
- FOMO login password/cookieを保存

実行プロバイダの認証情報は環境変数またはOS/secret managerから注入する。

例:

```text
EXECUTOR_API_KEY
EXECUTOR_API_SECRET
EXECUTOR_WALLET_KEY_REF
```

ウォレット秘密鍵を直接扱う必要がある方式より、権限制限可能なAPI、専用サブアカウント、専用ウォレット、送金不可キー等を優先する。

---

## 8. Risk Gate

初期設定は必ず安全側とする。

```json
{
  "live": {
    "enabled": false,
    "executor": null,
    "max_order_usd": 25.0,
    "max_daily_notional_usd": 100.0,
    "max_slippage_bps": 100,
    "stale_signal_seconds": 60,
    "require_token_allowlist": true,
    "allowed_tokens": [],
    "kill_switch": true
  }
}
```

### max_order_usd

1注文の最大USD額。

### max_daily_notional_usd

24時間またはUTC日単位の総発注上限。次フェーズでSQLiteのexecution ledgerと連携して強制する。

### max_slippage_bps

executorへ渡す最大slippage。providerがslippage上限をサポートしない場合、そのadapterはlive利用不可とする方針を推奨する。

### stale_signal_seconds

古いコピー取引を防止する。現在時刻との差が閾値を超えたイベントは拒否する。

### token allowlist

初期状態は空。明示的に許可したtokenだけ実取引対象にできる。

### kill_switch

最優先の停止スイッチ。trueの間はlive注文を送信しない。

---

## 9. BUY / SELL mapping

### BUY

follow ruleの `live_buy_usd` を利用する。

```text
followed trader BUY
        ↓
configured live_buy_usd
        ↓
max_order_usd check
        ↓
executor BUY
```

対象トレーダーの元注文額をそのままコピーする設計ではなく、利用者側で固定または別途定義した上限内の金額を使う。

### SELL

イベントの `sell_fraction` を利用する。

```text
sell_fraction = 0.5
→ F側の該当live positionの50%を売却
```

実装にはlive position ledgerが必要なため、executor provider実装と同時に `live_positions` / `live_executions` をSQLiteへ追加する。

SELLで `sell_fraction` 未指定の場合の扱いは100%を標準とするが、provider adapter実装前にテストで固定する。

---

## 10. Persistence extension for live trading

現在の `paper_trades` と混在させない。

将来追加:

```text
live_orders
live_executions
live_positions
```

`live_orders`:

- event_id
- provider
- requested side
- requested notional/fraction
- max slippage
- status
- provider order id
- tx hash
- submitted_at

`live_executions`:

- order id
- fill quantity
- fill price
- fee
- execution timestamp

`live_positions`:

- provider/account scope
- chain/token
- quantity
- cost basis

これによりpaper PnLとlive PnLを分離する。

---

## 11. Required implementation phases

### Phase L1 — done in core

- `Mode.LIVE`
- live config
- FOMO profile metadata
- `LiveExecutor` interface
- fail-closed `DisabledLiveExecutor`
- basic stale-signal/token allowlist/max-order/kill-switch checks

### Phase L2 — next

- 利用する実行先を1つ決定
- provider公式API仕様確認
- provider adapter実装
- API credentialsはenvironment/secret managerから取得
- sandbox/testnetがあれば先に接続
- `live_orders` / `live_executions` schema追加
- idempotency key対応
- daily notional enforcement
- actual fill reconciliation

### Phase L3

- OpenClaw agent contract
- authenticated `/agent/events` endpoint
- webhook signature/token authentication
- alerting
- kill switch command
- reconciliation loop

### Phase L4 — FOMO official integration only when permitted

- FOMO official API/webhook/SDK adapter
- official followed-trader sync
- official trade signal intake

---

## 12. Acceptance criteria for real-money release

実資金を有効化する前に最低限以下を満たす。

- provider official APIのみ利用
- API keyをrepoに保存しない
- withdrawals権限を無効化可能なら無効化
- event idempotency
- stale signal rejection
- max order enforced server-side
- daily notional enforced server-side
- token allowlist
- max slippage
- kill switch
- DB audit log
- order/fill reconciliation
- restart recovery
- failure/timeout tests
- paperまたはsandboxで一連のテスト完了

FOMO側の規約・API条件が変わる可能性があるため、FOMO signal adapterを実装する際はその時点の公式条件を再確認する。
