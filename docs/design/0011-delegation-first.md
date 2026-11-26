# 設計メモ 0011: Delegation-first の機械検証 (#76)

状態: Issue #76 の実装インターフェース確定版。CLI/ledger の実装は別担当。
関連: #76、#12、設計メモ [0004](0004-parallel-delegation.md)、[0005](0005-common-task-contract.md)、[0007](0007-task-model-routing.md)、[0008](0008-budget-gate.md)

## 1. 目的と事実

PR #75 の事後報告では、初回 push まで約17分を Coordinator が実装・検証・文書・コミットまで担い、後段レビューだけが3件委譲された。費用は概算で未計測、モデル不明が2件、自己申告が1件だった。これは個別事例であり、一般的な費用・時間の改善量を示すものではない。

既存 `plan ready` は TaskSpec の構造、依存の受理、失敗、Resolver、予算を判定するが、未宣言の直接作業が計画より先行していないかは観測できない。本設計は既存 API を壊さず、明示契約を与えた run に対する厳格な検証を追加する。計画の受理と実績によるゲートは別ファイルに分離し、#12 の完全な実行記録基盤は実装しない。

## 2. 設計判断

* 従来の CLI 契約は維持するが、配布 `/nfgo` は常に strict を使う。legacy は CLI 互換利用者に限る。
* 厳格モードでは対象 Issue の全作業を宣言する。初回要求理解・計画作成は準備として許すが、それ以外の作業は計画なしに実施しない。
* 要求理解→調査→設計→実装→テストの依存は維持する。各工程内で独立し依存が解消したタスクは並列に進め、工程全件完了バリアは作らない。
* 各進捗メッセージに進行中タスクと各モデルを記載し、モデルは選択値と観測後の実値を分ける。未観測は unknown と明示する。

## 3. ファイル・CLI インターフェース案

計画契約 (`PLAN`) と実績ゲート (`GATE`) は独立ファイルとする。

```json
{
  "schema_version": 1,
  "issue_ref": "#76",
  "deliverables": ["D1"],
  "tasks": [{
    "task_id": "T1", "role": "implementer", "executor": "agent",
    "stage": "implementation", "deliverables": ["D1"],
    "objective": "一つの検証可能な成果",
    "inputs": ["参照先"], "expected_output": "差分・試験結果",
    "acceptance_criteria": ["検証可能な条件"],
    "depends_on": [], "read_scope": ["nepoforge/**"],
    "write_scope": ["nepoforge/planning.py"]
  }]
}
```

PLAN は schema_version=1、issue_ref、deliverables (独立に確認する成果物 ID)、tasks を持つ。各成果物はちょうど一つの担当タスクに割り当てる。task は既存 TaskSpec に加えて executor (agent/tool/primary) と stage (requirements/research/design/implementation/test/review/integration) を明記する。`tool` の task_kind は test/ci-status/git/diagnostics に限り、追跡対象ファイルを編集できない。`primary` は `{exception:{reason:inseparable|delegation_cost|human_approved,detail:...,scope:write_scopeと同一}}` を必須とする。human_approved の例外は人間の承認と CLI の `--approved-exception TASK_ID` が必要。権限・送信先の自由記述は拒否する。

```json
{
  "run_id": "run-unique", "plan_sha256": "...",
  "started_ms": 0, "as_of_ms": 0,
  "primary": {"session_id": "ses...", "spent_yen": null, "reserved_yen": null,
    "outcome": "running", "ended_ms": null, "success": false, "messages": []},
  "attempts": [{"task_id": "T1", "attempt": 1, "session_id": "ses...",
    "started_ms": 0, "ended_ms": null, "model": {}, "reserved_yen": null,
    "spent_yen": null, "outcome": "running", "messages": [],
    "exit_code": null, "success": null, "failure_reason": null}],
  "budget_error": false
}
```

`primary` は `{session_id,spent_yen,reserved_yen,outcome:running|accepted|failed,ended_ms:null|ms,success,messages:[]}`。`success` は `outcome=accepted` のときのみ真。attempt は `{task_id,attempt,session_id,started_ms,ended_ms,model,reserved_yen,spent_yen,outcome,messages,exit_code,success,failure_reason}` で、`exit_code` は tool の終了コード、`success` は primary の判定、`failure_reason` は実行失敗理由を表す。金額不明はゼロでなく null/unknown とする。

OpenCode V2 の観測手順は `GET /api/session/{sessionID}/message?type=assistant&limit=...` を cursor でページングし、`.data[]` の `id,agent,model,time.created,tokens,cost` を取得する。親子関係は `GET /api/session/{sessionID}` でも独立取得し、実際のフィールド名は API schema またはツール出力で確認する。正規化した message は `{id,session_id,parent_session_id?,agent,model:{providerID,id,variant?},created_ms}`。親 message に `parent_session_id` は不要で、`created_ms` は必須かつ対象 attempt 区間内でなければならない。本文はフィルタ出力・保存せず、機密を含む本文表示を避ける。自己申告の session ID は API 証拠の代用にならない。親による直接作業の例外は親と同じ session ID の場合だけとし、親証拠を共用して attempt の `messages` に複製しない。tool の `session_id` は session でない一意なローカル記録 ID でもよく、その場合 `messages=[]`、費用欄は null とする。

### 必要最小限の CLI 拡張

* `nepoforge plan validate PLAN --contract [--approved-exception TASK_ID] [--json]` — strict 契約を検証する。
* `nepoforge plan ready PLAN --contract --run-id ID --gate GATE --limit-yen ... --json` — 最新の gate と人間承認上限で起動可否を判定する。
* Strict では `--accepted/--running/--failed/--spent-yen/--reserved-yen/--budget-error` の手入力を拒否する。これら旧入力は legacy 互換に限る。

