# 設計メモ 0011: Delegation-first の機械検証 (#76)

状態: 設計案。実装・契約変更は本書のレビュー後、別 Issue/PR で行う。
関連: #76、#12、設計メモ [0004](0004-parallel-delegation.md)、[0005](0005-common-task-contract.md)、[0007](0007-task-model-routing.md)、[0008](0008-budget-gate.md)

## 1. 目的と事実

PR #75 の事後報告では、初回 push まで約17分を Coordinator が実装・検証・文書・コミットまで担い、後段レビューだけが3件委譲された。費用は概算で未計測、モデル不明が2件、自己申告が1件だった。これは個別事例であり、一般的な費用・時間の改善量を示すものではない。

既存 `plan ready` は TaskSpec の構造、依存の受理、失敗、Resolver、予算を判定するが、未宣言の直接作業が計画より先行していないかは観測できない。本設計は既存 API を壊さず、明示契約を与えた run に対する厳格な検証を追加する。計画の受理と実績によるゲートは別ファイルに分離し、#12 の完全な実行記録基盤は実装しない。

## 2. 設計判断

* 従来の `plan validate/ready` の動作・JSON は維持する。新モードは明示した `--contract PATH` と `--run-id ID` を必要とする。契約なしで旧運用を自動的に厳格化しない。
* 厳格モードでは対象 Issue の全作業を計画へ宣言する。直接作業も `executor:"primary"` として TaskSpec に含める。Primary の初回把握・分解自体は計画対象外の準備として許すが、実装・検証・文書・git/gh 操作は宣言なしに実施しない。
* 作業は一目的の成果物、完了条件、`write_scope`、依存を持つ。不可分として統合する場合は `exception` に理由・範囲・代替分割案・分割コストの根拠を記し、統合 Task の範囲と一致させる。単に Coordinator が実施しやすいことは理由にならない。
* 進捗の表示は工程名・並列タスク・各タスクの Resolver 選択モデルと観測した実行モデルを示す。実行モデル未観測は `unknown` と明示し、選択済みと実行済みを混同しない。

## 3. ファイル・CLI インターフェース案

計画契約 (`plan.json`) と実績ゲート (`gate.json`) は独立ファイルとする。

```json
{
  "schema_version": 1,
  "issue_ref": "#76",
  "tasks": [{
    "task_id": "T1", "role": "implementer", "executor": "agent",
    "task_kind": "implementation", "objective": "一つの検証可能な成果",
    "inputs": ["参照先"], "expected_output": "差分・試験結果",
    "acceptance_criteria": ["検証可能な条件"],
    "depends_on": [], "read_scope": ["nepoforge/**"],
    "write_scope": ["nepoforge/planning.py"],
    "parallel_group": "impl-a"
  }]
}
```

`executor` は `agent` / `tool` / `primary`。`tool` は判断を伴わない決定的作業、`primary` は契約下での Coordinator 直接作業を表す。tool/primary はモデル選択・費用予約を要しないが、成果・証拠・受理条件は省略できない。agent は既存 resolver/binding/予算規則を通す。自由記述の権限・送信先指定は拒否。

```json
{
  "run_id": "run-unique",
  "plan_sha256": "...",
  "budget": {"limit_yen": 100, "spent_yen": 0, "reserved_yen": 0},
  "budget_error": false,
  "events": [{
    "task_id": "T1", "attempt": 1, "event": "launched",
    "session_id": "ses...", "selected_model": "provider/model",
    "actual_model": "provider/model", "evidence": ["session message ref"]
  }]
}
```

### 必要最小限の CLI 拡張

* `nepoforge plan validate PLAN --contract` — 厳格スキーマ・全作業宣言・write scope・依存・exception を検証する。既存の validate は互換維持。
* `nepoforge plan ready PLAN --contract --run-id ID --gate GATE --json` — 既存 resolver と `planning.ready_tasks` / `budget.decide_launch` を再利用し、契約 digest と gate のイベントから起動可能集合を判定する。JSON に `run_id`, `plan_sha256`, `stage`, `ready`, `launch`, `denied`, `not_ready`, `exceptions` を含める。明示モードでは gate 欠落・不正・計画 digest 不一致は拒否。
* `nepoforge plan record GATE --event EVENT.json` — 1イベントを追記する最小手段。run/task/attempt/session の対応を保持し、既存イベントを上書きしない。イベント重複・不正遷移は拒否。これは #12 の一般記録DBではない。

`ready` は plan + gate を受ける純粋判定相当の境界とし、ファイル読込/保存は CLI に置く。概念シグネチャ:

```python
validate_contract(tasks, issue_ref) -> None  # PlanError
strict_ready(tasks, gate, *, run_id, plan_sha256, profile, policy) -> StrictReady
record_event(gate, event) -> Gate             # 不変更新。契約違反は RecordError
```

`strict_ready` は旧 ready と同じ候補選択を行い、TaskResult の受理済み ID のみ依存充足とする。Stage はタスク属性から導出する表示用ラベルで、工程単位の全件完了バリアを作らない。`depends_on` が満ちたタスクは他工程が実行中でも起動可能。parallel_group は説明・表示用で、既存の write_scope 競合と max_parallel を緩和しない。

