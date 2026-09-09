# F — FOMO Follow Trader Assistant

FOMOでフォローしているトレーダーの売買シグナルを、利用者が指定したフォロー対象だけに限定して処理するための実験用プロジェクトです。

## 現在のスコープ

このリポジトリでは次の2モードを扱います。

- `paper`: 実資金を使わず、フォロー対象ユーザーのBUY/SELLを仮想ポートフォリオへ反映
- `alert`: フォロー対象ユーザーの取引を検知し、利用者が確認できる注文候補を生成

v0.2.0から、ペーパートレードの状態はSQLiteへ永続保存されます。

保存対象:

- 受信イベント
- 重複判定用event ID
- 仮想保有残高
- ペーパー取引履歴
- 売却時の実現損益
- 最終観測価格

**FOMOアカウントへの自動ログイン、ブラウザ自動操作、スクレイピング、実資金の自動注文は実装しません。** FOMOが公式に許可したAPIを提供した場合のみ、公式な入力アダプタとして追加できる設計にします。

## 想定フロー

```text
FOMOでフォローするユーザーを選択
        ↓
許可された方法で取引イベントを取得/入力
        ↓
FollowerFilter（フォロー対象だけ通す）
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

`config.json` に登録されていない `trader_id` はペーパーポートフォリオへ反映されません。拒否結果自体は監査用イベントとしてSQLiteへ保存されます。

### `GET /events`

受信済みイベント履歴を確認します。

### `GET /paper/positions`

現在の仮想保有残高をSQLiteから取得します。

UvicornやMacを再起動してもDBファイルが残っていれば状態は維持されます。

### `GET /paper/trades`

ペーパー取引履歴を取得します。

SELLでは以下も保存します。

- 売却数量
- 売却額
- 売却対象取得原価
- 実現損益

## 永続化の確認例

```text
Alice BUY
↓
20 token保有
↓
Uvicorn停止
↓
再起動
↓
GET /paper/positions
↓
20 tokenが残っている
```

同じ `event_id` を再起動後に再送した場合も、SQLiteの `events.event_id` によって `duplicate_event` として拒否されます。

## テスト

```bash
pip install -e '.[test]'
pytest
```

永続化テストでは、DBを再生成せずEngine/Storeだけ作り直し、再起動相当の状態復元と重複防止を確認します。

## 重要

- フォロー対象は利用者が自分で設定します。
- トレーダーの順位や銘柄をこのシステムが推薦することはしません。
- 実取引の判断・発注は利用者自身が行います。
- APIキー、秘密鍵、シードフレーズをこのリポジトリへ保存しないでください。
- SQLite DBには利用者の取引履歴が含まれるため、GitHubへ手動追加しないでください。

詳細設計:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/PERSISTENCE_DESIGN.md`](docs/PERSISTENCE_DESIGN.md)
- [`docs/SQLITE_IMPLEMENTATION_DESIGN.md`](docs/SQLITE_IMPLEMENTATION_DESIGN.md)