`ready` は plan + gate を受ける純粋判定相当の境界とし、ファイル読込/保存は CLI に置く。概念シグネチャ:

```python
validate_contract(tasks, issue_ref) -> None  # PlanError
strict_ready(tasks, gate, *, run_id, plan_sha256, profile, policy) -> StrictReady
record_event(gate, event) -> Gate             # 不変更新。契約違反は RecordError
```

`strict_ready` は既存 Resolver を再利用し、機械確認済みの受理 ID のみ依存充足とする。開始直前に最新 snapshot を用いる。工程依存を維持しつつ、依存解消済みタスクは並列起動可能で全件バリアを設けない。

## 4. 実績ゲートと fail-closed 規則

gate イベントは `run_id`, plan digest, task_id, attempt を常に結び付ける。親セッションは `primary` レコード、子は session_id を持つ agent レコード、再試行は同じ task_id の `attempt+1` として別イベントにする。run をまたぐ実績・予約の流用は禁止。新 run に引き継ぐには人間確認済みの実績値を明示する。

1. 起動・再試行直前に最新 run snapshot を再読込し、親実績 + 起動済み未精算予約 + 新規見積りで `decide_launch` を再実行する。起動許可後でも実際に起動しなかった予約は計上しない。
2. 起動記録がない実行、親費用の上限/実績が未記録、価格不明・失効、上限未設定、予算拒否は有料起動不可。親自身の有料実行も同じ上限に含める。利用者が請求等から費用を取得できない場合、unknown をゼロ扱いせず停止する。
3. provider budget error は run 全体の停止状態として永続イベントにし、新規有料起動と自動再試行を拒否する。再開は人間の明示承認記録のみ。決定的 tool は継続可能。
4. 子の error、受理拒否、実行モデル不一致、モデル観測不能、証拠欠落は成功に変換しない。actual_model unknown は status=failed相当の `verification=unknown` として失敗理由を区別し、依存を充足させない。選択モデルも未知なら起動を認めない。
5. evidence は受理 criteria ごとの参照を要する。Result の自己申告だけでは受理しない。機械取得した message API のモデル・agent・session 対応と、tool の終了コード/対象スナップショット等を証拠とする。取得不能なら reject/停止。
6. 計画に `retries:1` を明示した場合のみ、`failure_reason=execution_failure` の自動再試行を同一 task・同一選択モデルで最大1回許す。再試行起動指示にも attempt 番号を含め、開始直前に最新 snapshot/予算を再判定する。初回実行モデルの証明が得られなければ再試行しない。Resolver/権限/予算拒否、受理拒否、利用不能モデルは実行失敗ではない。これらを再試行・Primary 有料作業・別モデルで迂回しない。人間承認例外は拒否状態を解除しない。例外理由と範囲を記録して計画を改訂し、必要な承認ゲートを再通過する。
7. 権限拒否・子失敗は明示結果として保持し、別 role・Primary・tool への黙示代替を許さない。人間承認は PR マージ等既存の承認点を代替しない。

不可分、費用超過、権限不足、人間承認待ちは別々の停止理由とする。例外記録には対象 task/ファイル/費用上限/承認者・時刻/承認内容を限定記載し、承認範囲外の実行を許さない。計画変更は新 digest とし、既存結果は同じ対象スナップショットに限り再利用する。

## 5. 工程・並列性・表示

Stage は `stage` 表示用の分類だが、要求理解→調査→設計→実装→テストの依存関係をPLANで明示する。各段階内で依存解消済みタスクを並列にでき、全件バリアは作らない。受理されていない必須依存は起動不可。レビューは対象 snapshot の確定を依存にする。

各段階の進捗メッセージに進行中タスクと各モデルの selected / actual を示す。OpenCode API message の正規化情報を根拠とし、未観測は unknown と明記する。終了後の未照合・開始後の子の観測不明は受理せず、依存を充足させない。

## 6. #12 記録への接続と限界

GATE は単一 run の最小記録であり、#12 の完全 collector、正本DB、課金明細照合、改ざん耐性監査ログではない。OpenCode API から正規化 message を親または子の記録に付ける。本文は保持せず自己申告値を証拠にしない。`run_report` は正常・失敗・拒否・途中終了の全てで壁時計時間、費用（実額/推定と根拠/不明）、承認済み上限、取得可能な親子内訳と子費用の重複有無、モデル証拠、検証結果を出す。実行前には契約を機械判定する。新規 orchestrator・plan record CLI は作らない。

Nepoforge が観測できるのは宣言契約と記録されたイベントだけである。契約外の手作業、別セッション/CLI/provider利用、未報告の費用、実環境の強制支出、権限設定変更を検知・阻止できない。対策は宣言の網羅確認、実行ログ突合、差分/PRでの人間レビューであり、完全な強制を謳わない。

## 7. 代替案・影響範囲・未解決

採用案は strict と最小 gate。旧 CLI 利用者の互換性は保つが、配布 `/nfgo` は必ず strict を使う。完全DB/自動オーケストレーター案は #12 の責務・architecture §8 の導入条件を超えるため不採用。Markdown 進捗だけでは証拠・予算の機械判定ができず strict gate の代替にならない。

CLI/ledger は別担当。人間承認済み上限には親実績と追加分を含め、予算・権限拒否から実装等への直接作業へ迂回しない。決定的ツールのローカル計測に任せ、追加モデル起動は禁止。設定変更・課金実機試験は行わない。

未解決: Primary 自身の支出・選択/実行モデル証拠を各実行エンジンからどの API で得るか、現在の費用入力の信頼性と run をまたぐ人間承認済み精算形式は実装前に確認する必要がある。取得不能なら strict paid run は拒否する。実装は小さな契約 validator/ready/record を先行し、#12 の完全収集を待つ設計にはしない。