## 4. 実績ゲートと fail-closed 規則

gate イベントは `run_id`, plan digest, task_id, attempt を常に結び付ける。親セッションは `primary` レコード、子は session_id を持つ agent レコード、再試行は同じ task_id の `attempt+1` として別イベントにする。run をまたぐ実績・予約の流用は禁止。新 run に引き継ぐには人間確認済みの実績値を明示する。

1. 起動直前に最新 gate を再読込し、実績 + 全起動済み未精算予約 + 新規見積りで `decide_launch` を再実行する。起動許可後でも実際に起動しなかった予約は計上しない。
2. 起動記録がない実行、親費用の上限/実績が未記録、価格不明・失効、上限未設定、予算拒否は有料起動不可。親自身の有料実行も同じ上限に含める。利用者が請求等から費用を取得できない場合、unknown をゼロ扱いせず停止する。
3. provider budget error は run 全体の停止状態として永続イベントにし、新規有料起動と自動再試行を拒否する。再開は人間の明示承認記録のみ。決定的 tool は継続可能。
4. 子の error、受理拒否、実行モデル不一致、モデル観測不能、証拠欠落は成功に変換しない。actual_model unknown は status=failed相当の `verification=unknown` として失敗理由を区別し、依存を充足させない。選択モデルも未知なら起動を認めない。
5. evidence は受理 criteria ごとの参照を要する。Result の自己申告だけでは受理しない。機械取得した message API のモデル・agent・session 対応と、tool の終了コード/対象スナップショット等を証拠とする。取得不能なら reject/停止。
6. 自動再試行は同一 task・同一選択モデルで最大1回、毎回新しい予算判定・予約を行う。Resolver/権限/予算拒否、利用不能モデルを再試行・Primary 有料作業・別モデルで迂回しない。人間承認例外は拒否状態を解除しない。例外理由と範囲を記録して計画を改訂し、必要な承認ゲートを再通過する。
7. 権限拒否・子失敗は明示結果として保持し、別 role・Primary・tool への黙示代替を許さない。人間承認は PR マージ等既存の承認点を代替しない。

不可分、費用超過、権限不足、人間承認待ちは別々の停止理由とする。例外記録には対象 task/ファイル/費用上限/承認者・時刻/承認内容を限定記載し、承認範囲外の実行を許さない。計画変更は新 digest とし、既存結果は同じ対象スナップショットに限り再利用する。

## 5. 工程・並列性・表示

Stage は `stage` 表示用の分類であって DAG ではない。順序は `depends_on` のみで決定する。例えば独立した調査 A/B が同時進行中に、B の受理を待たず A 依存の設計・実装準備を起動してよい。受理されていない必須依存は起動不可。レビューはレビュー対象 snapshot の確定を依存にし、無関係な検証を待たせない。

進捗イベント/表示は各行に `stage`, `task_id`, `parallel_group`, state, selected_model, actual_model を出す。未起動の選択は「選択」、実行中で未取得は `actual_model: unknown`、終了後の未照合も明示し受理を止める。工程見出しだけを理由に待機・全件バリアを作らない。

## 6. #12 記録への接続と限界

本設計の `gate.json` は単一 run の入力証跡・予算ゲートに必要な最小ローカル記録であり、#12 の正本DB、完全な親子セッション収集、課金明細との照合、改ざん耐性監査ログを実装しない。後続 #12 は同じキー (`run_id`, `task_id`, `attempt`, `session_id`, selected/actual model, usage/cost, timestamps, outcome) で取り込める adapter を設ける。会話本文・秘密情報は保存しない。

Nepoforge が観測できるのは宣言契約と記録されたイベントだけである。契約外の手作業、別セッション/CLI/provider利用、未報告の費用、実環境の強制支出、権限設定変更を検知・阻止できない。対策は宣言の網羅確認、実行ログ突合、差分/PRでの人間レビューであり、完全な強制を謳わない。

## 7. 代替案・影響範囲・未解決

採用案は CLI の明示 strict mode と gate の最小記録。既存 API とデータ利用を保ち段階導入できる。代替として全 plan を即時 strict にする案は、旧利用者の暗黙直接作業を破壊するため不採用。完全DB/自動オーケストレーター案は #12 の責務・architecture §8 の導入条件を超えるため不採用。Markdown 進捗だけでは証拠・予算の機械判定ができず strict gate の代替にならない。

影響想定: `planning.py` に契約検証・strict ready、`cli.py` に `--contract`/`--run-id`/`--gate` と record、`budget.py` の既存価格・判定関数再利用、`nfgo.md` に契約必須化と進捗表示。Model Resolver、依存 DAG、軽量モデル規則を再実装しない。AGENTS.md/architecture の整合は実装PRで反映する。

未解決: Primary 自身の支出・選択/実行モデル証拠を各実行エンジンからどの API で得るか、現在の費用入力の信頼性と run をまたぐ人間承認済み精算形式は実装前に確認する必要がある。取得不能なら strict paid run は拒否する。実装は小さな契約 validator/ready/record を先行し、#12 の完全収集を待つ設計にはしない。
