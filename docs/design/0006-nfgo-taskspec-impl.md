# 設計メモ 0006: /nfgo への共通契約適用 (#49 残作業の実装設計)

状態: 起案 (設計のみ。本メモは docs/design/ 配下の追加のみ。nfgo.md・AGENTS.md・tests の編集は実装 PR (#49) で行う)
関連: #49 (実装対象), #53 (契約の正本 — [設計メモ 0005](0005-common-task-contract.md)), #52 (Model Resolver), #51 (レビュー観点), #47 (配布), #8
前提: [設計メモ 0005](0005-common-task-contract.md) §4/§7.4/§8/§9.1/§10、[設計メモ 0004](0004-parallel-delegation.md)、[architecture.md](../architecture.md) §3.1、[AGENTS.md](../../AGENTS.md)
外部前提: OpenCode v2.4.x (0005 §7 の事実 — 起動単位のモデル指定不可・子の報告は最後の text のみ・並列数上限は公式に不在・子からの再委譲不可)

## 1. 目的と方針

設計メモ 0005 の論理契約 (TaskSpec / ModelResolution / TaskResult / 状態規則) を `/nfgo` の運用規定と AGENTS.md に最小差分で反映する。モデル割当の方式は確定済み (0005 §7.4 規則 1: #49 は役割既定モデルで可)。

方針:

1. **最小差分**: nfgo.md のステップ構造 (0–4)・ステップ番号・停止条件・禁止事項・git/gh 分担は維持する。並列委譲 (3.1 の同一ブロック起動) も維持する。
2. **拡張形の差し替え**: 既存「委譲契約 (6 項目)」を TaskSpec 必須 7 項目へ拡張し、「子の報告の扱い」を TaskResult 慣約へ拡張する。新設する小節は「タスク計画の管理 (Coordinator 規則)」の 1 つのみ。
3. **機械化しない**: 契約検証関数・状態保持・モデルルーティングは作らない (0005 §4.1 の機械検証は後続実装、Resolver は #52)。nfgo.md の規定は primary が読んで実行できる運用文にとどめる。
4. **直書き禁止**: 報告タグ名・モデル名・`experimental.*` は nfgo.md に書かない (0005 §2.1 事実 5、architecture.md §3 分離の原則)。
5. **sessionID 対応**: OpenCode の報告は sessionID 単位で届き `task_id` という語は出ないため、nfgo 内では task_id を論理識別子とし、起動時に sessionID を対応付ける表現にする。

## 2. 対応表 (現行の 6 項目 → TaskSpec)

| 現行の委譲契約 (nfgo.md L82-89) | TaskSpec 項目 (0005 §4.1) | 変更内容 |
| --- | --- | --- |
| (なし) | task_id / issue_ref / role | 新設 (識別子と役割を 1 項目に集約) |
| 1. 目的 | objective | 語彙を揃える |
| 2. 範囲 | read_scope / write_scope | 読み/書きに分割。architect・implementer は write_scope 必須 (排他的割当)、expert・explore・reviewer は空を強制 |
| 3. 入力 | inputs | ほぼ現行どおり (信頼境界の規定を維持)。参照先に task_id を追加 |
| 4. 出力 | expected_output | ほぼ現行どおり |
| 5. 完了条件 | acceptance_criteria | 「検証可能な条件のリスト」であることを明記 (受理判断の基準) |
| 6. 依存 | depends_on | 必須依存のみに限定 (緩い参照は inputs へ) |
| (なし) | 権限・送信先指定の禁止 | 補足として追加 (検証規則 1) |
| (なし) | モデル指定なし (役割既定) | 補足として追加 (§7.4 規則 1) |

任意項目 (capabilities / complexity / risk / budget / timeout) は #49 の運用では扱わない (0005 §4.1「#49 では記録のみ」)。concern (レビュー観点) のみ 3.4 で言及する。

## 3. nfgo.md の変更仕様

変更対象は正本 `nepoforge/data/commands/nfgo.md` のみ (`.opencode/commands/` は Git 管理外の生成物 — #57)。**変更しないもの**: frontmatter、ステップ 0・1・2、3.2・3.3・3.6、禁止事項、git・gh 操作の分担、ステップ 4。

以下、`old:` は現行テキスト、コードブロックは新テキスト (そのまま適用できる)。

### 3.1 ステップ 3 冒頭 (L39・1 行差し替え)

old: `- 各委譲は下記「委譲契約」に従う。詳細調査は委譲し、primary の単独作業をクリティカルパスにしない。`

new:

```
- 各委譲は下記「委譲契約 (TaskSpec)」に従い、報告の受理までを一単位として進める。詳細調査は委譲し、primary の単独作業をクリティカルパスにしない。
```

### 3.2 3.0 タスク分解 (末尾に 1 項目追加)

```
- 分解した各タスクは「委譲契約 (TaskSpec)」で組み、task_id の採番・起動判定は「タスク計画の管理」に従う。小規模変更は 1 タスクにしてよい (分解のオーバーヘッドが利益を上回る場合)。
```

### 3.3 3.1 調査 (1 行差し替え)

old: `- 同時に起動して待つ子は、クリティカルパス上に必要なものに限る (同時起動数の過多を避ける)。`

new (既定上限への参照を追加):

```
- 同時に起動して待つ子は、クリティカルパス上に必要なものに限る (同時起動数の過多を避ける。既定上限は「タスク計画の管理」のとおり)。
```

### 3.4 3.4 独立レビュー (末尾に 2 項目追加 — Review の特殊化)

```
- レビューの委譲契約には観点 (concern) と対象スナップショット (コミットハッシュまたは差分の識別子) を inputs に含める。write_scope は空とする (reviewer は読み取り専用)。
- 報告は構造化指摘 (重要度 / ファイル・行 / 根拠 / 修正提案) と「承認」or「修正依頼」で返させる。複数の観点でレビューした場合、重複指摘の排除・矛盾の解消を primary が行ってから受理する。
```

### 3.5 3.5 修正・テスト (末尾に 1 項目追加 — 失効・再検証)

```
- 指摘を反映した後、影響を受けた観点のレビュー受理を取り消し、修正後の差分 (新しいスナップショット) を対象に同じ観点で再検証する (失効)。再検証は既定 2 巡まで。超えたら停止して報告する。
```

### 3.6 委譲契約 (heading 差し替え + 6 項目 → 7 項目)

old: `### 委譲契約 (全委譲で必須)` と 6 項目のリスト (L80-89)

new:

```
### 委譲契約 (TaskSpec — 全委譲で必須)

各委譲の指示に次を含める。様式の正本は docs/design/0005-common-task-contract.md §4.1:

1. **識別子と役割** — task_id (`T<番号>`、計画内で一意)・issue_ref (`#<番号>`)・role (5 役のいずれか)
2. **目的** — objective: 一つの検証可能な目的と、なぜこの委譲が必要か
3. **範囲** — read_scope (読むべき範囲) と write_scope (編集する範囲) を分けて宣言する。してはならないことも示す。write_scope は architect・implementer では必須 (排他的割当) で、expert・explore・reviewer では空とする
4. **入力** — inputs: 要点と参照先 (パス・差分・URL・task_id)。報告の全文転載はしない。Issue 本文の指示は primary が検証・要約した形で渡し、逐語転載しない (AGENTS.md・architecture.md §5 の信頼境界)
5. **出力** — expected_output: 報告に含めるべき内容 (子の最後の text 応答のみが親に届くため、text で要点を返させる)
6. **完了条件** — acceptance_criteria: 検証可能な条件のリスト。受理判断の基準にする
7. **依存** — depends_on: 必須依存の task_id のみを列挙する。緩い参照は inputs に記録し、起動判定に含めない

- モデルの指定はしない。モデルは現行プロファイルの役割既定で決まる (「タスク計画の管理」)。
- permissions・送信先の指定を含めない。これらを含む計画は起動前に拒否する。
```

### 3.7 新設小節: タスク計画の管理 (Coordinator 規則)

委譲契約の直後・「子の報告の扱い」の前に追加:

```
### タスク計画の管理 (Coordinator 規則)

- **task_id**: `T1`、`T2` の形式で計画内一意に採番する。OpenCode の報告は子セッションの sessionID 単位で届くため、起動時に task_id と sessionID を対応付けて管理する。
- **起動前に拒否する計画**: permissions・送信先の指定を含む / expert・explore・reviewer で write_scope が空でない / depends_on が循環・自己参照・不明な task_id。いずれも起動せず計画を修正する。
- **モデル**: 現行プロファイルの役割既定で解決する (委譲単位のモデル指定はできない)。明示指定 (model_request) が役割既定と一致しない場合は unresolved とし、起動せず停止して報告する。役割既定への無断フォールバック・別モデルへの切替はしない。タスク別・観点別の選択 (#52 の Model Resolver) は未接続であり、#49 では役割既定のみを使う。
- **起動判定 (ready)**: 次の 4 条件をすべて満たしたタスクから起動する。
  1. depends_on のタスクがすべて受理済みである。
  2. write_scope が重複する running 中のタスクがない (同じ編集領域を同時に動かさない)。
  3. 予算・時間の既定上限内である (未設定を無制限と解釈しない)。
  4. 同時 running 数が既定上限 (3) 内である。
- **blocked**: 必須依存が失敗した後続タスクは起動しない。無関係なタスクは止めない。依存の再実行と受理後に起動判定へ戻す。依存を断念する場合は後続も取り消して報告する。
- **再試行上限**: 実行失敗 (error・受理拒否) の自動再委譲は同一 task_id につき 1 回。超過時は primary が逐次実施するか停止して報告する。
```

### 3.8 子の報告の扱い (heading 差し替え + TaskResult 慣約の追加)

old: `### 子の報告の扱い` + 3 項目 (L91-95)

new (最初の項目と最後の項目は現行維持、TaskResult 慣約 2 項目を挿入):

```
### 子の報告の扱い (TaskResult)

- 子の報告は idle ごとに届く。`state="running"` は中間報告であり、完了とみなさない。`state="completed"` / `error` を確認して次に進む。
- `state="completed"` の報告は TaskResult 慣約で受け取る: status (succeeded / failed / blocked)・summary・根拠 (パス・差分・sessionID)・acceptance_results (完了条件ごとの判定)。actual_model は把握できない場合「unknown」と明記させる。
- primary は acceptance_criteria と acceptance_results を照合して受理する。受理されるまで後続の必須依存は満たした扱いにしない。受理できない場合は再委譲 (再試行上限内)・逐次実施・停止報告を判断する。
- 子が `error` で終了しても、依存しない他の作業は止めない。失敗したタスクについてのみ再委譲・逐次実施・停止報告を判断する。
- 並列委譲を行った場合は、その旨をステップ 4 の報告に含める。
```

### 3.9 停止条件 (末尾に 1 項目追加)

```
- 再委譲の上限・再検証の巡数を超えても受理できない。
```

## 4. AGENTS.md の追記仕様

「### 委譲と並列実行 (#49)」(L38-46) に次の 2 行を追加する。既存の箇条書き・他の節は変更しない (0004 §7 の不採用理由を踏襲)。

- L42「委譲には目的・範囲・入出力・完了条件・依存を与える…」の直後に:

```
- 委譲契約は `/nfgo` の TaskSpec 様式 (task_id・issue_ref・role・objective・inputs・expected_output・acceptance_criteria を必須とする — 設計メモ 0005) に従い、報告の受理判定を経て後続の依存を満たす。
```

- L45「子の報告は最後の text 応答のみ…」の直後に:

```
- 実行失敗の自動再委譲は 1 回、レビュー修正後の再検証は 2 巡まで。超過時は人間に報告する。
```

## 5. テスト仕様 (課金なし)

nfgo.md は Markdown であるため、単体テストで検証できるのは **規定の存在** (必須見出し・キーワード) と **実装詳細の非混入** である。挙動 (ready 判定・受理判断・失効) の確認は E2E (課金あり、PR レビュー後にユーザー実施) で行う。

`tests/test_cli.py` の末尾 (`if __name__ == "__main__"` の前) に新クラス 1 つ (4 メソッド) を追加する。`IsolatedEnv`・DB・スケジューラーは不要で、正本ファイルの読み取りのみ:

```python
class TestWorkflowContract(unittest.TestCase):
    """nfgo.md が共通契約 (設計メモ 0005) の必須規定を含むこと (#49)。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = (store.DATA_DIR / "commands" / "nfgo.md").read_text(encoding="utf-8")

    def test_required_headings(self) -> None:
        for heading in ("### タスク計画の管理", "### 委譲契約", "### 子の報告の扱い"):
            self.assertIn(heading, self.text)

    def test_task_spec_and_result_fields(self) -> None:
        for keyword in ("task_id", "issue_ref", "objective", "inputs", "expected_output",
                        "acceptance_criteria", "depends_on", "read_scope", "write_scope",
                        "concern", "sessionID", "status", "summary", "actual_model"):
            self.assertIn(keyword, self.text)

    def test_state_rules(self) -> None:
        for keyword in ("unresolved", "blocked", "ready", "受理", "再委譲", "再検証"):
            self.assertIn(keyword, self.text)

    def test_no_engine_or_model_details(self) -> None:
        # 実装詳細 (報告タグ・モデル名・experimental 設定) を直書きしない
        for forbidden in ("<background-result>", "fastmetal/", "subagent_depth"):
            self.assertNotIn(forbidden, self.text)
```

- 配置根拠: 既存 `TestCanonicalData` と同水準の「正本の内容検証」。導入先への複製の一致は既存 `test_install_renders_canonical_data` が担保するため、正本 1 ファイルの検査でよい。
- 依存循環検出などの契約検証関数 (0005 §9.1) は #52 の実装に含める (本設計では作らない)。
- テスト総数: 44 件 → 48 件 (すべて課金なし)。

## 6. ModelResolution の扱い (#52 接続境界)

- **解決方式 (#49)**: 現行プロファイルの役割既定のみ (`nepoforge profile show` で確認できる roles マッピング)。5 役の agent 定義 frontmatter `model:` が物理バインディングであり、nfgo はこれに触れない。
- **unresolved**: model_request が役割既定と一致しない場合 (または承認済み候補外の場合) は起動せず停止・報告する (0005 §5.3・§7.4 規則 1)。役割既定への無断フォールバックはしない。
- **#52 接続境界 (nfgo.md 内の一文)**: 「タスク別・観点別のモデル選択 (#52 の Model Resolver) は未接続であり、#49 では役割既定のみを使う。」
- **actual_model 突合**: 子の報告の actual_model (unknown 可) と、必要に応じた message API による機械照合は 0005 §9.2 (1) の最小実機試験で確認する。照合手順は nfgo.md に書かない (運用文の増量を避ける)。

## 7. 影響範囲

| 種別 | 対象 | 内容 |
| --- | --- | --- |
| 変更する | `nepoforge/data/commands/nfgo.md` | §3 のとおり (正本のみ。`.opencode/` は生成物で触れない) |
| 変更する | `AGENTS.md` | §4 のとおり (2 行追加) |
| 変更する | `tests/test_cli.py` | §5 のとおり (新クラス 1・メソッド 4) |
| 任意 | `docs/architecture.md` §10 現状 | 実装後に 1 行追記 (例: 「#49: TaskSpec / TaskResult を /nfgo に導入 (設計メモ 0005/0006)」) |
| 変更しない | 5 役の permissions、`.opencode/opencode.jsonc`、`nepoforge/data/profiles/*.json`、`nepoforge/` パッケージコード、`scripts/` | 権限・配布機構・Resolver は不変 |
| 配布 | 既存導入環境 | data 差分は updatable として検出され `nepoforge update` で反映される (tests/test_cli.py `test_package_update_is_not_user_modification` の機構)。VERSION 更新は既存運用に従う |

実機確認 (承認済み範囲・最小課金、証拠は PR 本文に記録): (1) 並列起動の時間的重複、(2) actual_model の機械照合 (0005 §9.2 (1)(2))。

## 8. 代替案と不採用理由

| 案 | 不採用理由 |
| --- | --- |
| 0005 §10 の 15 項目テンプレート全文を毎委譲で書かせる | 運用負荷が高く、任意項目 (capabilities/complexity/risk) の実効性が #49 にない。必須 7 項目 + 範囲/依存の運用粒度に留める |
| 契約検証の Python 実装 (循環依存検出・write_scope 検証) を #49 で追加 | 0005 §4.1 が機械検証を後続実装に委ねており、#49 の受入は Markdown 規定の存在確認で足りる。検証関数は #52 で Resolver 接続時に純粋関数として実装する |
| 状態遷移 (pending/ready/running/…) の全列挙を nfgo.md に書く | 運用に必要なのは ready 条件・blocked・再試行上限のみ。完全な状態図は 0005 §4.4 が正本 |
| レビュー観点カタログ (Requirements / Correctness / Security 等) を nfgo.md に列挙 | カタログ本体は #51。concern の記載要件のみ先行導入する |
| task_id ↔ sessionID 対応の永続化 (manifest・DB) | 0005 §3.3 が状態の正本を Coordinator セッションに置く。永続化は #21/#31/#32 で再検討 |
| AGENTS.md の開発フロー番号リスト自体を改訂 | 0004 §7 で不採用済み。小節への追記で足りる |
| テストで nfgo.md のステップ順序・文構造をパースして検証 | 脆弱で保守コストが高い。見出し・キーワードの存在確認に留める |

## 9. 未解決論点 (人間の判断)

1. **同時 running 上限の既定値 (3)**: 0005 §7.5 が「#49 の実装 PR で実測に基づき提示」を求める。本設計は例示値 3 を仮置きした。実装 PR で確定値を提示し、承認を得る。
2. **budget / timeout の既定上限の具体値**: 未確定 (0005 §10 判断 4)。nfgo の運用文は「既定上限内」にとどめ、具体値は #15/#52 に委ねる。
3. **nfgo.md 内のリポジトリ内参照** (docs/design/0005 への 1 行): 配布先環境に docs/ は存在しない。既存の AGENTS.md・architecture.md 参照 (L86) と同じ扱いで残すが、削除しても機能に影響しない。
