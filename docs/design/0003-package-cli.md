# 設計メモ 0003: パッケージ化・ユーザー単位のグローバル導入・管理 CLI (#47)

状態: 確定 (実装は PR で提出し、人間がレビューする)
関連: #47, #7, #8, #9, #10, #46

## 1. 目的

Nepoforge を配布可能なパッケージとして整備し、PATH 上の `nepoforge` CLI で
ユーザー単位 (ユーザーのホーム配下) に一度導入したら、任意の Git リポジトリで
`/nfgo` 以下の構成を利用できるようにする。プロジェクトごとの共通ファイル複製と
手動更新をなくす。

## 2. 決定事項 (ユーザー承認済み)

1. **パッケージ方式**: Python (標準ライブラリのみ) + `pyproject.toml`。
   `uv tool install git+...` / `pipx install git+...` / `pip install --user`
   で導入し、entry point `nepoforge` を PATH に置く。依存はゼロ。
2. **guardrail (permissions) の配置**: プロジェクト単位の明示的有効化のみ。
   グローバル常時適用はしない (理由は §4.3)。
3. **provider 設定**: `nepoforge init --provider fastmetal` が、既存の
   `~/.config/opencode/opencode.jsonc` に provider 定義が**無い場合のみ**
   バックアップ付きで追記する。秘密情報 (API キー) は書かない。

## 3. OpenCode V2 の仕様確認 (docs.opencode.ai + 実機 opencode v2.4.0)

### 3.1 設定の優先順位 (docs/config)

- グローバル: `~/.config/opencode/opencode.json(c)` — 全プロジェクトに適用。
- プロジェクト: `<repo>/opencode.json(c)` または `<repo>/.opencode/opencode.json(c)`。
  プロジェクトがグローバルを上書きする。競合しない設定は両方有効。
- 実機確認: `~/.config/opencode/opencode.jsonc` が存在し FastMetal provider
  と既定モデルを定義している (ユーザー所有・本設計では原則触らない)。

### 3.2 コマンド・エージェントのグローバル配置 (docs/commands, docs/agents)

- `~/.config/opencode/commands/*.md` — 全プロジェクトで利用可能。
  プロジェクト側 `.opencode/commands/` が同名を上書きする。
