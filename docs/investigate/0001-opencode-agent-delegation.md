# 調査報告: OpenCode のエージェント構成と委譲仕様

- Issue: [#4](https://github.com/jo7ueb/nepoforge/issues/4)
- 検証日: 2026-11-24
- 検証環境: OpenCode **v2.4.1** (Linux, バックグラウンドサービス共有、`opencode api get /api/info` で version 2.4.1 を確認)
- 性質: 読み取り専用調査。プロジェクトコードへの変更なし。検証は `.sandbox/issue4-test/`(コミット対象外、検証後に削除)で実施し、ユーザーの既存設定は変更していない。

## 1. 出典

| 出典 | URL | 用途 |
| --- | --- | --- |
| Agents ガイド | https://opencode.ai/docs/agents | primary/subagent、モード、マージ、権限 |
| Permissions ガイド | https://opencode.ai/docs/permissions | 権限ルール、既定ポリシー、承認 |
| Config ガイド | https://opencode.ai/docs/config | 設定場所、マージ順、変数 |
| Models ガイド | https://opencode.ai/docs/models | モデル選択・優先順位・バリアント |
| Commands ガイド | https://opencode.ai/docs/commands | カスタムコマンド、モデル選択規則 |

いずれも 2026-11-24 に取得。OpenCode V2 の文書であり、V1 の記法(`task` 権限名、`permission` 単数形等)は非推奨として扱う。

## 2. 公式資料から確認した仕様(事実)

### 2.1 エージェント構成 (primary / subagent)

- エージェントは Markdown(`.opencode/agents/<name>.md`、フロントマター + 本文が system プロンプト)または JSONC(`agents` キー)で定義する。
- `mode`: `primary`(セッションの主エージェント)/ `subagent`(子セッション専用)/ `all`。新規カスタムエージェントの既定は `primary`。
- 組み込みエージェント: `build`(primary)/ `plan`(primary)/ `general`(subagent)/ `explore`(subagent)。非表示の `title` `summary` `compaction` は保守用。V2 に組み込み `scout` はない。
- subagent は**常にバックグラウンドの子セッション**で実行され、専用コンテキストを持つ。親は子が idle になった時点と、その後の作業往復ごとに報告を受け取る。
- `subagent` 権限(親側)で起動可能なエージェントを制御する。子は親の権限のサブセットではなく、**自分に設定された権限**で動く。
- `experimental.subagent_depth`(既定 1)でネストの深さを制御。既定では subagent はさらに subagent を起動できない。組み込み `general` も subagent 起動を拒否される。
- `steps`(正の整数)でエージェントの最大ステップ数を制限できる。最終ステップでツールを外して要約を促す。新規ユーザー入力でリセット。

### 2.2 権限 (permissions)

- ルールは `{ action, resource, effect }` の順序付きリスト。**後勝ち**(last matching rule wins)。effect は `allow` / `ask` / `deny`。未一致は `ask`。
- V2 のアクション名: `shell` / `edit` / `subagent` / `read` / `glob` / `grep` / `webfetch` / `websearch` / `skill` / `external_directory` / `execute`(Code Mode)等。V1 の `bash`・`task` は非推奨。
- 全エージェント共通の既定ポリシー: `allow all` の後に `read *.env` / `*.env.*` が `ask`(例外で `*.env.example` は allow)。
- 組み込みポリシー例: `plan` は通常ファイルへの編集を拒否、`general` は質問と subagent 起動を拒否、`explore` は読み取り系以外をすべて拒否。
- マージ: 低優先設定 → グローバルルール → エージェント個別ルールの順に**追記**され、後勝ちで評価される。
- `policies` は設定・承認パターンよりさらに上位で強制 deny を行う(グローバル/Console 管理ポリシーはプロジェクト設定を上書き)。
- `ask` への応答は once / always / reject。always はプロジェクト単位で保存されるが、設定済み `deny` は決して上書きしない。

### 2.3 設定マージの優先順位

低い方から:

1. `~/.config/opencode/opencode.json(c)`(グローバル)
2. 起点ディレクトリからルートまで祖先探索した**直接** `opencode.json(c)`(遠い → 近い)
3. 同じく探索した **`.opencode/` 内**の設定(すべての直接設定より強い)
4. `/etc/opencode`(Linux のシステム全体設定)— 上記すべてを上書き
5. macOS の管理設定 (`ai.opencode.managed`) — 最高位

競合しない設定は保持される。マシン全体設定や管理設定がない通常の個人利用では、実効的な強さの順は `.opencode/` > 直接設定(近い側が強い)> グローバル。

### 2.4 モデル選択の優先順位

- 主セッション: CLI `--model` > セッションで選択済みのモデル > 設定ファイルのルート `model` > 利用可能な最新モデルへフォールバック。
- ルート `model` は `#variant` を保持しない(variant は agent / command / 実行時選択で指定)。
- subagent: **自分に設定された `model`** > 未設定なら親セッションのモデルを継承。
- コマンド: コマンドの `model` > 選択されたエージェントの `model` > 呼び出し時点のセッションモデル。
- セッションで選択したモデルは設定ファイルを書き換えない。設定は自動リロードされるが、実行中のリクエストは開始時の設定を保持する。

### 2.5 カスタムコマンド

- `.opencode/commands/<name>.md`(本文がテンプレート)または `commands` キー(JSON)で定義。`$ARGUMENTS` / `$1` 位置引数 / `!` + バッククォートのシェル展開に対応。
- フロントマターで `agent` / `model` / `subagent`(`true` で子セッション実行)を指定可能。
- シェル展開はエージェントの権限フローの**外側**で実行されるため、信頼できるコマンドのみに使うこと(ドキュメント明記の警告)。
- コマンドへのエイリアス上書き(後勝ち)は自動リロードされる。

## 3. 実機検証 (v2.4.1)

サンドボックス設定: `model: fastmetal/glm-5.3-flash` + subagent `verify-agent`(`model: fastmetal/japan-gemma-4-31b`、`edit`/`shell` を `deny`)。

| # | 検証 | 手法 | 結果 | 証拠 |
| --- | --- | --- | --- | --- |
| T1 | プロジェクト設定がグローバルを上書き | グローバル既定(`japan-deepseek-v4.1-flash-1017`)とは別のモデルをプロジェクト設定で指定し `opencode run` | **プロジェクト設定が勝った** | 実行ヘッダー `build · glm-5.3-flash`。メッセージ API で `agent=build, model=fastmetal/glm-5.3-flash` を機械可読に確認 |
| T2 | 親子で異なるモデル | 親から `verify-agent` を起動 | **親子で別モデル** | 親セッション `ses_df2bb3b3fffeIm9TLCercGCJQw`: `agent=build, model=fastmetal/glm-5.3-flash`。子 `ses_df2bb3370ffec6HxgO07tpjSsB` (parentID=親): `agent=verify-agent, model=fastmetal/japan-gemma-4-31b`。子の応答 `VERIFY-OK` |
| T3 | subagent 内での deny | `verify-agent` に write を指示 | **拒否**(ファイル未作成) | deny により `write` ツールがカタログから**削除**され、子は `Unknown tool 'write'` を受け取る挙動。実行時に権限エラーが返るのではなく、ツール自体が見えなくなる |
| T3b | 親の `subagent` 権限 | トップレベルで `subagent: *` を deny して再実行 | **委譲が拒否** | `Permission denied: subagent` で起動失敗。サイドエフェクトなし |
| T4 | CLI `--model` の優先 | `--model fastmetal/glm-5.3-flashx` で実行 | **フラグが設定に勝った** | ヘッダー `build · glm-5.3-flashx` |
| T5 | カスタムコマンド | `opencode run "/verifycmd hello"` | **展開されない**(制約、§4) | ユーザーメッセージはリテラル `"/verifycmd hello"` のまま送信され、モデルが自分で `.opencode/commands/verifycmd.md` を読んで指示に従った |
| T6 | `.opencode` が直接設定を上書き | `.opencode/opencode.json`(`japan-gemma-4-31b`)を追加 | **上書きされた** | ヘッダー `build · japan-gemma-4-31b`(直接設定 `glm-5.3-flash` より強い) |

## 4. 制約・暫定回答・留意点

**事実として確認できた制約**

1. **`opencode run` ではカスタムコマンドが展開されない**(T5)。コマンド展開は TUI/composer 層の機能。無人実行 (`run`) で `/issue` 相当を実現するには、展開済みプロンプトを直接渡すか、コマンドレジストリに頼らない設計が必要。
2. **subagent は常にバックグラウンド実行**。親は子の完了を「報告」として受け取る(同期完了を待つ単純な手続きは書けない)。
3. **subagent_depth 既定 1**。子からさらに委譲できない(#5 の委譲設計で明示的に必要か判断する)。
4. **deny はツールを不可視化する**(T3)。権限違反のログとして理由が残りにくい。レビュー役エージェントの権限制御は「ツールが存在しない」挙動を前提に設計する。
5. ルート設定の `model` は variant を保持しない。variant が必要な役割では agent / command 側で指定する。
6. per-agent の `request` オーバーレイ(headers/body)は **V2 セッションランナーがまだモデルリクエストに送信しない**(公式の warning)。依存しないこと。
7. メッセージ API は 1 メッセージごとに `tokens`(入出力・reasoning・cache)と `cost` を返す(#12 の記録設計に使える)。検証環境では fastmetal の `cost` が常に `0` であったため、**費用推定は OpenCode の cost フィールドに頼れず、自前の価格テーブル(#12・#14)が必要**。

**未確認(残存課題)**

- `ask` effect の非対話 (`opencode run`) 時の挙動(ハングするか自動 reject か)は未検証。無人実行設計 (#20) の前に要確認。
- TUI でのカスタムコマンド展開・`subagent: true` コマンドの実挙動は未検証(対話環境が必要)。
- 設定の自動リロードは動作確認済みだが、実行中リクエストへの適用タイミングは長時間実行でのみ確認可能。

**暫定回答(Phase 1 設計への示唆、architecture.md §4・§7 の確定材料)**

- 設定優先順位 (§4): 「実行時指定 > セッション選択 > リポジトリ(.opencode)> グローバル個人設定」で概ね確定。個人設定を保持しつつリポジトリ設定で上書きする Issue #6 の要件は、**グローバル個人設定にユーザー上書きを置き、リポジトリの `.opencode` を配布物とする**構成で実現できる。
- OpenCode 依存部分 (§7): 役割定義は `.opencode/agents/*.md` がそのまま使え、モデル割当はエージェント定義の `model` フィールドで完結する。**独自オーケストレーターは不要**(§8 の導入条件を満たす必要性は現時点で観測されていない)。
- `/issue` (#8): `.opencode/commands/issue.md` + `subagent: true` + `agent` 指定で実装可能に見えるが、無人実行では T5 の制約があるため、`run` 用のプロンプト組み立ての包装が要る。詳細は #8 で設計。
- 権限 (#10): 読み取り専用レビュー役は `edit`/`shell`/`subagent` を deny するエージェント定義で実現可能(T3・T3b で動作確認済み)。`policies` による強制 deny も併用できる。

## 5. 結論

OpenCode v2.4.1 の標準機能のみで、「役割(primary/subagent)・モデル割当・権限・コマンド」の分離はすべて実現可能。独自オーケストレーターの必要性は観測されなかった。ただし無人実行 (`opencode run`) とコマンド展開の関係、`ask` の非対話挙動には制約があり、#8・#20 の設計時に考慮する。
