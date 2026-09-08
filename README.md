# F — FOMO Follow Trader Assistant

FOMOでフォローしているトレーダーの売買を追跡し、対象ユーザーのシグナルだけを処理するための実験用プロジェクトです。

## 現在のスコープ

このリポジトリでは次の2モードを扱います。

- `paper`: 実資金を使わず、フォロー対象ユーザーのBUY/SELLを仮想ポートフォリオへ反映
- `alert`: フォロー対象ユーザーの取引を検知し、利用者が確認できる注文候補を生成

**FOMOアカウントへの自動ログイン、ブラウザ自動操作、スクレイピング、実資金の自動注文は実装しません。** FOMOが公式に許可したAPIを提供した場合のみ、`FomoSignalSource` アダプタを公式API実装へ差し替えられる設計にします。

## 想定フロー

```text
FOMOでフォローするユーザーを選択
        ↓
許可された方法で取引イベントを取得/入力
        ↓
FollowerFilter（フォロー対象だけ通す）
        ↓
SignalNormalizer（BUY/SELL、chain、token、時刻を統一）
        ↓
┌───────────────┬────────────────┐
│ paper mode    │ alert mode     │
│ 仮想売買       │ 確認用候補を生成 │
└───────────────┴────────────────┘
        ↓
監査ログ保存
```

## クイックスタート

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.json config.json
uvicorn fomo_follower.app:app --reload
```

イベント投入例:

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

`config.json` に登録されていない `trader_id` のイベントは無視されます。

## 重要

- フォロー対象は利用者が自分で設定します。
- トレーダーの順位や銘柄をこのシステムが推薦することはしません。
- 実取引の判断・発注は利用者自身が行います。
- APIキー、秘密鍵、シードフレーズをこのリポジトリへ保存しないでください。

詳細は [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) を参照してください。
