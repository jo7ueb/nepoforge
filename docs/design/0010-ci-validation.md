# 設定ファイルの自動検証と CI の整備 (#11)

2026-11-26

## 背景

設定変更による破損 (frontmatter・権限ルール・プロファイル・テンプレート) を防ぎ、全体の回帰試験を
AI エージェントの判断・待機から切り離す。個々のエージェントは担当範囲の最小限の試験だけを行い、
PR の最終差分に対する全体検証は GitHub Actions に集約する。

## 方針

### 1. `nepoforge validate` (課金なし・決定的)

同梱データ `nepoforge/data/` (#57 の唯一の正本) を検査し、問題が 1 件でもあれば終了コード 1 で失敗する。
問題は `{kind, path, message}` の形で報告する。ネットワーク・API は使わない。

| 種別 | 検査内容 |
| --- | --- |
| agents | frontmatter の存在・既知キーのみ・`description`・`mode` (primary/subagent/all)・`model` の表記・本文の非空。管理対象 5 職種の存在 |
| permissions | 各ルールの `action` / `resource` / `effect` と未知キー |
| commands | frontmatter の `description`・本文・管理対象 `/nfgo` の存在 |
| role-references | エージェント・コマンドから未定義の役割名 (`implementer` 等)・コマンド (`/nfgo`) を参照していないこと |
| profiles | `profiles.load_profile_config` の検証 (候補・タスク規則・制約・Primary)。役割モデルと task_rules のモデルが候補に登録されていること |
| routes | `model-routes.json` のキーがモデル表記で、値のキー・列挙値・`approval_ref` が妥当であること。プロファイルの候補にルートがあること |
| templates | guardrail が有効な JSONC でなければならないこと、管理マーカーが対になっていること、`gh pr merge*` 等の deny があること。provider テンプレートはオブジェクト内側の断片として解析 |
| data-policy | 値の集合が `open` / `private` / `confidential` (既定 `confidential`) であること |

frontmatter は PyYAML を追加依存にせず、`key: value` と `- field: value` だけの最小サブセットとして解析する
(`nepoforge/validate.py: parse_block_scalars`)。

### 2. GitHub Actions (`.github/workflows/ci.yml`)

- トリガ: `pull_request` (draft を含む) と `master` への `push`。
- `concurrency` で同じ PR の古い実行を取り消し、最新差分を優先する (`cancel-in-progress`)。
- `permissions: contents: read`、各ジョブに `timeout-minutes`。秘密情報・実 API・有料モデルは使わない。
- `test` ジョブ: Python 3.11 (pyproject の下限) と最新の 2 版のマトリクスで `nepoforge validate` と
  `python -m unittest discover -s tests -t .`。
- `package` ジョブ: `python -m build` で wheel と sdist を作り、wheel を隔離 venv に導入して
  `--version`・同梱データ・`init` / `status` / `uninstall` を確認する (配布設定の整合性)。

チェック名は `test` と `package`。required status check にする場合はリポジトリのブランチ保護・Ruleset へ
追加するが、**実際の変更は人間の明示承認の下で行う**。

### 3. 検証の責務分離

`AGENTS.md` (基本原則・開発フロー・検証の責務分離)、配布する `/nfgo` (3.5・3.6)、`implementer` / `reviewer`
の役割定義、README を更新した。要点:

- implementer は変更に直接関係する最小限の試験のみ。CI のあるリポジトリでは全体検証を CI に委ねる。
- reviewer は差分・テストの妥当性・受理済みの結果を確認し、必要な場合だけ限定的な追加検証を要求する。
- Coordinator (primary) は各子と同じテストや全テストを既定で再実行せず、検証範囲の選定と統合を担う。
- CI の pending・未実行・失敗を成功としない。CI 待ちの子・有料ポーリングは行わない。
- CI がない・利用不能・切り分けに必要・明示的要求の場合に限りローカルで全体を実行し、理由を記録する。

## 影響範囲

- 新規: `nepoforge/validate.py`、`nepoforge/cli.py` の `validate` サブコマンド、`tests/test_validate.py`、
  `.github/workflows/ci.yml`。
- 文書: `AGENTS.md`、`README.md`、`ROADMAP.md`、`nepoforge/data/commands/nfgo.md`、
  `nepoforge/data/agents/implementer.md`、`nepoforge/data/agents/reviewer.md`。本メモ。
- 既存テストは変更・削除しない。新しいテストフレームワークも導入しない。

## 代替案

- 外部リンター (actionlint 等) の導入: 追加依存・利用枠を避けるため見送り。YAML の形は CI 自体の実行で確認する。
- PyYAML で frontmatter を解析: 依存最小化 (#47) の方針に合わせ、最小サブセットのパーサを自前で持つ。
- マトリクスを全 Python 版に拡大: Actions の利用枠を考慮し、下限と最新の 2 版だけにする。

## 既知の限界

- `validate` は同梱データ (パッケージの正本) を検査する。利用者の `~/.config/opencode` の改変は検査しない。
- 生成した派生エージェント (`nf-*`) の内容は `nepoforge profile derive --dry-run` で確認する (既存の仕組み)。
- YAML サブセットは OpenCode のエージェント・コマンド定義で使う範囲に限る。複雑な YAML (複数行文字列・アンカー等) は扱わない。
- CI が実際に GitHub 上で通ることは、PR の push 後に確認する。ローカルでは workflow の実行は検証できない。
