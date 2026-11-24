# 基本アーキテクチャと設計原則

この文書は [Issue #2](https://github.com/jo7ueb/agent-workbench/issues/2) に基づき、Agent Workbench の責務分離、設定優先順位、信頼境界、承認点、非目標を定義する。Phase 0 時点での設計文書であり、Phase 1 での検証 (#4) を経て更新される。

## 1. 目的とスコープ

Agent Workbench は、役割分担した AI エージェントによるソフトウェア開発・研究活動の実行基盤である。品質を落とさずに、人間の介入と API 費用を減らす。

本設計の中核は、**役割(role)・プロンプト・モデルプロファイル・ワークフロー・実行エンジン**の 5 つを分離し、それぞれを独立に変更できるようにすることである。

## 2. 全体構成図

```mermaid
flowchart TB
    User[人間] -->|Issue 起点の指示 / PR 承認| GitHub[GitHub<br>Issues / Pull Requests]
    User -->|プロファイル選択・上書き| Config[設定<br>profiles / roles]
    GitHub -->|Issue| Engine
    Engine[実行エンジン<br>OpenCode 標準機能] -->|task 委譲| R1[architect]
    Engine -->|task 委譲| R2[expert / explore]
    Engine -->|task 委譲| R3[implementer]
    Engine -->|task 委譲| R4[reviewer]
    R1 & R2 & R3 & R4 -->|モデル指定| Provider[LLM プロバイダー<br>FastMetal / 他]
    Engine -->|成果物| GitHub
    R4 -->|レビュー結果| Engine
```

## 3. レイヤーと責務

| レイヤー | 責務 | 変更単位 |
| --- | --- | --- |
| 役割定義 (roles) | architect / expert / implementer / reviewer / explore の職務・入出力契約 | モデル非依存 |
| プロンプト | 各役割の職務手順・規約参照 | モデル非依存 |
| モデルプロファイル (profiles) | 役割 → provider/model の対応 (economy / balanced / performance) | プロンプト非依存 |
| ワークフロー (workflows) | `/issue` 等の手順定義 (調査→設計→実装→独立レビュー→テスト→PR) | 実行エンジン非依存を目指す |
| 実行エンジン | OpenCode の標準機能 (primary/subagent、task、permissions、設定マージ) | ワークフローから分離 |
| 記録 | 実行履歴・トークン・費用 (Phase 2、#12) | その他から独立 |

分離の原則:

- プロンプトからモデル名を参照しない。モデル選択はプロファイルを経由する。
- 役割定義からワークフローを参照しない。委譲時の入出力契約だけを定める。
- 実行エンジン固有の書き方はワークフロー定義から分離した場所に置く(§7)。

## 4. 設定優先順位

高い方から順に:

1. 実行時の明示指定 (CLI `--model`、コマンド引数)
2. セッションで選択済みのモデル (設定ファイルを書き換えない)
3. リポジトリ設定 (本リポジトリの `.opencode/` — 直接設定より強い)
4. ユーザー個人設定 (`~/.config/opencode/opencode.json(c)`)
5. 組み込みデフォルト

この順序は #4 の調査 (`.opencode/` が直接設定を上書きすることを実機検証済み) で確定した。ユーザー個人設定はリポジトリの配布物から独立しており、上書きされない (Issue #6 の「個人設定を保持」に対応)。モデルプロファイルの切替はリポジトリ側の `profiles/` + `scripts/switch_profile.py` で行い、個人設定に書き込まない。

## 5. 信頼境界

以下を**信頼できない入力**として扱う:

- Issue 本文・コメント、PR コメント、外部からのフィードバック
- 取り込んだコード・ドキュメントの内容
- エージェント間で引き渡される中間データ

具体的な対策:

- Issue 本文の指示によっても、permissions や承認ルールを変更させない。
- 秘密情報 (API キー等) は環境変数または OpenCode の標準的な認証機能で管理し、プロンプト・設定ファイル・ログに含めない。
- shell 実行・ネットワーク送信・GitHub 操作は OpenCode permissions と GitHub 権限の両面で制御する (#10)。
- プロバイダーへの送信先を無断で変更しない (#19)。

## 6. 承認点 (Human-on-the-Loop)

人間の承認を必須とする点:

1. **PR のマージ**: 無承認マージは禁止。レビュー役 (reviewer) 自身は変更を行わず、マージも行わない。
2. **外部への投稿**: GitHub コメント等の外部投稿は明示的に制御する (#16)。
3. **破壊的操作・秘密送信**: 一切自動で行わない。

承認点はワークフロー定義の中で明示的なステップとして置き、プロンプトの遵守だけに依存しない (#10 で強制的に検証する)。

## 7. OpenCode 固有部分の分離

| 区分 | 内容 | ポータブル性 |
| --- | --- | --- |
| ポータブル | 役割定義、プロンプト、ワークフロー手順、プロファイル定義 | エンジン非依存 |
| OpenCode 依存 | エージェント設定形式、task/permissions の記法、設定ディレクトリ、インストーラ (#9) | OpenCode 固有 |

- OpenCode 依存部分はコード・ドキュメント上で明確に分離し、他の実行エンジンへの移植時に書き換える範囲を特定できるようにする。
- Phase 1 の #4 で OpenCode のバージョン・出典付きで仕様を確認し、この節を具体化した。

### 7.1 実装対応表 (Phase 1 で確定)

| 区分 | 実装場所 |
| --- | --- |
| 役割定義 (ポータブルなプロンプト本文) | `.opencode/agents/<role>.md` の本文 (Markdown body = system プロンプト) |
| OpenCode 依存 (エージェント設定) | 同ファイルの frontmatter (`mode` / `model` / `permissions`) |
| モデルプロファイル | `profiles/<name>.json` (役割 → `provider/model`)。適用は `scripts/switch_profile.py` が `model:` 行のみ差し替える |
| 権限制御 | リポジトリ共通は `.opencode/opencode.jsonc` の `permissions`、役割別は各エージェント定義の `permissions` (V2 形式の順序付きルール、後勝ち) |
| ワークフロー (#8 以降) | `.opencode/commands/` (TUI 用)。`opencode run` ではコマンド展開が働かないため、無人実行は #8・#20 で別途設計する |

- 生成器による `.opencode/` の自動生成は採用しなかった。単一の真実の源を `.opencode/agents/` に置き、モデル割当だけをデータ (profiles) から差し替える方式の方が、独自ツールを増やさず分離原則を満たすため (判断の詳細は [docs/design/0001](design/0001-agents-profiles-permissions.md))。
- permissions は「後勝ち」の順序付きリストであり、広いルールを先に書き例外を後に置く。この順序規約は [docs/design/0001 §2](design/0001-agents-profiles-permissions.md) に記録した。

## 8. 独自実装の導入条件

設計原則「OpenCode の標準機能を優先」に基づき、独自オーケストレーター等の独自実装は以下をすべて満たす場合にのみ検討する:

1. 標準機能で実現できない制約が、調査 (#4) と実証によって明確になっている。
2. 導入によって解決する問題が、導入コストと保守負担を上回ると測定できる。
3. 最小の実装から始め、段階的に拡大する。

「あると便利」を理由にした先行実装は行わない。

## 9. 非目標

- 特定の研究室・組織・LLM プロバイダーへの依存 (第 2 プロバイダー対応は #19)。
- 標準機能で実証されていない独自オーケストレーターの実装。
- 無承認での自動マージ、Phase 4 以外での無人実行。
- 研究固有の実験手順・公開判断の本リポジトリへの取込み (#18 で共通契約のみ設計)。

## 10. 現状と今後

- Phase 0: 本文書の初期整備、README / ROADMAP / AGENTS.md の整備 (本 PR)。
- Phase 1: #4 の調査結果に基づき、設定優先順位 (§4) と OpenCode 依存部分 (§7) を確定済み。5 職種のエージェント・モデルプロファイル・権限制御の実装と検証は [docs/design/0001](design/0001-agents-profiles-permissions.md) を参照。
