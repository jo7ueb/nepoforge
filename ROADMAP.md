# ロードマップ

この文書は [Issue #1](https://github.com/jo7ueb/nepoforge/issues/1) で策定した方針を追跡します。個別開発の進捗は各 Issue で管理し、この文書は Phase 単位の状態を示します。

## 直近の優先ロードマップ（2026-11-26）

**最優先は Nepoforge の完成ではなく、他の研究開発の再開。** 現在の `/nfgo <番号>` を安全に使える状態で一旦整備を止める。3分以内という性能目標・完全自律・大規模ベンチマークは再開の必須条件ではない。研究コードの機密性、費用事故の防止、品質、追跡可能性は妥協しない。以下は本日時点の優先順位案であり、人間のレビューを経て採用する。既存の長期 Phase は維持するが、直近の着手順は本節を優先する。

### 4象限：未解決 Issue 全35件

重要度は安全性・品質・研究開発の生産性への影響、緊急度は「現時点で他プロジェクトの着手を妨げるか／直近の運用上の摩擦があるか」で判定する。Issue の Open は未実装を意味しない。部分実装済みの案件は残件だけを扱う。分類はスナップショットであり、完了・環境変化時に更新する。

| 象限 | Issue | 理由・対応 |
| --- | --- | --- |
| Q1 重要・緊急 | [#76](https://github.com/jo7ueb/nepoforge/issues/76)、[#53](https://github.com/jo7ueb/nepoforge/issues/53)、[#49](https://github.com/jo7ueb/nepoforge/issues/49)、[#52](https://github.com/jo7ueb/nepoforge/issues/52)、[#61](https://github.com/jo7ueb/nepoforge/issues/61)、[#15](https://github.com/jo7ueb/nepoforge/issues/15)、[#12](https://github.com/jo7ueb/nepoforge/issues/12)、[#72](https://github.com/jo7ueb/nepoforge/issues/72)、[#73](https://github.com/jo7ueb/nepoforge/issues/73) | 安全な指定Issue実行、予算・計測、機密性、モデル設定の再現性。最小限の再開ゲートだけを満たす。 |
| Q2 重要・非緊急 | [#79](https://github.com/jo7ueb/nepoforge/issues/79)、[#13](https://github.com/jo7ueb/nepoforge/issues/13)、[#14](https://github.com/jo7ueb/nepoforge/issues/14)、[#51](https://github.com/jo7ueb/nepoforge/issues/51)、[#16](https://github.com/jo7ueb/nepoforge/issues/16)、[#17](https://github.com/jo7ueb/nepoforge/issues/17)、[#18](https://github.com/jo7ueb/nepoforge/issues/18)、[#19](https://github.com/jo7ueb/nepoforge/issues/19)、[#25](https://github.com/jo7ueb/nepoforge/issues/25)、[#26](https://github.com/jo7ueb/nepoforge/issues/26)、[#27](https://github.com/jo7ueb/nepoforge/issues/27)、[#28](https://github.com/jo7ueb/nepoforge/issues/28)、[#20](https://github.com/jo7ueb/nepoforge/issues/20)、[#21](https://github.com/jo7ueb/nepoforge/issues/21)、[#31](https://github.com/jo7ueb/nepoforge/issues/31)、[#32](https://github.com/jo7ueb/nepoforge/issues/32)、[#33](https://github.com/jo7ueb/nepoforge/issues/33)、[#34](https://github.com/jo7ueb/nepoforge/issues/34)、[#35](https://github.com/jo7ueb/nepoforge/issues/35)、[#36](https://github.com/jo7ueb/nepoforge/issues/36)、[#37](https://github.com/jo7ueb/nepoforge/issues/37) | 継続改善・品質評価・研究向けワークフロー・自律運転・長期設計。再開後、実際の需要に応じて進める。 |
| Q3 相対的に重要度低・緊急 | [#11](https://github.com/jo7ueb/nepoforge/issues/11)、[#47](https://github.com/jo7ueb/nepoforge/issues/47)、[#74](https://github.com/jo7ueb/nepoforge/issues/74)、[#77](https://github.com/jo7ueb/nepoforge/issues/77) | CI/導入の完了確認、表示・署名の運用摩擦。手動代替できるため再開を妨げない。署名・CI自体の価値を低く評価する意味ではない。 |
| Q4 相対的に重要度低・非緊急 | [#46](https://github.com/jo7ueb/nepoforge/issues/46) | force push制約。現行denyを維持し、必要時に人間が対応。 |

#### Q1：再開のための作業順

| 順序 | Issue | 今回の出口（全面完成を要求しない） |
| ---: | --- | --- |
| 1 | [#76](https://github.com/jo7ueb/nepoforge/issues/76) / [PR #78](https://github.com/jo7ueb/nepoforge/pull/78)、[#53](https://github.com/jo7ueb/nepoforge/issues/53)・[#49](https://github.com/jo7ueb/nepoforge/issues/49)・[#52](https://github.com/jo7ueb/nepoforge/issues/52) | PR #78 の最終差分・CI・安全性を人間が確認し、マージする。共通契約 (#54)、並列委譲 (#50/#59/#66)、ルーティング (#60/#71) と照合し、重大な未充足のみ修正。再実装・新規有料実機試験はしない。 |
| 2 | [#15](https://github.com/jo7ueb/nepoforge/issues/15)・[#61](https://github.com/jo7ueb/nepoforge/issues/61)、[#12](https://github.com/jo7ueb/nepoforge/issues/12) | 親・子・予約・再試行を含む予算と停止条件、モデル観測、既存run reportの計測境界を確認する。全実行の時間/費用（実額・推定・不明の区別）を自動で最終報告できる最小機能を整える。既存 #62/#63/#65/#66/#67/#71 とPR #78の成果を再利用。#12の詳細イベントや完全請求照合は後回し。 |
| 3 | [#72](https://github.com/jo7ueb/nepoforge/issues/72) | 通常更新で管理対象FastMetalモデルを安全に同期、doctor等で欠落/衝突を検出。手動JSONC編集を常態化させない。既存認証と他設定は保護。 |
| 4 | [#73](https://github.com/jo7ueb/nepoforge/issues/73) | Git共有の `.nepoforge.json` からprofileとData Policyを一貫して適用。未設定はconfidential、未確認の送信先/保持条件は拒否、プロジェクト移動・clone・worktreeでも有効。二重登録を廃止し、緩和や全体設定の無断変更は不可。 |
| 5 | 再開判定（新規の大規模開発Issueにしない） | 配布版更新、設定・ルート・権限の無課金確認、予算の最終防衛線を確認し、人間が通常作業1件の実行を承認。通常の研究開発へ移行。 |

#12 と #15/#61 は同一の実行契約に関わるため一体として整合させ、依存のある部分は先に実装する。#72/#73 のCLIや配布設定の変更競合を避ける。独立部分は並列可能だが、並列化のために不要なタスクを作らない。#76のマージまで #78 の既存検証を再実行しない。Issueの完了条件のうち残る非緊急項目は再開後に扱い、完了していないものを閉じない。

### 再開ゲート（人間が判断）

- [ ] PR #78 とQ1の必須残件が人間レビュー・マージ済み。最新CIが成功。masterのrequired checkは人間の承認下で設定（未設定なら手動で確認）。
- [ ] `nepoforge update` と `nepoforge doctor`、有効なprofile/route/Data Policyを課金なしで確認。対象研究プロジェクトの秘密・未公開データを送信前に保護できること。不明時は起動しない。
- [ ] 承認済みの実行全体予算と停止条件を確認。FastMetal側のキー上限等、提供元の強制上限を最終防衛線とする。アプリの予算判定は管理外実行や全リクエストの厳密な支出上限を保証しない。実行前に上限と範囲を決め、予算不足・ルート不一致・実行モデル不明時に安全側へ停止。無断再試行・昇格・マージ禁止。
- [ ] 実行ごとの時間・費用を観測し、未計測をゼロにしない。無課金テストと既存記録を優先。追加有料実機試験は別途明示承認がない限り禁止。
- [ ] 人間が承認した小規模な通常開発を監視下で1件実施する（費用上限は別途指定。追加ベンチマークを兼ねて大量起動しない）。成功・停止・レビュー・CI・報告を確認。問題がなければ研究開発を再開する。無人・夜間の大規模実行の承認ではない。

**再開したら Nepoforge の新規機能開発は原則保留。** 実際の研究・開発を進め、利用中に重大な阻害要因が出た場合だけQ1へ再分類する。研究固有の計画・実験・データ・公開判断は各研究リポジトリの規約と人間の監督で扱う。専用研究ワークフロー #18 や自律選定 #25〜#28 の完成を待たない。

### 再開後の優先順

- **近接改善:** #77 のAI生成署名は自動化まで手動で付与。#74 は表示を改善するが、実効ルーティングの確認は現行手段でも実施。#11（PR #75でCI導入済みだがIssueはOpen）、#47（PR #48で基盤導入済み）は残件を確認し、不要な再実装を避ける。
- **性能:** #79を段階的に進める。#12の詳細イベント記録と既存通常運用のデータを使い、制御の非LLM化 → 情報量縮小 → 往復・手戻り削減 → #14のモデル評価。#13は再現可能な比較が本当に必要な場合。#51の全面実装・有料試験の保留条件を維持し、#16/#17/#18/#19は実需要に応じる。
- **自律性:** #25 → #26 → #27 → #28（承認付きIssue選定）、#20/#21 → #31（継続自律）、#32〜#36（複数プロジェクト・監督・予算・通知・実験）。#37の長期ビジョンは維持。今はこれらの完了を再開条件にしない。

根拠: PR #75 の実行効率問題、PR #78 の並列委譲PoC・[追加分析](https://github.com/jo7ueb/nepoforge/pull/78#issuecomment-7186365754)。PR #78は本記録時点でOpen、FastMetal上の改善効果は未実測。#79の3分目標は将来の検証対象であり、現行条件での達成を保証しない。

---

## 目的

Nepoforge は、個人・小規模チーム向けに、役割分担した AI エージェントによるソフトウェア開発・研究活動の実行基盤を提供する。品質を落とさずに人間の介入と API 費用を減らすことを目指す。

## 設計原則

- 特定の研究室・組織・プロバイダーから独立する。
- 役割・プロンプト・モデルプロファイル・ワークフローを分離する。
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
