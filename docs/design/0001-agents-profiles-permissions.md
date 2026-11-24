# 設計メモ: 基本エージェント・モデルプロファイル・権限制御

- Issue: [#5](https://github.com/jo7ueb/nepoforge/issues/5) / [#6](https://github.com/jo7ueb/nepoforge/issues/6) / [#10](https://github.com/jo7ueb/nepoforge/issues/10)
- 作成日: 2026-11-24
- 前提: [調査報告 #4](../investigate/0001-opencode-agent-delegation.md)(OpenCode v2.4.1)、[architecture.md](../architecture.md)
- 検証環境: OpenCode v2.4.0/2.4.1 (Linux)、FastMetal プロバイダー

## 1. スコープと設計原則の対応

| 要求 | 実装 |
| --- | --- |
| OpenCode の標準機能を最大限活用 | エージェント定義・権限・モデル割当はすべて OpenCode の標準機構 (`.opencode/agents/*.md` + frontmatter)。独自オーケストレーター・独自フレームワークは導入しない |
| 役割とモデル選択の分離 | 役割のプロンプト・権限は `.opencode/agents/<role>.md`、モデル割当だけを `profiles/<name>.json` に置き、`scripts/switch_profile.py` が `model:` 行だけを差し替える |
| 既存の OpenCode 環境を破壊しない | 5 職種すべて `mode: subagent`。primary エージェント (`build`)・グローバル設定 (`~/.config/opencode`) は変更しない |
| プロンプトによる指示と技術的強制の区別 | 読み取り専用・編集禁止・委譲禁止は permissions で強制。プロンプト本文は職務の手順と報告契約のみを記述する |
| 過剰な抽象化を避ける | ビルド・生成なし。切替は 1 つの Python スクリプト (標準ライブラリのみ) |

## 2. 役割定義 (`.opencode/agents/`)

AGENTS.md の役割分担に対応する 5 職種。すべて `mode: subagent` で、primary から `task`/`subagent` ツールで委譲されて初めて動く。

| 役割 | 職務 | 編集 | shell | 子委譲 | Web |
| --- | --- | --- | --- | --- | --- |
| architect | 調査結果から設計をまとめる | `docs/**` のみ | 否 | 否 | 可 |
| expert | コード・公式資料の調査 (出典付き) | 否 | 否 | 否 | 可 |
| explore | リポジトリ内の探索・現状把握 | 否 | 否 | 否 | 否 |
| implementer | 実装・修正・テスト | 可 (`.opencode/**` 以外) | 個別承認 (`ask`) | 否 | 可(既定) |
| reviewer | 独立レビュー | 否 | `git diff/log/status/show` のみ | 否 | 否 |

permissions の記述方針 (重要):

- ルールは**後勝ち (last matching rule wins)**。広いルールを先に、例外を後に書く。
- 読み取り専用役割は `{ "*": deny }` を最初に置き、許可を後に列挙する (未一致は deny に落ちる)。
- implementer は逆に `{ edit: "*": allow }` を先に、`{ edit: ".opencode/**": deny }` を後に置く。
  - 検証 V3 で「deny → 広い allow」の順序では deny が allow に上書きされる失敗を確認し、V3c で修正後の順序が技術的に強制されることを確認した。
- implementer は agent 個別ルールがグローバルより後に適用されるため、`git push *` / `gh pr merge*` / `gh repo delete*` の deny を agent 内にも再掲して先頭に置く (グローバルの deny が agent の `shell: ask` で上書きされないようにする)。

技術的強制の根拠 (実機検証):

- deny されたツールはカタログから不可視化される (explore は「shell ツールが存在しない」と認識)。
- リソース一致で deny された編集は `Permission denied: edit` で失敗する (V3c)。

## 3. モデルプロファイル (`profiles/`)

- `economy` / `balanced` (既定) / `performance` の 3 種。役割 → `provider/model` の対応のみを記述する。
- コミット済みの `.opencode/agents/*.md` の `model:` 行は balanced と一致させる。
- `scripts/switch_profile.py`:
  - 役割の過不足・モデル表記 (`provider/model`)・`opencode models` との照合を検証し、不明モデル・不足役割は明確なエラーで停止する (部分的な適用を起こさない)。
  - `--check` で適用状況の検証のみ (CI 向け)。`--models` で利用可能モデル一覧を表示。
  - `profiles/local.json` は gitignore 済みの個人上書きプロファイル。**グローバル設定 (`~/.config/opencode`) には一切書き込まない**ため、ユーザーの個人設定は保持される。
- モデル ID の選定は FastMetal の料金表 ([fastmetal.jp/pricing](https://fastmetal.jp/pricing)、2026-11-24 時点) に基づく。単価 (¥/100万トークン、入力/出力):

| モデル | 入力 | 出力 | 採用プロファイル (役割) |
| --- | --- | --- | --- |
| `japan-glm-5.3-flash` | ¥17.64 | ¥58.8 | economy (全役割)、balanced (判断系 4 役)、performance (explore) |
| `beam-1.0` | ¥67.2 | ¥201.6 | balanced (implementer) — コーディング・エージェント特化 501B MoE、出力単価が `japan-kimi-k2.7-code` (¥530) より 62% 安い |
| `glm-5.3` | ¥235.2 | ¥739.2 | performance (architect / reviewer) |
| `japan-deepseek-v4.1-flash-1017` | ¥33.6 | ¥134.4 | performance (expert) — グローバル版と同額のため🇯🇵国内ホスト版を選択 |
| `kimi-k3.1-pro` | ¥336 | ¥1,680 | performance (implementer) — フラッグシップ推論。同価格帯の `gpt-6.1-sol` (¥336/¥1,680) や `kimi-k3` (¥352.8/¥1,764) より安い |

選定方針:

- 同一モデルの `japan-` 付き (国内ホスト) 版が安い、または同額の場合は `japan-` 版を選ぶ (例: `japan-glm-5.3-flash` は `glm-5.3-flash` より 30% 安)。逆に `japan-qwen3.8-27b` (¥84/¥504) はグローバル版 (¥33.6/¥420) より高いため使わない。
- 実装役は出力トークンが支配的なため出力単価を優先して比較する。
- 詳細な品質比較は #14 の比較評価で見直す。fastmetal の cost フィールドが常に 0 のため費用評価は別途価格テーブルが必要 (#12)。

## 4. 権限制御 (`#10`)

2 層で構成する:

1. **リポジトリ共通 (`.opencode/opencode.jsonc` の `permissions`)**: 全エージェント (primary 含む) に適用。
   - deny: `git push --force*` / `git push -f*` / `git push * --delete*` / `git push * :refs*` / `git branch -D*` / `gh pr merge*` / `gh repo delete*` / `shell での .env* 読み取り`
   - ask: `git reset --hard*` / `git clean*` / `gh release delete*`
   - 無承認マージは `gh pr merge` の deny と Human-on-the-Loop 運用 (AGENTS.md) の両面で抑止する。
2. **役割別 (各エージェント定義の `permissions`)**: §2 のマトリクスどおり。

信頼境界との対応 (architecture.md §5):

- Issue 本文等の指示で permissions を変更させない → implementer は `.opencode/**` を編集できない (技術的強制)。ただし **primary エージェント (`build`) は制限していない**ため、人間が管理するセッションでの変更は可能 (このリポジトリ自身の開発に必要。残存リスクとして §6 に記録)。
- 秘密情報は環境変数・標準認証機能で管理し、設定ファイルに書かない。

## 5. 検証結果 (実機)

検証は `.sandbox` ではなく `/tmp/opencode/issue5610-test/` (コミット対象外) で実施。子セッションの agent/model はメッセージ API で機械可読に確認した。

| # | 検証 | 手法 | 結果 | 証拠 |
| --- | --- | --- | --- | --- |
| V1 | explore への委譲と親子分離 | `opencode run` から委譲 | **子セッション agent=explore, model=glm-5.3-flash** | `ses_df248cbeeffeP4rbxHS4rleCIZ` |
| V2b | reviewer に編集を指示 | reviewer 子セッションに追記を指示 | **ファイル無編集** (reviewer が職務規約に基づき拒否) | `ses_df24721f0ffelnlQ61MFpQU5DA` |
| V2d | reviewer の技術的強制 | reviewer を primary として shell/edit を指示 | **`Permission denied` で不発、ファイル無編集** | `ses_df246a1fcffeF0vlIEQH1AYdGo` 付近 |
| V3/V3b | implementer による `.opencode/**` 編集 | deny を先に書いた初期版で実施 | **失敗例**: 広い `edit: "*": allow` が後勝ちで deny を上書きし編集成功 → ルール順序の誤りとして修正 | `ses_df2460b6effeAU0lOdTG7puEtB` |
| V3c | 修正後の implementer | 同試験を再実施 | **`Permission denied: edit` で編集不可** | 修正後の implementer.md |
| V4 | 共通ガードレール | primary から `git push --force origin main` を実行 | **`Permission denied: shell` で不発** | `ses_...` (V4-forcepush-deny) |
| V5 | architect・expert の委譲 | architect に docs/design-note.md 作成、expert に要約を指示 | **両方とも子セッションで実行され、architect の docs 編集は成功** (許可 `/docs/**` の確認) | `ses_df242224bffeVfGkqLDP0IOcQF`, `ses_df241f473ffe5dVgb9HLxo1Rde` |
| V7 | プロファイル切替の実効性 | sandbox で `performance` を適用し reviewer を起動 | **子セッション model=glm-5.3 に変化** (切替前は glm-5.3-flash) | `ses_df2418830ffem0LgkNrP44S9W7` |
| V8 | 再選定後の balanced implementer (`beam-1.0`) | sandbox で implementer にファイル作成を指示 | **子セッション agent=implementer, model=beam-1.0 でファイル作成成功** | `ses_df22e2ec4ffeehC7kkJJ9hQZHV` |
| E1–E4 | スクリプトのエラー系 | 不明プロファイル / 不明モデル / 不足役割 / 過剰役割 | **すべて明確なエラーメッセージで非 0 終了** | コマンド実行ログ |

完了条件の対応:

- **#5**: 5 職種が呼び出せる (V1/V2/V3/V5)、実装とレビューが独立 (別セッション + reviewer 編集不可)、親への報告が動作 (V1/V5 の報告)。
- **#6**: 3 プロファイル切替可能 (V7)、不明モデル・不足役割は明確なエラー (E1–E4)、個人設定を保持 (スクリプトは `~/.config/opencode` に触れない)。
- **#10**: 許可・拒否の試験 (V2–V5)、レビュー役は変更不可 (V2b/V2d)、無承認 merge 禁止 (`gh pr merge` deny + 運用ルール)、制約と残存リスクを記録 (§6)。

## 6. 制約・残存リスク

1. **`ask` の非対話挙動は未検証** (調査報告 §4 の未確認事項を引き継ぎ)。`implementer` の `shell: ask` は対話 (TUI) を前提とし、`opencode run` での無人実行時の挙動は #20 の前に要確認。
2. **primary エージェント (`build`) は制限対象外**。信頼できる人間が操作するセッションであり、`.opencode/**` の編集・権限変更は可能。悪意ある入力から守る対象は「委譲された子エージェント」である。
3. **deny は理由のログを残しにくい** (ツール不可視化の挙動、調査報告 §4.4)。権限違反の監査は #12 の実行履歴記録で補完する。
4. **本リポジトリ自身では `.opencode/**` の編集を禁止していない** (product のソースだから)。導入先リポジトリで運用する場合、そのリポジトリの implementer は編集できない。
5. shell のガードレールはコマンド文字列パターンであり、迂回 (エイリアス・スクリプト経由) は原理的に可能。OpenCode 公式ドキュメントも「shell はホストユーザーの権限で動くため、狭い allowlist を推奨」と明記している。GitHub 側の保護 (branch protection 等) と併用する (#10 の GitHub 権限面)。
6. モデル ID は fastmetal に依存した選定 (§3 の料金表ベース)。品質比較は #14、第 2 プロバイダー対応は #19。

## 7. 今後の参照

- `/nfgo` コマンド (#8) は本設計の 5 職種を委譲順 (explore/expert → architect → implementer → reviewer) で呼び出すワークフローとして実装する。
- 設定検証と CI (#11) は `switch_profile.py --check` を流用できる。
