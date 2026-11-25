# 設計メモ 0007: タスク別モデルルーティング — 共通 Model Resolver と派生エージェント定義 (#52)

状態: 承認済み (2026-11-25 人間承認: 命名 `nf-<role>-<slug>`、0005 改訂の先行反映、最小実機試験の実施)。実装と検証は #52
関連: #52 (本設計), #53 (契約の正本 — [設計メモ 0005](0005-common-task-contract.md)), #49 (基盤実装 — [設計メモ 0006](0006-nfgo-taskspec-impl.md)), #51 (レビュー観点), #47/#57 (配布), #19 (第 2 プロバイダー), #14/#12 (評価・記録)
前提: [設計メモ 0005](0005-common-task-contract.md) §3.3・§4・§5・§6・§7.4・§7.5・§8・§9、[設計メモ 0006](0006-nfgo-taskspec-impl.md)、[設計メモ 0001](0001-agents-profiles-permissions.md) §2・§3、[設計メモ 0003](0003-package-cli.md) §4・§5・§7、[architecture.md](../architecture.md) §3・§5、[AGENTS.md](../../AGENTS.md)
外部前提: OpenCode v2.4.0 (0005 §7 の事実 — 起動単位のモデル指定不可・子のモデルはエージェント定義の `model` で決まる・設定は自動リロードされるが実行中リクエストは開始時設定を保持・message API で agent/model を機械可読取得可)
性質: 設計のみ。コード・課金試験は行わない。

## 0. 0005 の論理フィールドについて (本設計の原則)

本設計は 0005 §4 で確定した **TaskSpec / ModelResolution / TaskResult の論理フィールドを一切変更しない**。追加するのは次の 4 種で、いずれも論理契約の**表現の明確化**に限る。

1. 設定の表現: プロファイルの optional キー (`candidates` / `task_rules` / `constraints`) — §3
2. 選択手順の機械化: 純粋関数としての Model Resolver — §4
3. 生成物: 承認済み role × model の派生エージェント定義とその管理 — §5
4. 運用規定の明確化: 細粒度分解・並列上限・観測 — §6・§7

## 1. 目的・スコープ・非スコープ

### 1.1 目的

0005 §7.4 は role×model 割当の方式の骨子 (派生定義生成を正規経路とする) のみを定めており、設定スキーマ・生成/保護/ライフサイクル・検証方法が未確定である。本設計でこれらを確定し、#52 の実装者がそのまま着手できる粒度にする。

