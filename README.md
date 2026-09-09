# F — FOMO Follow Trader Assistant

FOMOでフォローしているトレーダーの売買シグナルを、利用者が指定したフォロー対象だけに限定して処理し、ペーパートレード・アラート・将来の実取引へつなぐためのプロジェクトです。

基準FOMOプロフィール:

```text
https://fomo.family/profile/Hiroki
```

## モード

- `paper`: 実資金を使わず、フォロー対象ユーザーのBUY/SELLを仮想ポートフォリオへ反映
- `alert`: フォロー対象ユーザーの取引を検知し、利用者が確認できる注文候補を生成
- `live`: Risk Gateを通過したシグナルを `LiveExecutor` へ渡し、公式に自動取引を許可する取引所・DEX・ウォレットAPIへ実注文を送るためのモード

v0.2.0から、ペーパートレードの状態はSQLiteへ永続保存されます。

保存対象:

- 受信イベント
- 重複判定用event ID
- 仮想保有残高
- ペーパー取引履歴
- 売却時の実現損益
- 最終観測価格

## FOMOとの接続方針

FOMOはシグナル源として扱います。FOMOプロフィール `Hiroki` のfollow対象や取引シグナルを自動取得するのは、FOMOが公式に許可したAPI/Webhook/SDK等が利用できる場合に限ります。

FOMOへの無許可の自動ログイン、スクレイピング、Playwright/Selenium等によるアカウント操作・自動取引は実装しません。

実際の注文執行はFOMO画面ではなく、利用者が明示的に接続した、第三者自動取引を許可する公式の取引所・DEX・ウォレットAPIアダプタを通じて行います。

## OpenClawなどのAIエージェント

OpenClaw等は次のオーケストレーションに利用する想定です。

```text
FOMOの許可されたsignal source
        ↓
OpenClaw / AI agent
        ↓
POST /events
        ↓
FollowerEngine
        ↓
Risk Gate
        ↓
LiveExecutor
        ↓
Official exchange / DEX / wallet API
        ↓
実注文
```

AIエージェント単独でrisk limitを迂回しないよう、金額上限・token allowlist・signal freshness・slippage・kill switchはF側で強制します。

## Live modeの初期状態

`config.example.json` にはlive設定がありますが、初期状態では意図的に注文できません。

```json
{
  "mode": "paper",
  "fomo_profile_url": "https://fomo.family/profile/Hiroki",
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

`Mode.LIVE` と `LiveExecutor` interfaceは追加済みです。実資金で注文するには、利用する公式execution providerを決め、そのprovider adapterを実装・接続する必要があります。未設定時はfail-closedでブロックされます。

## Paper modeの想定フロー

```text
取引イベント
        ↓
FollowerFilter
        ↓
FollowerEngine
        ↓
SQLite transaction
   ├─ events
   ├─ paper_trades
   ├─ paper_positions
   └─ token_prices
        ↓
COMMIT
```

## クイックスタート

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.json config.json
uvicorn fomo_follower.app:app --reload
```

起動後:

```text
http://127.0.0.1:8000/docs
```

を開くとSwagger UIから操作できます。

## SQLite

デフォルトのDBファイル:

```text
data/fomo_follower.sqlite3
```

別の場所へ保存したい場合:

```bash
export FOMO_FOLLOWER_DB_PATH=/path/to/fomo_follower.sqlite3
uvicorn fomo_follower.app:app --reload
```

`.gitignore` で `*.sqlite3` と `*.db` は除外しているため、利用者の取引履歴DBそのものはGitHubへcommitしません。

## API

### `GET /health`

起動状態、モード、SQLite利用状態、フォロー対象を確認します。

### `POST /events`

取引イベントを投入します。

例:

```bash
curl -X POST http://127.0.0.1:8000/events \
  -H 'content-type: application/json' \
  -d '{
    "event_id":"evt-001",
    "trader_id":"alice",
    "side":"buy",
    "chain":"solana",
    "token_address":"TOKEN_MINT",
    "price_usd":1.25,
    "executed_at":"2026-09-09T00:00:00Z",
    "source":"manual"
  }'
```

`config.json` に登録されていない `trader_id` は処理対象になりません。拒否結果自体は監査用イベントとしてSQLiteへ保存されます。

### `GET /events`

受信済みイベント履歴を確認します。

### `GET /paper/positions`

現在の仮想保有残高をSQLiteから取得します。

### `GET /paper/trades`

ペーパー取引履歴を取得します。

## テスト

```bash
pip install -e '.[test]'
pytest
```

## Secrets

API key、秘密鍵、seed phrase、FOMOパスワード、session cookieをGitHubへ保存しないでください。LiveExecutorの認証情報は環境変数またはsecret managerから注入する設計とします。

## 詳細設計

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/PERSISTENCE_DESIGN.md`](docs/PERSISTENCE_DESIGN.md)
- [`docs/SQLITE_IMPLEMENTATION_DESIGN.md`](docs/SQLITE_IMPLEMENTATION_DESIGN.md)
- [`docs/LIVE_TRADING_DESIGN.md`](docs/LIVE_TRADING_DESIGN.md)
