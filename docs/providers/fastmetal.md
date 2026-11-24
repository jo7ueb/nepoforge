# FastMetal プロバイダー設定 (#7)

Agent Workbench の既定プロバイダーとして FastMetal を利用するための設定と確認記録。
秘密情報 (API キー) はこの文書にもリポジトリにも書かない。環境変数または OpenCode の標準の認証機能で管理する。

- 確認日: **2026-11-24**
- 運営: 株式会社国際調達情報 (PSI-Japan, Inc.)
- サービス: 複数の生成 AI モデルを単一 API キーで利用するゲートウェイ
- 公式: [fastmetal.jp](https://fastmetal.jp/) / [料金](https://fastmetal.jp/pricing) / [API ドキュメント](https://fastmetal.jp/docs/api/getting-started) / [利用規約](https://fastmetal.jp/legal/terms) (2026-10-28 制定)

## 1. API の仕様 (公式ドキュメント確認済み)

| 項目 | 内容 |
| --- | --- |
| プロトコル | OpenAI 互換 (`/v1/chat/completions` ほか)。Anthropic 互換 (`/v1/messages`) も提供 |
| エンドポイント | `https://api.fastmetal.ai/v1` |
| 認証 | `Authorization: Bearer <APIキー>`。キーはダッシュボードで生成し、再表示されないため利用者側で保管する |
| ツール呼び出し | OpenAI 互換 API の tool calling に対応。Agent Workbench の全エージェント検証 (V1–V8、task 委譲・edit・shell) がこの API で動作済み |
| 単価 | モデルごとに料金ページに表示。日本円建て・消費税抜き。前払残高制 |

## 2. OpenCode への設定方法 (秘密情報なし)

FastMetal は OpenCode の内蔵カタログ (models.dev) には含まれないため、`providers` に OpenAI 互換プロバイダーとして定義する。設定ファイルにキーを書かないこと。どちらの方法でも OpenCode の標準機能のみを使う。

### 方法 A (推奨): OpenCode 標準の認証機能

```sh
opencode auth login   # プロバイダーで FastMetal を選び、API キーを貼る
```

キーは OpenCode のローカルな認証情報ストアに保存され、リポジトリや設定ファイルには現れない。

### 方法 B: 環境変数

```sh
export FASTMETAL_API_KEY="..."   # ダッシュボードで生成したキー
```

`providers.fastmetal.env` に環境変数名を宣言すると、OpenCode が認証情報として解決する ([Providers ドキュメント](https://opencode.ai/docs/providers/) の `env` フィールド)。

### 設定例 (`opencode.jsonc`、キーは含めない)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "fastmetal": {
      "name": "FastMetal",
      "env": ["FASTMETAL_API_KEY"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "https://api.fastmetal.ai/v1"
      },
      "models": {
        "japan-glm-5.3-flash": { "name": "GLM 5.3 Flash (JP)" },
        "beam-1.0": { "name": "Beam 1.0" }
      }
    }
  }
}
```

- `models` には利用するモデル ID を列挙する。一覧は `opencode models` で確認できる。
- `env` と `opencode auth login` の併用も可能 (ストアの認証情報が優先される)。

## 3. モデル一覧と確認日

`opencode models` で確認できた FastMetal モデル (**2026-11-24 時点**、OpenCode v2.4.0):

> anthropic-claude-haiku-5-5, anthropic-claude-opus-5-5, anthropic-claude-sonnet-5-5,
> beam-1.0, deepseek-v4-pro, deepseek-v4.1-flash-1017, devstral-2512, gemini-3.8-flash,
> gemma-4-26b-a4b-it, glm-5.3, glm-5.3-flash, glm-5.3-flashx, gpt-6.1-sol,
> japan-deepseek-v4.1-flash-1017, japan-gemma-4-31b, japan-glm-5.3-flash,
> japan-glm-5.3-nvfp4, japan-kimi-k2.7-code, japan-qwen3.6-35b, japan-qwen3.8-27b,
> kimi-k3.1-pro, qwen3.8-max, qwen4-max

Agent Workbench が採用したモデルと単価 (¥/100万トークン、入力/出力、2026-11-24 時点) は
[設計メモ 0001 §3](../design/0001-agents-profiles-permissions.md) に記録した。
選定方針: 同一モデルの `japan-` 付き (国内ホスト) 版が安い・同額なら `japan-` 版を採用。
ただし `japan-qwen3.8-27b` のようにグローバル版より高い逆例もあるため、毎回料金表で比較する。

## 4. 学習利用とデータ保持 (利用規約の確認結果、2026-11-24 時点)

FastMetal 利用規約 (2026-10-28 制定) の要旨。正文と最新の表示を必ず確認すること。

- **FastMetal 自体は API 利用の推論データ (入力・出力) を保存・記録・閲覧しない** (第 14 条第 2 項)。保存するのは利用履歴 (モデル・トークン数・日時・料金。内容は含まない) のみ。規約上、FastMetal 自身がモデル学習に推論データを利用することもない (第 13 条第 4 項)。
- **上流事業者の扱いはモデル規約次第** (第 6 条・第 14 条)。上流事業者には推論データを保存・学習に利用する事業者も含まれ、FastMetal は各上流の「処理国 / 保存の有無 / 学習利用の有無」を表示し、利用者側で送信先を絞り込むルーティング設定を提供する。ルーティング設定をしない場合、推論データはどの国の上流にも送信され得る。
- `japan-` 付きモデルは日本国内でホスト・実行される (料金ページの表示)。機密性の高い入力がある場合は `japan-` モデル + ルーティング設定を推奨。
- **無料モデルは入力を学習に利用される場合がある** (第 11 条)。機密情報を入力しないこと。
- ブラウザのチャット機能は推論データが FastMetal のサーバーに保存される (第 14 条の 2)。API 利用とは異なるので注意。
- FastMetal は上流事業者の表明の正確性を保証しない (第 6 条第 6 項)。機密データの扱いが厳密に必要な案件では、プロンプトに含める情報自体を最小化する (architecture.md §5)。

## 5. 動作確認

- 選定モデルの実機動作は設計メモ [0001 §5](../design/0001-agents-profiles-permissions.md) の V1–V8 (task 委譲・権限・プロファイル切替を含む) と、#8 の /nfgo 試験 (設計メモ 0002) で確認済み。
- 費用の確認は §3 の単価表に基づく (fastmetal の cost フィールドは常に 0 のため、費用評価は価格テーブルで行う。#12 で正式対応)。

## 6. 残存課題

- 上流事業者・ルーティング設定はアカウント側の設定であり、本リポジトリでは管理しない。機密案件ごとに利用者が設定する。
- 第 2 プロバイダー対応は #19。本設定例は FastMetal 固有。