- 共通 Model Resolver (全役割共通。レビュー専用ルーターは作らない — 0005 §4.5)
- 承認済み候補とプロファイル互換の明文化 (roles のみの既存プロファイルが無変更で動く)
- 派生エージェント定義の生成・保護・ライフサイクル
- 実行モデルの観測 (resolved_model と actual_model の突合)
- 細粒度分解方針の設計文書への先行反映 (#52 の追加指示)

### 1.2 スコープ

- 0005 §5 の論理仕様を、設定スキーマ・純粋関数・CLI に落とすこと。
- 0005 §7.4 規則 2 の派生定義生成について、命名・配置・衝突・冪等・配布整合を確定すること。
- 0005 §4.2 の決定的ツール判定と Resolver の接続境界を確定すること。
- 0005 改訂案 (§2) と `/nfgo`・AGENTS.md・architecture への最小追記案 (§7・§8)。

### 1.3 非スコープ

- 自動学習ルーター・モデル品質の自動判定 (0005 §1.1 対象外。品質比較は #14 の全面)。
- 大規模ベンチマーク (#13/#14)、独自オーケストレーター・独自実行基盤 (architecture §8 の導入条件を満たすまで作らない)、永続グラフ・DB (#21/#31/#32)。
- 第 2 プロバイダー対応 (#19。スキーマは provider を一般化して書くが、検証は現行 fastmetal に限定)。
- budget の実強制 (fastmetal の cost は常に 0 — 0001 §3。#52 ではフィールドと検証のみ。強制は #15)。
- レビュー観点カタログ・観点別割当の適用例 (#51)。

## 2. 0005 との関係と改訂案

### 2.1 関係

- 0005 が本設計の**契約の正本**である。本設計はその §5 (Resolver 論理) と §7.4 (派生定義方式) を実装可能な粒度に具体化するものであり、契約を分岐させない。
- 0005 §9 の後続対応表どおり、#52 は「共通 Model Resolver・承認済み候補・プロファイル互換・実行モデルの観測」を責務とする。#49 (実装済み — 0006) の役割既定モデル方式を置き換えず、**優先順位の最終段 (役割デフォルト) として残す**。

### 2.2 0005 への改訂案 (採否判断付き)

細粒度分解方針 (T1e 調査の差分案) を含む改訂提案。すべて「表現の明確化」であり、論理フィールドの変更を含まない。0005 の改訂は契約改訂であるため、#52 実装 PR の**先頭コミット**として設計文書を先行反映する (0005 §1「契約変更は共通設計の改訂が先」)。

| # | 対象 | 差分案 | 判断 |
| --- | --- | --- | --- |
| R1 | 0005 §3.3 規則 5・7 | 「全部の調査完了を一律に待たず」「編集は排他的に割当たった範囲内でのみ並列」に、**工程バリアを作らない** (調査→設計→実装の工程単位の一括直列化をしない) ことを明記 | 採用 |
| R2 | 0005 §4.4 ready 条件 d | 「並列上限内 (§7.5)」に「既定上限はプロファイルの `constraints.max_parallel` (未設定なら 3)」を補記 | 採用 |
| R3 | 0005 §5.1 タスク別ルール | 「設定はプロファイル側に置く」に「スキーマと照合順序は設計メモ 0007 §3/§4」の参照を追加 | 採用 |
| R4 | 0005 §7.4 規則 2 | 派生定義の**命名を `nf-<role>-<slug>` に確定** (0005 は `<role>-<slug>` を例示し、命名確定を #52 に委ねていた)。生成範囲を「承認済み候補 × role の全組合せ」から「**プロファイルの task_rules が参照する (role, model) ペア + 明示要求されたペア**」に絞る (25 ファイル相当の無用な生成を避ける) | 採用 (0005 が #52 に委ねていた項目の確定) |
| R5 | 0005 §7.5 | 同時 running 既定 3 を**暫定既定**と明記し、調整方法 (`constraints.max_parallel`) を記載 | 採用 |
| R6 | 0005 §8 影響範囲表 | #52 行の変更対象に `nepoforge/resolver.py`・`nepoforge/derive.py`・`tests/test_resolver.py`・`tests/test_derive.py` を追加 | 採用 |
| R7 | AGENTS.md「委譲と並列実行」 | 細粒度分解の 1 行を追加 (§7.2)。数値 (同時 running 上限) は AGENTS.md に書かない (T1e 確認: 現行の記載なし) | 採用 |
| R8 | nfgo.md 3.0 と「タスク計画の管理」 | バリア禁止・上限の調整可能性・Resolver 接続文の更新 (§7)。**直書き禁止ルール (報告タグ・モデル名・`experimental.*` を書かない) を遵守**する | 採用 |
| R9 | architecture.md §3.1 規則 3 | 「細かく分割したタスクはプロファイルのタスク別ルールでモデルを選択でき、依存が揃った順に起動する (工程バリアを作らない。設計メモ 0007)」の 1 文を追加 | 採用 |

不採用とした T1e 関連の候補: AGENTS.md への同時 running 数の記載 (運用値はプロファイル・nfgo 側に置き、規約文書に数値を散らさない)。0005 §4.1 TaskSpec への新フィールド追加 (論理フィールド変更に当たるため行わない。細粒度分解は既存の complexity / risk / capabilities で表現できる — T1e 結論)。

## 3. プロファイル設定スキーマの拡張 (後方互換)

### 3.1 現状の事実 (読み取り確認済み)

1. `profiles.load_profile` は `roles` マッピングのみを検査し、**トップレベルの追加キーは許容して読み捨てる**。戻り値は `{role: model}` (`nepoforge/profiles.py` L36-67)。
2. `profiles.apply_to_agent` は frontmatter の `model:` 行のみ差し替え、行欠如で例外 (黙って追加しない)。派生生成にそのまま再利用できる。
3. `profiles.render` は `rel` が `agents/` で始まり、かつ stem が profile のキーに一致する場合のみ適用する。**派生定義 (DATA_DIR に存在しない) には無影響**。
4. 承認済み候補の初期定義 (0005 §5.2) = 同梱 3 プロファイル roles の和集合 + local の値 = 次の 5 モデル (すべて fastmetal):

   | model | 採用プロファイル |
   | --- | --- |
   | `fastmetal/japan-glm-5.3-flash` | economy (全役割) / balanced (判断系 4 役) / performance (explore) |
   | `fastmetal/beam-1.0` | balanced (implementer) |
   | `fastmetal/glm-5.3` | performance (architect / reviewer) |
   | `fastmetal/japan-deepseek-v4.1-flash-1017` | performance (expert) |
   | `fastmetal/kimi-k3.1-pro` | performance (implementer) |

5. モデル表記: OpenCode スキーマは `^[^/#]+\/[^#]+(?:#[^#]+)?$` (`provider/model` + 任意 `#variant`)。nepoforge の `MODEL_RE` は model 部に `#` を許すため `provider/x#y` も通るが、OpenCode では `model=x, variant=y` と解釈される。**派生生成・Resolver では厳密版 (`parse_model`) を通す**。

### 3.2 スキーマ

roles のみの既存プロファイルは**無変更でそのまま動く** (`balanced.json` 現状のままで `load_profile_config("balanced")` が成立する)。追加キーはすべて optional で、省略時は既定値を使う。

```json
{
  "name": "balanced",
  "description": "バランス型(既定)。...",
  "roles": {
    "architect": "fastmetal/japan-glm-5.3-flash",
    "expert": "fastmetal/japan-glm-5.3-flash",
    "explore": "fastmetal/japan-glm-5.3-flash",
    "implementer": "fastmetal/beam-1.0",
    "reviewer": "fastmetal/japan-glm-5.3-flash"
  },
  "candidates": [
    {
      "model": "fastmetal/kimi-k3.1-pro",
      "capabilities": ["code", "reasoning"],
      "context_tokens": null,
      "destination": "FastMetal 国内ホスト",
      "retention": "学習利用なし",
      "approval_ref": "設計メモ 0001 §3 の選定記録 (人間レビュー済み, 2026-11-24)"
    }
  ],
  "task_rules": [
    {
      "when": { "role": "implementer", "complexity": "high" },
      "model": "fastmetal/kimi-k3.1-pro",
      "note": "高複雑度の実装はフラッグシップ推論に割当てる"
    },
    {
      "when": { "risk": "low", "complexity": "low" },
      "model": "fastmetal/japan-glm-5.3-flash",
      "note": "低リスク・低複雑度は軽量モデル"
    }
  ],
  "constraints": {
    "providers": [
      {
        "id": "fastmetal",
        "destination": "FastMetal 国内ホスト",
        "retention": "学習利用なし",
        "approved": true,
        "approval_ref": "0001 §3 / #52 PR"
      }
    ],
    "max_parallel": 3,
    "budget": { "per_task": null, "note": "#52 では強制しない (#15 で実装)" }
  }
}
```

### 3.3 検証規則 (`load_profile_config`)

`load_profile` (roles のみ、戻り値 `{role: model}`) は**現状のまま無変更**で維持する (既存テスト `test_profiles.TestLoadProfile` が `prof[role]` アクセスに依存するため)。新設する `load_profile_config(name) -> ProfileConfig` が optional キーを検証する:

1. トップレベルの不明キーは従来どおり許容 (読み捨て)。既存の動作を変えない。
2. `candidates`: 配列。各要素の `model` は `parse_model` が成功し (`provider/model[#variant]`、`#` は高々 1 個)、重複がないこと。
3. `task_rules`: 配列。`when` は空でなく、キーは `{role, capabilities, complexity, risk, concern}` の部分集合。`complexity` / `risk` は `low|medium|high`、`role` は 5 役、`capabilities` は文字列の配列。各ルールの `model` は有効候補に含まれること (含まれない場合はロード時にエラー — 実行時ではなく設定時に壊れたルールを検出する)。
4. `constraints.providers`: 配列。各要素は `id` (文字列)・`approved` (真偽)・`destination`・`retention` (文字列) を必須とする。候補の provider は `approved: true` の provider に含まれること。
5. `constraints.max_parallel`: 1 以上の整数。
6. **roles の値は常に有効候補に含める** (暗黙追加)。`candidates` 省略時の既定 = 同梱 3 プロファイル roles の和集合 + local の roles の値 (0005 §5.2 の初期定義と一致 — §3.1 事実 4 の 5 モデル)。`candidates` 明示時は roles ⊆ candidates を検証しない代わりに、roles の値を候補に union する (ローカルで新モデルを roles に足すだけで承認済み候補になる。承認の主体は「プロファイルに書いた人間」)。
7. `local` プロファイル (`~/.config/nepoforge/profile.json`): roles 必須は現行どおり。optional キーも同一スキーマで検証する。省略時は既定値 (§3.3 規則 6)。

### 3.4 既定値の確定

| キー | 省略時の既定 |
| --- | --- |
| `candidates` | 同梱 3 プロファイル roles の和集合 + local の roles の値 (capabilities / context_tokens は null = 情報なし) |
| `task_rules` | 空配列 (Resolver は 明示指定 → 役割デフォルト の 2 段で動く) |
| `constraints.providers` | `[{"id": "fastmetal", "approved": true, ...}]` (同梱プロファイルに登場する provider の集合) |
| `constraints.max_parallel` | 3 (暫定既定 — 0005 §7.5) |
| `constraints.budget` | `{ "per_task": null, "note": "..." }` |

## 4. Model Resolver

### 4.1 配置と関数シグネチャ (案)

`nepoforge/resolver.py` を新設する。**I/O を含まない純粋関数**とし、単体テストを課金なしで書けるようにする。設定の読み込み (I/O) は `nepoforge/profiles.py` に置く。

```python
# nepoforge/profiles.py (既存モジュールへ追加)
@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str
    variant: str | None

def parse_model(text: str) -> ModelRef: ...     # 不正表記は NepoforgeError
def format_model(ref: ModelRef) -> str: ...     # "provider/model" (+ "#variant")

@dataclass(frozen=True)
class Candidate:
    ref: ModelRef
    capabilities: tuple[str, ...] | None        # None = 情報なし (フィルタで除外しない)
    context_tokens: int | None
    destination: str | None
    retention: str | None
    approval_ref: str | None

@dataclass(frozen=True)
class ProfileConfig:
    name: str
    roles: dict[str, str]
    candidates: tuple[Candidate, ...]
    task_rules: tuple[TaskRule, ...]
    constraints: "Constraints"

def load_profile_config(name: str) -> ProfileConfig: ...

# nepoforge/resolver.py (新規・純粋関数のみ)
@dataclass(frozen=True)
class TaskInput:
    task_id: str
    role: str                                   # 5 役のいずれか
    capabilities: tuple[str, ...]
    complexity: str                             # low / medium / high
    risk: str                                   # low / medium / high
    concern: str | None
    context_tokens: int | None
    model_request: ModelRef | None
    model_request_source: str                   # "human-config" / "coordinator" / "external"
    executor: str                               # "tool" / "agent" (Coordinator の判断を受ける)

@dataclass(frozen=True)
class ModelResolution:
    task_id: str
    executor: str
    role: str
    resolved_model: ModelRef | None             # executor=tool は None
    binding: str                                # 物理バインディング (エージェント ID)
    reason: str                                 # 例: "explicit(human-config)" / "task_rule[0]" / "role_default"
    constraints_applied: tuple[str, ...]
    status: str                                 # selected / unresolved

def resolve(task: TaskInput, config: ProfileConfig) -> ModelResolution: ...
```

### 4.2 アルゴリズム (決定的・優先順位固定)

```text
resolve(task, config):
  0. 決定的ツール判定 (executor)
     - executor は Coordinator の運用判断 (nfgo「委譲しない作業」) を入力として受け取る。
       Resolver 内で自然言語を機械判定しない (§9)。
     - executor == "tool" → resolved_model=None, binding="-", status=selected で返す。
  1. 候補フィルタ (適格条件 — 0005 §5.2)
     config.candidates から次を除外する:
       a. provider が constraints.providers の approved=true に含まれない
       b. task.capabilities の一部が candidate.capabilities に欠ける (candidate.capabilities
          が null の場合は除外しない — 能力情報の機械源がなく、人間承認の選定記録で代替 — 0005 §5.2)
       c. task.context_tokens と candidate.context_tokens が両方既知で、候補が不足
       d. budget: per_task が設定済みで候補が適合しない (#52 では実強制なし。プレースホルダ)
  2. 明示指定 (優先順位 1 — 0005 §5.1)
     task.model_request があれば:
       - model_request_source == "external" → unresolved (理由: 信頼できない出所)。
         Issue 本文・子の報告由来の指定は 0005 §4.1 検証規則 4 により禁止。
       - フィルタ済み候補に含まれない → unresolved (候補外の無条件拒否)。
       - 含まれる → resolved_model=model_request。
  3. task_rules マッチ (優先順位 2)
     - **宣言順に走査し、最初に when が一致したルールを採用** (先頭一致・決定的)。
     - when の各キーは AND 結合。capabilities は「タスクの capabilities が when の
       リストに 1 つでも含まれる」(any-of)。
     - 採用ルールの model がフィルタ済み候補に含まれない場合、そのルールは不適用として
       **走査を続行**し、理由に "rule[i] skipped (model ineligible)" を記録する
       (環境条件による一時的な不適格で計画全体を止めないため。無断フォールバックではない —
       役割デフォルトは承認済みポリシーの明示的な最終段である)。
     - 一致ルールがあれば resolved_model=その model。
  4. 役割デフォルト (優先順位 3 — 常に存在)
     - config.roles[role] (roles ⊆ candidates は設定検証で保証 — §3.3 規則 6)。
     - 役割デフォルトのモデルまでフィルタで除外された場合のみ unresolved。
  5. unresolved の場合
     - status=unresolved, reason に不足条件を記録して返す。
       **役割既定への無断フォールバック・別モデルへの無断切替は行わない** (0005 §5.3)。
  6. binding (物理バインディング)
     - resolved_model が roles[role] と完全一致 (variant 込み) → 基本定義 "<role>"
     - 異なる → 派生定義 "nf-<role>-<slug>" (§5.2)。生成済みかの確認は derive 側 (§5.4)。
```

決定性の保証: `resolve` は時刻・乱数・環境・I/O に依存せず、同一入力に同一出力を返す (単体テストで 2 回呼び出しの同値を検証する)。

### 4.2.1 決定的ツール判定との接続

0006 §8 で「executor 選択そのものは #52 の Resolver 接続時に実装」とした点の確定: 判定自体は **Coordinator の運用判断** (nfgo.md「委譲しない作業」規定) のまま残し、Resolver は `TaskInput.executor` として受け取る。テスト実行・glob/grep 集計・lint 等の標準ツールで達成できる作業を LLM タスクにしない規定は nfgo に既にあり、これを機械化しない (§9)。

### 4.3 unresolved 時の停止動作

- Resolver をライブラリから使う場合 (Coordinator): status=unresolved は**委譲しない**。0005 §4.2 の「停止・報告」に従い、計画を修正するか人間に報告する。
- `nepoforge resolve` CLI (§5.6): unresolved は終了コード 2。
- 無断フォールバック禁止の明文化: Resolver はポリシーの外に出ない。ポリシー (= 優先順位 3 まで) で解決できない選択はすべて unresolved。

## 5. 派生エージェント定義の生成・保護・ライフサイクル

### 5.1 原則

- 派生定義は**生成物**であり、配布正本 (`nepoforge/data/`) には置かない (0005 §7.4 規則 2。TestDataSync は 5 役のみ比較するため影響なし — 0005 §2.1 事実 5)。
- 内容は元 role 定義 (`nepoforge/data/agents/<role>.md`) と **`model:` 行のみ**異なる (`profiles.apply_to_agent` を再利用)。permissions・本文・`mode: subagent` は元 role から複製され、**モデル変更で権限は拡大しない** (#52 要件)。
- 既存 5 役の定義は書き換えない (新規ファイルの追加のみ)。稼働中設定を競合更新しない (0005 §7.4)。
- 同一 (role, model) からの再生成は**冪等** (同一バイト列)。

### 5.2 命名

- エージェント ID = ファイル名: `nf-<role>-<slug>.md`。
- slug = model 部そのまま (`.` `_` `-` は保持)、`#variant` は `#` → `-` に置換。例:
  - role=`expert` × `fastmetal/kimi-k3.1-pro` → `nf-expert-kimi-k3.1-pro`
  - role=`architect` × `fastmetal/glm-5.3#think` → `nf-architect-glm-5.3-think`
- 文字種は `nf-` + `[a-z0-9][a-z0-9._-]*` に限定 (`*` `?` `#` は原理的に現れない。slug 先頭は MODEL_RE の制約により英数字)。OpenCode の組み込み ID (build / plan / general / explore / title / summary / compaction 等) とは `nf-` 接頭辞で衝突しない。
- 0005 §7.4 の例示 (`<role>-<slug>`) からの変更理由: 接頭辞なしでは **Nepoforge 生成物の所有判定が不可能**になり、uninstall / prune で利用者所有の `<role>-<slug>.md` を誤削除し得る。`nf*` 名前空間は #9 以来の「Nepoforge 所有」規約であり、`warn_nf_command_collision` と同じ発想で安全に発見できる。命名は 0005 が #52 に委ねていた項目である (R4)。

### 5.3 配置先と manifest / classify との関係

- 配置先: `~/.config/opencode/agents/nf-<role>-<slug>.md` (`store.opencode_dir()`)。OpenCode のグローバル定義として全プロジェクトで利用可能 (0003 §3.2)。
- **manifest (`USER_MANAGED_FILES`) には含めない**。根拠 (T1 調査の事実): `store.classify` は expected (現行パッケージ + プロファイル由来のハッシュ) のキーのみを走査し、expected に無い rel は分類対象にならない。逆に派生 rel を expected に入れると、manifest に記録のない既存導入環境で初回 `init`/`update` が conflict 停止する。派生定義は再生成可能な生成物であり、manifest 管理 (改変検知) の利益がない。
- 代替の保護機構: 既存ファイルとの**内容ハッシュ比較**で衝突を扱う (§5.4)。生成元が DATA_DIR 正本 + 対象モデルであるため、期待内容は常に再計算できる。

### 5.4 生成手順 (CLI)

```text
nepoforge profile derive [--profile NAME] [--role ROLE --model M] [--prune] [--dry-run] [--force]
```

1. 対象ペアの決定 (`derive.plan(config)`):
   - 既定: 現行プロファイルの `task_rules` が参照する (role, model) ペアのうち、**model がその role の役割既定と異なるもの** (役割既定と同一なら基本定義で足りる)。
   - `--role` + `--model` 指定時: そのペア 1 件 (model は当該プロファイルの有効候補に含まれることを検証。**無制限に生成しない** — 0005 §7.4)。
2. 内容生成 (`derive.derive_text(role, model)`): `(store.DATA_DIR / "agents/<role>.md")` のテキストに `profiles.apply_to_agent(role, text, format_model(model))` を適用。**生成元は DATA_DIR 正本であり、導入先の現行ファイル (利用者改変の可能性) を使わない** — 内容の再現性を保つため。
3. 配置判定 (既存導入で conflict を起こさない核心):
   - 対象ファイルが存在しない → `[write]` 計画。
   - 存在し内容が期待と一致 → no-op (`[ok]`、冪等)。
   - 存在し内容が異なる → **停止** (`[conflict]`。利用者所有か旧版生成物。移動・退避するか `--force` でバックアップ後に上書き)。
4. 書き込み。`--dry-run` では計画と差分を表示して書き込まない。差分は `store.diff_text_vs(old, new_text, label)` を使う (**`store.diff_text` は DATA_DIR 同梱ファイルとの比較にしか使えないため、DATA_DIR に無い生成物では使用不可** — T1 調査の制約)。
5. 事前生成方針: `nepoforge profile use` と `nepoforge init` は、プロファイル適用後に同じ plan を実行して**事前一括生成**する。実行セッション内で設定を書かない — 派生定義が「起動前に存在する」ことを保証し、OpenCode の自動リロードのタイミングに依存しない (0005 §7.2 事実 11)。
6. 実行時の明示指定 (model_request) で必要な派生定義が未生成の場合: Resolver は unresolved (binding 未生成) を返し、Coordinator は `nepoforge profile derive --role <role> --model <model>` (--dry-run で確認後) を shell で実行してから再解決する。**Resolver 自体はファイルを書かない** (設定変更は CLI・dry-run・バックアップの既存安全機構を通す)。

### 5.5 ライフサイクル

| 事象 | 扱い |
| --- | --- |
| `profile use` 切替 | 基本定義の model 行差し替え (現行どおり) + 新プロファイルの plan を冪等生成。**旧プロファイル由来の不要になった派生ファイルは既定では削除しない** (orphan として報告) |
| orphan の解消 | `profile derive --prune`: 現行 plan に含まれない `agents/nf-*.md` を削除 (削除前に `store.backup_file` で退避)。内容が現行派生のいずれとも一致しない (改変疑い) ファイルは既定で残す (`--force` でバックアップ後に削除) |
| `uninstall` | `agents/nf-*.md` を所有判定 (`nf*` 名前空間規約) により撤去対象にする。manifest の記録対象ではないため、**内容が現在の生成規則と一致するものは通常削除、一致しないものは既定で残す (バックアップ付き --force)**。manifest 完全撤去条件 (`len(removed) == len(USER_MANAGED_FILES)`) は**現行どおり USER_MANAGED_FILES ベースで維持**し、派生ファイルの有無で uninstall の成否を変えない |
| `status` | 現行 plan に対する derived の状態 (`ok` / `missing` / `orphan`) を情報表示に追加 (任意。exit コードに影響させない) |
| `doctor` | (任意) `nf-*.md` と現行 plan の不整合を info として報告 |

### 5.6 CLI 形のまとめ

```text
nepoforge profile derive [--profile NAME] [--role ROLE --model M] [--prune] [--dry-run] [--force]
nepoforge profile show [NAME]     # roles に加え constraints.max_parallel・task_rules 件数を表示 (既存出力に追加)
nepoforge resolve --file PATH     # 任意: TaskInput の JSON を読み ModelResolution JSON を出力
                                  # unresolved は終了コード 2。課金なしでルーティング決定を検証・監査できる
```

`resolve` コマンドは任意 (実装コストが小さい場合のみ)。実行基盤を作るものではなく、純粋関数 `resolve` の薄い CLI である。**独自実行基盤・スケジューラーは作らない** (0005 §1.1)。

### 5.7 権限 (subagent resource) の扱い

- 派生定義の permissions は元 role 定義から複製 (内容が同一であることを単体テストで検証)。派生子も親権限のサブセットではなく自分の権限で動く (0005 §6 規則 2)。
- 起動可否は親の `subagent` 権限 (resource = エージェント ID、ワイルドカード可 — T2a 調査) で制御される。guardrail テンプレートは `subagent` 権限を規定していない (0005 §2.1 事実 6) ため、利用者が明示的な allow リストを運用している場合、派生 ID (`nf-*`) は自動的には許可されない。**CLI が利用者の権限設定を書き換えることはしない**。README・設計文書に「`subagent` 権限に resource `nf-*` を allow する設定例」を案内し、`doctor` で設定状況を info 表示する (任意)。

## 6. 実行モデル観測

### 6.1 actual_model 照合の手順 (T2b 調査に基づく)

1. 委譲時に task_id ↔ sessionID を対応付ける (現行運用 — 0006 §3.7)。
2. TaskResult 受領後、message API で当該セッションの assistant メッセージを取得し、`agent` / `model` (Model.Ref = `{providerID, id, variant?}`) を機械可読に読む (0001 T2 手法)。
3. Model.Ref と resolved_model を構成要素単位で突合する (provider / model / variant の 3 項目)。
4. 不一致は 0005 §4.3 受理規則 2 に従い報告・検証する。意図しない切替と判断した場合は失敗扱い。
5. 取得不能な場合は TaskResult の actual_model を `unknown` と明記する (TaskResult 慣約 — 0006 §3.8)。

### 6.2 課金なしで確認できる検証

| 手順 | 内容 | 課金 |
| --- | --- | --- |
| `opencode api get /api/agent` / `/api/agent/<id>` | 派生定義が登録されているか・frontmatter の model 解釈 (`{providerID, id, variant?}`) を確認 | なし (LLM 呼び出しを伴わない) |
| `opencode models` との照合 | 有効候補の実在確認 (`switch_profile.py` の `verify_models_exist` と同一基準) | なし |
| `nepoforge resolve` / 単体テスト | 選択・理由・binding・決定性の検証 | なし |

### 6.3 記録する項目 / 残さない項目

| 記録する (#52 の最小範囲) | 記録しない |
| --- | --- |
| task_id・role・resolved_model (provider/model[#variant])・binding・理由 (優先順位の段階) | 委譲プロンプト本文・応答本文 (0005 §3.3 規則「報告は最後の text のみ」に従い、計測ログに本文を残さない) |
| actual_model・session_id・突合結果 | 秘密情報 (API キー等 — AGENTS.md 基本原則 2) |
| 取得可能な場合の tokens / cost (fastmetal の cost は常に 0 のため価格テーブル推定 — 0001 §3) | Issue 本文・外部文書の逐語転載 |
| started_at / ended_at (取得可能な場合のみ) | |

保存先は #52 の最小範囲では **TaskResult (報告) と PR 本文の証跡のみ**。機械格納 DB・状態の永続化は作らない (0005 §3.3 規則 1、#12 の記録基盤に委ねる)。

## 7. 細粒度分解方針の反映

0005 の論理フィールドを維持したまま (T1e 結論)、次の 3 点で表現する。

### 7.1 タスク別ルールでの表現

分解した各 TaskSpec が既に持つ `complexity` / `risk` / `capabilities` を `task_rules` の `when` に使うことで、**分解粒度とモデル選択が連動**する。例: 大きい実装を complexity=high と low のサブタスクに分ければ、前者はフラッグシップ・後者は軽量モデルに自動配分される。TaskSpec 側に新しいフィールドは不要。

### 7.2 並列上限の暫定既定と調整

- 現行 nfgo.md「タスク計画の管理」の「同時 running 数が既定上限 (3) 内」を「**既定上限内 (暫定既定 3。プロファイルの `constraints.max_parallel` で調整できる)**」に改める (R5/R8)。`nepoforge profile show` で確認できる。
- 並列数の公式上限は不在 (0005 §7.2 事実 10) であり、上限は Coordinator 側ポリシー。実測に基づく確定は #49 実装 PR (0006 §9 論点 1) と #12 の観測に委ねる。
- AGENTS.md には数値を書かない (T1e 確認: 現行の記載なし。R7 は分解方針のみ)。

### 7.3 工程バリアの禁止 (nfgo.md 3.0 に 1 行追加)

```text
- 分解は調査→設計→実装の工程単位で一括直列にしない。依存 (depends_on) が揃ったタスクから順次起動し、
  後続工程の準備を並行して進める (工程バリアを作らない)。設計が依存する調査だけを depends_on で結ぶ。
```

nfgo.md への追記は **直書き禁止ルールを遵守**する (モデル名・`fastmetal/`・報告タグ・`subagent_depth` を書かない — `test_cli.TestWorkflowContract.test_no_engine_or_model_details` を壊さない)。実機検証が完了するまで、nfgo の「#52 の Model Resolver は未接続であり、#49 では役割既定のみを使う」の一文は維持し、検証証跡を PR 本文に記録してから差し替える (段階的リリース)。

### 7.4 AGENTS.md 追記案 (R7)

「委譲と並列実行」節に 1 行:

```text
- 分解は必要最小限にとどめつつ、独立した作業は細かく分割して並列度を上げてよい (小規模変更は 1 タスク。工程バリアを作らない)。
```

## 8. 影響範囲・テスト計画・実機確認

### 8.1 影響範囲表

| 種別 | 対象 | 内容 |
| --- | --- | --- |
| 変更する (新規) | `nepoforge/resolver.py` | `resolve`・`TaskInput`・`ModelResolution` (純粋関数・I/O なし) |
| 変更する (新規) | `nepoforge/derive.py` | `agent_id_for` / `slug_of` / `derive_text` / `plan` (命名・内容生成・対象ペア) |
| 変更する | `nepoforge/profiles.py` | `load_profile_config`・`parse_model` / `format_model`・`Candidate` / `ProfileConfig`・`default_candidates` を追加。**`load_profile` / `apply_to_agent` / `render` は無変更** |
| 変更する | `nepoforge/cli.py` | `profile derive` 追加、`profile use` / `init` への事前生成呼び出し、`profile show` への constraints 表示、(任意) `resolve` コマンド・status の derived 表示。4 箇所で同型の期待ハッシュ構築 (`sync_global` / `cmd_status` / `cmd_doctor` / `cmd_profile use`) は**挙動を変えない範囲で**共通ヘルパーへの集約を可とする (任意リファクタ) |
| 変更する | `nepoforge/data/commands/nfgo.md` | §7.3 の 1 行追加 + Resolver 接続文の更新 (実機検証後)。直書き禁止遵守 |
| 変更する | `AGENTS.md` | §7.4 の 1 行追加 |
| 変更する | `docs/design/0005-...md` | §2.2 の R1–R6 (実装 PR の先頭コミットで先行反映) |
| 変更する | `docs/architecture.md` | §3.1 に 1 文 (R9)、§10 現状に 1 行 |
| 変更する | `ROADMAP.md` | Phase 2 前提に 0007 へのリンク 1 行 |
| 変更する (新規) | `tests/test_resolver.py`・`tests/test_derive.py` | §8.2 |
| 変更しない | `nepoforge/data/agents/*.md` (5 役) | permissions・本文・model 行の正本は触れない |
| 変更しない | `nepoforge/data/profiles/*.json` | #52 最小では無変更 (roles のみ維持。optional キーは schema 対応のみ — §3.4 の既定で成立するため)。task_rules の具体例は local プロファイルとテストで検証し、同梱プロファイルへの適用は実機検証後に判断 |
| 変更しない | `nepoforge/data/templates/*` (guardrail / provider) | 権限・provider 設定は不変 |
| 変更しない | `store.USER_MANAGED_FILES`・manifest 形式・`store.classify` の語彙 | 派生定義は管理対象に入れない (§5.3) |
| 変更しない | `.opencode/` (生成物)・`scripts/` (旧互換) | 触れない |
| 変更しない | `experimental.*` 設定 | 不変 (0005 §6) |

### 8.2 テスト計画 (課金なし単体テスト)

実行: `python3 -m unittest discover -s tests -t .`。既存テストは原則無変更で通ることを前提にする。

1. `parse_model` / `format_model`: 正常系 (`provider/model`・`provider/model#variant`)・異常系 (`#` 複数・provider 欠落・空)。`provider/x#y` が OpenCode 解釈 (model=x, variant=y) と一致すること。
2. `load_profile_config`: roles のみのプロファイルが既定候補 (5 モデルの和集合) で成立する (§3.3 規則 6)。roles に local 由来の新モデルがあれば候補に自動追加。`task_rules` の model が候補外 → ロード時エラー。不明トップレベルキーの許容。
3. `resolve`: (a) 明示指定 (信頼できる出所) で selected・理由が `explicit`、(b) `external` 出所は unresolved、(c) 候補外の明示指定は unresolved、(d) task_rules の先頭一致・宣言順の決定性・ineligible ルールのスキップと理由記録、(e) 役割デフォルトへの到達、(f) capabilities/context フィルタ (候補情報 null は除外しない)、(g) executor=tool でモデル不選択、(h) 同一入力の同値性 (決定性)、(i) unresolved で binding が空。
4. derive: `derive_text` の内容が「DATA_DIR 正本 + model 行のみ差し替え」と一致、permissions 部の完全一致、冪等 (2 回生成の同値)、`nf-` 命名と slug 変換 (`#` → `-`)、`profiles.render` が派生定義に影響しないこと、`USER_MANAGED_FILES` が変化しないこと。
5. CLI: `profile derive --dry-run` が書き込まず計画と差分 (`diff_text_vs`) を表示、2 回実行の no-op、conflict (異内容ファイルが既存) で終了コード 2、`profile use` が派生セットを事前生成、`--prune` の orphan 削除とバックアップ。
6. 既存テストの無変更通過: `test_profiles.TestLoadProfile`、`test_cli.TestLifecycle` / `TestCanonicalData` / `TestWorkflowContract`。

### 8.3 実機確認 (承認要否の区別)

**課金なし・実機 (承認不要。証跡は PR 本文に記録):**

1. `nepoforge profile derive` 実行後、`opencode api get /api/agent` で派生定義の登録と model 解釈 (`{providerID, id, variant?}`) を確認。
2. `opencode models` で有効候補の実在確認。

**課金あり・実機 (承認待ち — 明示的な許可を得るまで実施しない):**

3. 派生定義での実起動 → message API で actual_model 照合 (0005 §9.2 (3))。
4. 派生定義の並列起動と時間的重複 (0002 §3 手法)。
5. resolved_model ≠ actual_model の意図的差分 (variant 指定等) がある場合の突合動作。

**承認待ちの運用上の扱い:** 3–5 の証跡が得られるまで、派生定義に依存する経路 (task_rules による役割既定以外の割当) を本番運用に使用しない (0005 制約・残存リスク 7 を引き継ぐ)。nfgo.md の「未接続」一文はその後で差し替える。

### 8.4 残存課題

1. 派生定義の実機妥当性 (課金試験承認待ち — §8.3)。
2. capabilities / context 長の機械可読情報源は未確認 (0005 §7.3)。#52 では人間承認の選定記録 (`approval_ref`) で代替。#14/#12 で整備。
3. budget の実強制なし (fastmetal の cost は常に 0)。#15 で実装。
4. 同時 running 上限 3 は暫定既定 (実測は #49/#12)。
5. `#variant` の fastmetal での実用性は未確認。既定運用は variant なしとする。
6. actual_model と resolved_model の不一致を「意図しない切替」と判定する基準の精密化 (#12 の記録で)。

## 9. 代替案と不採用理由

| 案 | 不採用理由 |
| --- | --- |
| subagent 呼び出し単位のモデル指定 (起動時に渡す) | 公式仕様に存在しない (0005 §7.2 事実 1)。未確認の API を仮定しない (#52 要件) |
| コマンド経由でのモデル指定 | 優先順位は公式定義だがコマンド展開は composer/TUI 層のみで `opencode run` では展開されない (0001 T5)。稼働中の委譲ループから使えない |
| 5 役定義の `model:` 行を委譲直前に差し替えて使い分け | 同一 role の並列タスクで競合し、稼働中設定の競合更新に当たる。非冪等 (0005 で不採用済み) |
| `opencode.json` の `agents.<id>` (JSON 形) で派生定義 | frontmatter 正本 (data/agents) と二重管理になる。利用者所有のグローバル設定ファイルへの書込は 0003 §10 の不採用理由に抵触 |
| 派生定義のプロジェクト配置 (`.opencode/agents/`) | 配布はグローバル一元 (#47/#57)。プロジェクト複製の復活になり、enable 済みリポジトリとの衝突・二重管理を生む |
| 派生定義を manifest (`USER_MANAGED_FILES`) に含める | T1 事実: `store.classify` は expected に無い rel を conflict 扱いにしないが、expected に**入れる**と manifest 未記録の既存導入で初回 conflict 停止になる。再生成可能な生成物に改変検知の利益がない (§5.3) |
| 派生 ID を `nf-` なし (`<role>-<slug>`) | 所有判定が不可能になり uninstall/prune で利用者ファイルを誤削除し得る (§5.2) |
| 承認済み候補 × role の全組合せ (25 件) を常に生成 | 0005 §7.4 の「無制限に生成しない」に反し、不要な定義が TUI/ID 空間を濁す。task_rules 参照ペア + 明示要求ペアに絞る (R4) |
| Resolver 内で決定的ツール判定を機械化 | 判定が意味論的でコストが利益を上回る。nfgo の運用規定 (委譲しない作業) で十分。Resolver は `executor` 入力を受け取る |
| task_rules の model が不適格な場合に即 unresolved | 予算等の環境条件での過剰停止になる。理由記録付きスキップ (§4.2 step 3) を採用 |
| レビュー専用ルーター・観点別ルーターの独立実装 | Review は Task の特殊化で共通 Resolver を使う (0005 §4.5 規則 6、#53 決定) |
| 同時 running 上限をコード側の既定値にする | 運用パラメータは設定側に置く (分離原則)。プロファイル `constraints.max_parallel` (§7.2) |
| モデル名・ルールをコード内に持つ | 分離原則違反 (architecture §3)。モデルはプロファイル・承認済み候補側に置く |
| 派生定義の中間レジストリ (state 配下の生成記録ファイル) | `nf-` 接頭辞 + 内容ハッシュ比較で所有判定と冪等が成立し、状態ファイルの同期ずれリスクを回避できる。単純な方を採用 |

## 10. 状態と人間の判断事項

状態: **承認済み** (2026-11-25 人間承認)。実装と検証は #52。

1. 本設計の承認 (0005 改訂 R1–R9 を含む)。
2. 命名 `nf-<role>-<slug>` の承認 (0005 例示からの変更 — §5.2)。
3. 課金あり実機試験 (§8.3 の 3–5) の実施許可。
4. `constraints.max_parallel` 既定値 3 の暫定承認 (実測後に確定)。
5. `nepoforge resolve` コマンドの要否 (任意扱い)。

## 11. #52 完了条件との対応 (受入の目安)

| #52 要件 (primary 要約) | 本設計の対応 |
| --- | --- |
| (1) #53 契約と #49 基盤の再利用・未確認 API を仮定しない | §2.1・§4・§9 (0005 §7.2 事実の範囲のみ使用) |
| (2) 優先順位・承認済み候補・プロファイル互換・権限非拡大の明文化 | §4.2・§3・§5.1 |
| (3) 全役割で同じ Resolver (レビュー専用ルーターなし) | §4 (role は TaskInput の一要素。特殊ケースなし) |
| (4) 選択モデル・理由・実行モデル・session ID の確認可能性 | §4.1 (`ModelResolution`)・§6 |
| (5) 適格条件 (送信先・保持・コンテキスト・費用上限) | §3.2 (`candidates` / `constraints`)・§4.2 step 1 |
| (6) AGENTS / architecture / ROADMAP / 配布手順の整合 | §2.2 (R7–R9)・§8.1 |
| (7) 細粒度分解方針の先行反映・0005 論理フィールド維持 | §0・§2.2・§7 |