- `~/.config/opencode/agents/*.md` — 同様に全プロジェクトで利用可能。
- `/nf*` 名前空間 (#9) により衝突リスクは限定的で、衝突検出は継続する (§6)。

### 3.3 permissions の結合規則 (docs/permissions)

- 「Lower-priority configuration is loaded first, global rules are appended
  next, and agent rules are appended last」— 最後の一致ルールが勝ち、
  いずれかの `deny` があれば全体が拒否される。
- **含意**: guardrail をグローバル設定に置くと、すべてのプロジェクト
  (Nepoforge を使わない通常利用も含む) に deny ルールが無条件適用され、
  プロジェクト側の allow で上書きできない。#47 の「他用途の OpenCode に
  権限を無条件に強制しない」に反するため、グローバル常時適用は不採用。
- permissions は名前空間化できないため、プロジェクト単位の明示的有効化
  (`nepoforge enable`) を採用する。「権限を安全に隔離できない場合は
  起動時の明示的有効化を優先する」という issue の指示に一致。

### 3.4 モデル指定 (docs/commands, docs/agents)

- コマンド・エージェントの frontmatter `model:` は設定ファイルの既定モデル
  より優先される。→ ユーザーのグローバル設定ファイルに触れずに、
  管理下のエージェントファイルへモデル割当 (プロファイル) を適用できる。
- 既存の `scripts/switch_profile.py` の方式 (frontmatter の `model:` 行の
  差し替え) をユーザーレベルの管理ファイルにそのまま転用する。

## 4. レイアウト

### 4.1 配布物の正本

- 正本はパッケージ内 `nepoforge/data/` (wheel に同梱、package-data)。
  - `data/agents/{architect,expert,explore,implementer,reviewer}.md`
  - `data/commands/nfgo.md`
  - `data/profiles/{economy,balanced,performance}.json`
  - `data/templates/guardrail.jsonc` (permissions 雛形)
  - `data/templates/provider-fastmetal.jsonc` (provider 雛形)
- リポジトリ自身の `.opencode/` は開発環境として維持する。
  テストで両者の無意味な乖離 (model 行を除き一致しない) を検出する。
  model 行はプロファイル適用で変わるため比較から除外する。

### 4.2 ユーザー単位の導入先 (グローバル)

| 対象 | パス |
| --- | --- |
| エージェント | `~/.config/opencode/agents/*.md` (5 ファイル) |
| コマンド | `~/.config/opencode/commands/nfgo.md` |
| マニフェスト | `${XDG_STATE_HOME:-~/.local/state}/nepoforge/manifest.json` |
| 有効化済みプロジェクト記録 | 同 `projects.json` |
| バックアップ | 同 `backup/<UTC時刻>/...` |

- `XDG_CONFIG_HOME` / `XDG_STATE_HOME` を尊重する (テストで分離に使用)。
- グローバル設定ファイル (`opencode.jsonc`) は `--provider` 指定時の
  追記を除き一切変更しない。

### 4.3 プロジェクト単位の有効化

`nepoforge enable` はプロジェクト (既定: cwd の Git ルート) に:

- `.opencode/opencode.jsonc` — guardrail (permissions のみ) を書く。
  既存ファイルが Nepoforge 管理下 (管理マーカーコメントあり) なら更新、
  それ以外 (利用者所有・他ツール所有) なら**停止して上書きしない**。
- `.opencode/.nepoforge.json` — プロジェクト用マニフェスト (`kind: "project"`)。
  旧方式 (#9) のマニフェスト (`kind` 無し・files 一式) とは区別し、
  旧形式を検出したら移行手順 (§8) を案内して停止する。
- 中央の `projects.json` にパスとハッシュを記録する。

`nepoforge disable` で管理対象のみ撤去する (利用者改変はバックアップ後に
--force で削除、既定は残す)。

## 5. CLI 仕様

```
nepoforge init     [--profile NAME] [--provider fastmetal] [--dry-run] [--force]
                   ユーザー単位の初期導入。グローバルに agents/commands を
                   配置しプロファイルを適用する。--provider fastmetal で
                   provider 定義が無ければバックアップ付きで追記する。
nepoforge update   [--dry-run] [--force] [--no-projects]
                   管理下ファイルをパッケージ同梱版に同期し、現行プロファイルを
                   再適用する。既定で有効化済みプロジェクトの guardrail も更新する
                   (一度で共通更新が反映される)。
nepoforge status   [--json]
                   読み取り専用。導入バージョン・管理対象ファイルの状態・
                   プロファイル・有効化済みプロジェクト・更新の有無を表示する。
                   未導入は終了コード 1。
nepoforge uninstall [--dry-run] [--force] [--projects]
                   Nepoforge 所有の導入物のみ撤去する。有効化済みプロジェクトが
                   ある場合は一覧を表示して停止 (--projects で同時に撤去)。
                   CLI 自体の削除 (uv tool uninstall 等) は案内文を表示するのみ。
nepoforge doctor   [--json]
                   読み取り専用・課金なし。opencode/git/gh の有無とバージョン、
                   グローバル設定の構文、provider 定義と認証の手がかり
                   (環境変数・auth ストアの存在のみ。値は表示しない)、
                   プロファイル一致、プロジェクト内の nf* コマンド衝突、
                   guardrail 有効化状態を診断する。
nepoforge enable   [--dry-run] [--force]
                   現在のプロジェクトに guardrail を有効化する。
nepoforge disable  [--dry-run] [--force]
                   現在のプロジェクトの guardrail を撤去する。
nepoforge profile use NAME | list | show [NAME] [--dry-run]
                   プロファイルの一元管理。管理下エージェントの model 行を
                   差し替える。`local` は ~/.config/nepoforge/profile.json
                   (利用者所有・個人の上書き) を指す。
```

共通仕様:

- `--help` / `--version` 対応。破壊的 操作は `--dry-run` で計画と差分を表示。
- 終了コード: `0` 成功 / `1` 未導入・診断 NG / `2` 衝突・利用者エラー
  (既存 `nf_workbench.py` と整合)。
- 冪等: 導入済みで差分がなければ何もしない。
- 衝突時停止: 管理対象外の既存ファイルは表示して停止し、上書きしない。
- バックアップ: 上書き・削除前に state 配下へ退避し、パスを表示する。
- エラーはstderrに明瞭なメッセージ、終了コード付き。

## 6. 安全性設計

- **管理対象の識別**: マニフェスト (sha256) + 管理マーカーコメント。
  衝突 (管理外の既存ファイル) は常に停止。
- **改変検知**: マニフェストのハッシュと現在値を比較。改変されていたら
  停止 (`--force` でバックアップ後に続行)。
- **失敗時復旧**: バックアップからの手動復元 + `uninstall` + 再 `init`。
  バックアップは自動削除しない。
- **秘密情報**: CLI は API キー等を書かない・表示しない。doctor は
  存在の有無のみ報告する。
- **他環境への影響**: グローバルに置くのは `/nf*` コマンドと `nf` エージェント
  のみ。既定モデル・permissions・他 provider には触れない。
  OpenCode 標準コマンド/エージェントと同名を持たない。
- **#10 / #46 との関係**: guardrail の内容は #10 で確定したものを
  テンプレート化しただけ。force push 制限 (#46) は緩和しない。
- **既存の旧方式導入との共存**: 旧 `nf_workbench.py` はリポジトリ内に残す
  (開発環境用)。旧マニフェストを持つリポジトリでは enable を停止して
  移行手順を案内する。

## 7. プロファイルの一元管理

- プロファイルはパッケージ同梱 (`data/profiles/`)。役割→モデルの割当のみを
  持ち、役割定義・プロンプト・権限はエージェント md 側 (#6 と同じ分離)。
- `profile use NAME` は管理下エージェント 5 ファイルの frontmatter
  `model:` 行を差し替える。マニフェストには「現行プロファイル」と
  適用後ハッシュを記録し、`update` は同梱版コピー後に現行プロファイルを
  再適用する (プロファイル適用が誤って「改変」と判定されない)。
- プロジェクト固有の上書きは OpenCode の仕様 (プロジェクト設定の
  `agents.<id>.model` がグローバル定義に上書きマージされる) を案内する。

## 8. 旧方式 (#9) からの移行

1. 旧方式で導入したリポジトリで `python3 scripts/nf_workbench.py uninstall`
   (or 配布済みスクリプト) を実行し、リポジトリ内の複製を撤去する。
   利用者改変ファイルは既定で残る。
2. `nepoforge init` (ユーザー単位) → 各リポジトリで `nepoforge enable`。
3. `nepoforge status` は旧マニフェスト (`kind` 無し) を検出したら上記を案内する。
   誤って旧環境を壊さないよう、CLI が旧マニフェストを自動削除することはない。

## 9. テスト計画 (API 課金なし)

- 単体 (stdlib unittest, `python3 -m unittest discover -s tests`):
  - JSONC パーサ (コメント・文字列内 `//`・trailing comma)
  - マニフェスト分類 (missing/unchanged/modified/conflict)
  - プロファイル適用 (model 行差し替え・不明モデル・不正表記)
  - enable/disable/dry-run/衝突停止/旧マニフェスト検出
  - provider 追記 (未定義/既定義済み/providers あり fastmetal なし/壊れた JSONC)
  - data とリポジトリ `.opencode` の同期検証 (model 行を除く)
- 統合 (実機・サンドボックス): `XDG_CONFIG_HOME`/`XDG_STATE_HOME` を
  一時ディレクトリに向け、init → status → profile use → enable (別リポジトリ
  2 件) → update → doctor → disable → uninstall の一連を実行。
  実リポジトリや実ユーザー設定には触れない。
- E2E (#47 完了条件): 実環境への導入と 2 リポジトリでの `/nfgo` は
  PR レビュー後にユーザーが実施する (課金を伴う可能性があるため)。

## 10. 代替案と不採用理由

| 案 | 不採用理由 |
| --- | --- |
| guardrail をグローバル設定に常時適用 | §4.3 のとおり、全プロジェクトへの無条件強制になる |
| guardrail をグローバル配置し起動ラッパーで切替 | config パスの切替手段が公式仕様に無く、未検証の env 依存になる |
| 単一スクリプト配布 (make install) | バージョン管理・更新が手作業。pip/uv の方が保守が簡潔 |
| Node.js / Go での再実装 | 既存 Python 実装の再利用が目的に反し、依存が増える |
| curl \| bash インストーラ | issue が明示的に排除 |
| グローバル設定の全面管理 (model 等を CLI が書く) | 利用者所有の設定を破壊するリスク。frontmatter で代替可能 |
