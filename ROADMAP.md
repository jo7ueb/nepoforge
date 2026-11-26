# ロードマップ

この文書は [Issue #1](https://github.com/jo7ueb/nepoforge/issues/1) で策定した方針を追跡します。個別開発の進捗は各 Issue で管理し、この文書は Phase 単位の状態を示します。

## 直近の優先ロードマップ（2026-11-26、PR #82 費用事故後）

**最優先は Nepoforge の完成ではなく、他の研究開発の再開。現在、FastMetalの通常有料 `/nfgo` は保留する。** 修正は別途承認済みの Subscription 等の環境で行い、既存ログ・合成fixture・モック・CIを使用する。追加有料実機試験は別途明示承認なしに行わない。3分目標・完全自律・大規模ベンチマークは再開条件ではない。

現在の設計方針は [docs/architecture.md](docs/architecture.md) が正本。Issue本文は当初要求、コメントは追加要求と時系列、PRは成果物と検証記録。優先順位・再開条件の変更は本節へ統合し、関連する履歴へリンクする。以下は人間レビュー後に採用する優先順位で、実装完了の宣言ではない。

### 現状と事故からの判断

- PR #78 / #76 はマージ済み。strict plan/run、委譲の証拠、起動ゲートは存在するが、計画分解の妥当性や実行中の消費を強制停止できない。
- PR #82 / #12 の事後報告機能はマージ済み。通常実行自体は不合格。T1は予約1.3911円に対し推定148.7266円（約106.9倍）、起動約12秒で予約超過、同系統edit失敗139回。子同士の重複なし、親待機中にループ継続。親子総額439.74円はキャッシュ読取を通常入力単価で評価した推定であり請求実額ではない。承認済み総予算、実請求、キャッシュ実単価は未確認。
- #15/#61/#12 の [実行中の監視・強制停止](docs/architecture.md#33-実行安全性観測判定強制停止) を最優先の再開ブロッカーとする。#49の分解品質も未解消。モデルの単価・性能だけに帰着させず、制御の欠陥を先に直す。

### 4象限：未解決 Issue 全35件

重要度は安全性・品質・研究開発への影響、緊急度は現在の他プロジェクトへの阻害・運用摩擦で判定する。Openは未実装を意味しない。#76は完了し、#81をQ1に追加した（前回 #80 からの更新）。各Issueは一つの象限だけに置く。

| 象限 | Issue | 理由・対応 |
| --- | --- | --- |
| Q1 重要・緊急 | [#15](https://github.com/jo7ueb/nepoforge/issues/15)、[#61](https://github.com/jo7ueb/nepoforge/issues/61)、[#12](https://github.com/jo7ueb/nepoforge/issues/12)、[#53](https://github.com/jo7ueb/nepoforge/issues/53)、[#49](https://github.com/jo7ueb/nepoforge/issues/49)、[#52](https://github.com/jo7ueb/nepoforge/issues/52)、[#72](https://github.com/jo7ueb/nepoforge/issues/72)、[#73](https://github.com/jo7ueb/nepoforge/issues/73)、[#81](https://github.com/jo7ueb/nepoforge/issues/81) | 実行中の安全性・費用・分解・機密性・設定再現性。必須残件のみ。 |
| Q2 重要・非緊急 | [#79](https://github.com/jo7ueb/nepoforge/issues/79)、[#13](https://github.com/jo7ueb/nepoforge/issues/13)、[#14](https://github.com/jo7ueb/nepoforge/issues/14)、[#51](https://github.com/jo7ueb/nepoforge/issues/51)、[#16](https://github.com/jo7ueb/nepoforge/issues/16)、[#17](https://github.com/jo7ueb/nepoforge/issues/17)、[#18](https://github.com/jo7ueb/nepoforge/issues/18)、[#19](https://github.com/jo7ueb/nepoforge/issues/19)、[#25](https://github.com/jo7ueb/nepoforge/issues/25)、[#26](https://github.com/jo7ueb/nepoforge/issues/26)、[#27](https://github.com/jo7ueb/nepoforge/issues/27)、[#28](https://github.com/jo7ueb/nepoforge/issues/28)、[#20](https://github.com/jo7ueb/nepoforge/issues/20)、[#21](https://github.com/jo7ueb/nepoforge/issues/21)、[#31](https://github.com/jo7ueb/nepoforge/issues/31)、[#32](https://github.com/jo7ueb/nepoforge/issues/32)、[#33](https://github.com/jo7ueb/nepoforge/issues/33)、[#34](https://github.com/jo7ueb/nepoforge/issues/34)、[#35](https://github.com/jo7ueb/nepoforge/issues/35)、[#36](https://github.com/jo7ueb/nepoforge/issues/36)、[#37](https://github.com/jo7ueb/nepoforge/issues/37) | 継続改善・品質評価・研究向けワークフロー・自律運転・長期設計。再開後に実需要で判断。 |
| Q3 相対的に重要度低・緊急 | [#11](https://github.com/jo7ueb/nepoforge/issues/11)、[#47](https://github.com/jo7ueb/nepoforge/issues/47)、[#74](https://github.com/jo7ueb/nepoforge/issues/74)、[#77](https://github.com/jo7ueb/nepoforge/issues/77) | CI/導入の完了確認、表示・署名の摩擦。手動代替可。 |
| Q4 相対的に重要度低・非緊急 | [#46](https://github.com/jo7ueb/nepoforge/issues/46) | force push制約。現行denyを維持。 |

#### Q1：再開のための作業順

| 順序 | Issue | 今回の出口（全面完成を要求しない） |
| ---: | --- | --- |
| 1 | [#15](https://github.com/jo7ueb/nepoforge/issues/15)・[#61](https://github.com/jo7ueb/nepoforge/issues/61)・[#12](https://github.com/jo7ueb/nepoforge/issues/12) | **P0：実行安全性の一体設計・実装。** #12の実行中観測 → #15の次リクエスト前の予算判定/停止と #61の異常・無進捗停止を同じ実行契約で接続。親のLLM待機中も動くこと。OpenCode既存のフック・usage・cancel・内部retryを確認し、制御不能なら安全な上限を確保できない実行は拒否。#82の生データをfixture化し、予約超過・同系統エラー・同時in-flight・親子・再試行・欠損・停止ラッチを無課金検証。PR #82 の既存機能を再利用し、詳細イベント収集・完全請求連携は後回し。 |
| 2 | [#53](https://github.com/jo7ueb/nepoforge/issues/53)・[#49](https://github.com/jo7ueb/nepoforge/issues/49)・[#52](https://github.com/jo7ueb/nepoforge/issues/52) | **P1：分解・モデル選択の実効性。** 既存 #54/#50/#59/#66/#60/#71/#78 を維持し、独立調査→必要な設計→独立実装・試験を依存/競合/予算に沿って分離・並列。局所変更の例外に根拠を求め、子への丸投げを防ぐ。モデルのツール成功率・キャッシュ・総費用を既存証拠から評価し、#82の失敗経路をそのまま再実行しない。無断昇格・全面再実装不要。 |
| 3 | [#72](https://github.com/jo7ueb/nepoforge/issues/72) | 通常updateで管理対象FastMetalモデル定義を安全に同期、doctorで欠落/衝突を検出。既存認証・他設定・利用者上書きを保護。 |
| 4 | [#73](https://github.com/jo7ueb/nepoforge/issues/73) | Git共有の `.nepoforge.json` をプロジェクト設定の正本とし、profile/Data Policyの二重登録・絶対パス依存を解消。未設定はconfidential、信頼済みリポジトリ・人間レビュー・実行開始時固定。 |
| 5 | [#81](https://github.com/jo7ueb/nepoforge/issues/81) | /nfgoの最初のLLMリクエスト前に有効なCoordinator/variant/route/価格/ポリシーを解決・適用。誤選択・不適格時は送信せず停止。#73の結果を再利用。#74は同じ解決結果の表示に用いるが、全表示刷新はQ3のまま。 |
| 6 | 再開判定（新規Issueにしない） | 下記ゲートを満たした後、人間が承認した通常作業1件のみ監視下で実行。成功・停止・費用報告・品質を確認して通常研究開発へ戻る。 |

#12/#15/#61は共通の観測・判定・停止機構を分担し二重実装しない。先にOpenCodeでの強制可能性を確認する。#53/#49/#52は既存設計・実装の必須残件だけ扱う。独立作業は並列可能だが、編集競合や依存を守る。#72/#73/#81の設定変更は依存と競合に従って進める。完了済みPRは再実行せず、未完のIssueを無理に閉じない。

### 再開ゲート（人間が判断）

- [ ] Q1の必須残件がレビュー・マージ済み。最新CI成功。required checkは人間の承認下で設定（未設定なら手動確認）。
- [ ] 実行中の予算ガードが親・子・内部retry・in-flightを含めて機能することを無課金fixtureで確認。#82の履歴で予約超過後の追加送信が拒否され、tool失敗反復が設定閾値内に停止する。親が子の完了待ちでも検知・停止でき、停止後に有料起動・直接実装・無断再試行で迂回しない。停止遅延/最大追加損失を記録し、安全な上限を確保できない条件では運用しない。
- [ ] 承認済みのrun/task予算と提供元側の強制支出上限を確認。FastMetalキー上限は独立した最終防衛線。アプリ内の見積りを厳密な請求上限と称しない。並行利用・in-flight・未知の課金条件も考慮する。
- [ ] 配布版更新、doctor、実効profile/Data Policy、Coordinator・variant・ルート・価格・権限を課金なしで確認。対象研究プロジェクトの機密性を最初の送信前に保護し、不明時は拒否する。
- [ ] 独立タスクの分解・ready・受入・レビュー・CIの回帰検証を確認。実行中の観測と最終報告で親子費用・時間・予算・停止理由・観測境界を示し、実額/推定/不明を分ける。
- [ ] 上記を満たした後、人間が別途承認した小規模な通常開発1件を監視下で実行。費用上限を明示し、追加ベンチマーク・無人夜間実行は行わない。安全・品質・費用が許容範囲なら研究開発を再開する。

**再開したら Nepoforge の新規機能開発は原則保留。** 実際の研究・開発を進め、重大な阻害要因のみQ1へ再分類する。専用研究ワークフロー #18、自律選定 #25〜#28、#79の全面最適化を待たない。

### 再開後の優先順

- **近接改善:** #77のAI署名は自動化まで手動、#74のタスク別モデル表示を改善、#11/#47の既存成果の残件確認。
- **性能:** #79を段階的に進める。決定的制御の非LLM化 → 情報抽象化 → 往復・手戻り削減 → 軽量CoordinatorとOn-demand Intelligenceの評価 (#14/#52)。#12の詳細イベントと通常運用データを使用し、3分目標は未検証。#13の比較試験は必要時に別途承認。#51の全面実装・有料試験保留を維持し、#16/#17/#18/#19は実需要で判断する。
- **自律性:** #25 → #26 → #27 → #28、#20/#21 → #31、#32〜#36。#37の長期ビジョンは維持する。

### 変更根拠（時系列への参照）

PR #75のCoordinator抱え込み → PR #78のDelegation-first PoC → PR #80の短期ロードマップ → #81の送信前自動選択 → #79の軽量Coordinator方針 → [#49の分解問題再発](https://github.com/jo7ueb/nepoforge/issues/49#issuecomment-7187776005) → PR #82の費用事故と[追加分析](https://github.com/jo7ueb/nepoforge/pull/82#issuecomment-7188507364) → [#15](https://github.com/jo7ueb/nepoforge/issues/15#issuecomment-7188663384)・[#61](https://github.com/jo7ueb/nepoforge/issues/61#issuecomment-7188664642)・[#12](https://github.com/jo7ueb/nepoforge/issues/12#issuecomment-7188666131)の追加受入条件。本節は現時点の統合結果。過去のコメントは変更せず、詳細な数値・議論はリンク先で参照する。

---

## 目的

Nepoforge は、個人・小規模チーム向けに、役割分担した AI エージェントによるソフトウェア開発・研究活動の実行基盤を提供する。品質を落とさずに人間の介入と API 費用を減らすことを目指す。

## 設計原則

- 特定の研究室・組織・プロバイダーから独立する。
- 役割・プロンプト・モデルプロファイル・ワークフローを分離する。詳細な設計思想と実行安全性は [docs/architecture.md](docs/architecture.md) を正本とする。
- 当面は OpenCode の標準機能を優先し、独自オーケストレーターは必要性を実証してから検討する。
- 既存の設定を破壊しない。秘密情報はリポジトリに保存しない。
- Human-on-the-Loop: 人間は成果物を PR で承認する。無承認マージはしない。
- モデル評価は合格成果物あたりのコスト、成功率、人間介入、時間を基準にする。

詳細な責務分離と信頼境界は [docs/architecture.md](docs/architecture.md) を参照。

長期構想（未着手）: [Loop Engineering / Graph Engineering / Self-Improving Factory](docs/future-factory-engineering.md) を参照。本構想は #37 の Autonomous R&D Factory ビジョンを補完するものであり、現在の MVP の優先順位や Phase の出口条件を変更しない。

## Phase 0 — Project Foundation

- [x] #2 基本アーキテクチャと設計原則の文書化
- [x] #3 リポジトリ構成・README・AGENTS.md の整備

出口条件: 目的、設計原則、責務分離、開発規約が文書化されている。

## Phase 1 — MVP (v0.1.0)

- [x] #4 OpenCode のエージェント構成と委譲仕様の調査 (PR #23)
- [x] #5 基本エージェント 5 種の実装
- [x] #6 モデルプロファイルと役割定義の分離
- [x] #7 FastMetal プロバイダー設定の整備
- [x] #8 `/nfgo` コマンドによる指定 Issue 開発ワークフローの実装 (旧 `/issue` から `/nfgo` に統合)
- [x] #9 インストール・更新・アンインストール機能の実装
- [x] #10 権限制御と安全な GitHub 操作の実装

出口条件:

- 安全にインストール・更新・アンインストールできる。
- モデルプロファイルを切り替えられる。
- 分析、実装、レビューに別々のモデルを割り当て、実際の利用モデルを検証できる。
- `/nfgo <番号>` からテスト・独立レビュー・PR 作成まで実行できる。
- 最低限の設定検証と費用確認ができる (#11・#12 の基礎部分を先行)。
- 権限・再試行の安全な初期制限があり、無承認マージしない。

## Phase 2 — Evaluation & Optimization

- [x] #11 設定ファイルの自動検証と CI の整備 (`nepoforge validate` と `.github/workflows/ci.yml`)
- [ ] #12 エージェント実行履歴・トークン・費用の記録
- [ ] #13 再現可能なベンチマーク環境の構築
- [ ] #14 モデル構成の比較評価
- [ ] #15 再試行・エスカレーション・予算制御の設計
- [ ] #47 パッケージ化・ユーザー単位のグローバル導入・管理 CLI の整備 (MVP 後の UX 改善として Phase 2 に位置づけ)

前提 (先行ゲート): #53 タスク分解・並列委譲・モデル選択・レビューの共通契約 ([設計メモ 0005](docs/design/0005-common-task-contract.md))。#49 → #52 → #51 は本設計のマージ後に着手する。#52 のタスク単位モデル割り当ては [設計メモ 0007](docs/design/0007-task-model-routing.md) に従い、細粒度分解・暫定並列上限の方針を 0005 に先行反映したうえで実装する。

出口条件: 固定課題で品質・コストを再現可能に比較でき、推奨プロファイルと停止条件を定義できる。

## Phase 3 — Workflow Expansion

- [ ] #16 独立した PR レビューワークフローの実装
- [ ] #17 技術調査ワークフローの実装
- [ ] #18 研究活動・実験・研究記録ワークフローの設計
- [ ] #19 複数 LLM プロバイダーへの対応

出口条件: 独立レビューと技術調査が利用でき、研究ワークフローの設計および第 2 プロバイダーの検証が完了している。

## Phase 4 — Autonomous Execution

- [ ] #20 GitHub Issue を起点とした自動実行基盤の設計
- [ ] #21 並列実行・作業分離・実行状態管理の設計

出口条件: 許可された Issue に対する自動実行と並列作業分離の設計・検証計画が整っている。無人実装への移行は安全性・費用の実証後に別途判断する。

## 推奨着手順

現在の着手順・再開ゲートは冒頭の「直近の優先ロードマップ」に従う。従来の Phase は長期の機能分類として維持する。

## 運用ルール

- 各 Issue の「共通ルール」に従う: AGENTS.md と既存の規約を遵守し、検証結果・残存課題を PR に記録し、秘密情報はコミットしない。
- 人間の承認なしに PR をマージしない。

## 長期技術構想（着手未承認）

[Autonomous R&D Factory — 長期技術構想](docs/design/factory-engineering-vision.md) に Loop Engineering、Graph Engineering、工場自体の継続的改善の方向性を記録する。現在の Phase・優先順位・実行承認は変更しない。
