# 設計メモ 0009: doctor の FastMetal 認証判定 (#68)

- 状態: 確定 (実装は implementer、独立レビューは reviewer。本メモの作成のみが本タスクの成果物)
- 関連: #68、設計メモ 0003 §5 (doctor 仕様: 0003-package-cli.md:125-130)、`nepoforge/cli.py`
- 実測根拠: opencode v2.4.1 実機確認 (T2・primary 受理済み)、コード調査 (T1・受理済み)

## 1. 背景と問題 (事実)

現行の doctor は FastMetal 認証を次のように判定している (`nepoforge/cli.py:453-458`):

```python
has_env_key = bool(_tool_env_key())                                   # cli.py:494-497
auth_store = platformdirs.user_data_dir("opencode") + "/auth.json"    # cli.py:454
ok = has_env_key or Path(auth_store).is_file()
```

問題:

1. **auth.json の存在は FastMetal の登録と相関しない。** OpenAI だけを
   `opencode auth login` で登録した利用者でも auth.json は存在するため ok になる。
   #68 (保存済み FastMetal で FAIL / OpenAI のみで誤って PASS) の原因。
2. **CLI の失敗・不在・タイムアウトを「未登録」と区別できない。**
   存在チェックしかしていないため、そもそも区別の概念がない。

## 2. 実測事実 (T2 / opencode v2.4.1)

- `opencode auth` のサブコマンド: `list / login / logout / export / import / switch`。
- `opencode auth list --format json` は rc=0 で次を出力する (**トークン値は含まない**):

  ```json
  [{"id": "fastmetal", "name": "FastMetal",
    "connections": [{"type": "credential", "id": "cred_...", "label": "FastMetal", "method": "key"}]}]
  ```

- `--format` の選択肢は `default, json`。default は人間向け 2 行テキスト
  (例: `FastMetal  FastMetal  stored`)。
- `opencode auth list` はローカルコマンドであり API 呼び出し・課金なし
  (issue の要件「ローカルの認証一覧確認は課金なしで可」を満たす)。
- **`opencode auth export` は秘密を含む出力のため、本設計では使用を禁止する。**

## 3. 設計

### 3.1 判定の構成: 3 関数に分離 (テスト差し替え可能に)

cli.py の既存ヘルパー群 (`_tool_version`: cli.py:418-426 の which → run → 例外捕捉
パターン) に倣い、実行と解釈と統合を分ける。

```python
_AUTH_LIST_TIMEOUT = 10  # _tool_version と同じ値 (cli.py:423 の timeout=10 に整合)


def _run_opencode_auth_list() -> tuple[str, str]:
    """`opencode auth list --format json` を実行する (読み取り専用・課金なし)。

    戻り値: (状態, 補足)
      ("ok", stdout)         rc=0 (stdout をそのまま返す。ログには出さない)
      ("not-found", "")      opencode が PATH 上に無い (shutil.which が None)
      ("timeout", "")        _AUTH_LIST_TIMEOUT 秒でタイムアウト
      ("error", "理由")      OSError (プロセス起動失敗など)
      ("failed", "stderr先頭行")  rc != 0
    """
    exe = shutil.which("opencode")
    if exe is None:
        return ("not-found", "")
    try:
        result = subprocess.run(
            [exe, "auth", "list", "--format", "json"],
            capture_output=True, text=True, timeout=_AUTH_LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", "")
    except OSError as exc:
        return ("error", _one_line(exc))   # 先頭 1 行・200 文字に切り詰めるヘルパ
    if result.returncode != 0:
        reason = (result.stderr or result.stdout or "").strip().splitlines()
        return ("failed", _one_line(reason[0]) if reason else f"rc={result.returncode}")
    return ("ok", result.stdout or "")


def _fastmetal_in_auth_list(stdout: str) -> bool | None:
    """auth list の stdout に fastmetal の登録があるかを判定する (純粋関数)。

    戻り値: True (登録あり) / False (解釈成功・登録なし) / None (解釈不能)
    """
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None                     # JSON だが配列でない → 解釈不能
    for entry in data:
        if not isinstance(entry, dict):
            return None                 # 要素の型違い → 未知の形式として解釈不能
        ident = str(entry.get("id") or "").lower()
        name = str(entry.get("name") or "").lower()
        if ident == "fastmetal" or name == "fastmetal":
            return True
    return False


def _fastmetal_auth_status() -> tuple[str, str]:
    """FastMetal 認証の状態を (状態, detail) で返す。

    状態: "env" / "stored" / "env+stored" / "missing" / "unknown"
    """
    env_set = bool(_tool_env_key())          # cli.py:494-497 をそのまま再利用
    state, extra = _run_opencode_auth_list()
    if state == "ok":
        found = _fastmetal_in_auth_list(extra)
        cli = "stored" if found else ("missing" if found is False else "unknown")
        cli_reason = ("unrecognized output from 'opencode auth list --format json'"
                      if cli == "unknown" else "")
    elif state == "not-found":
        cli, cli_reason = "unknown", "opencode not found"
    elif state == "timeout":
        cli, cli_reason = "unknown", "'opencode auth list' timed out"
    elif state == "failed":
        cli, cli_reason = "unknown", f"'opencode auth list' failed: {extra}"
    else:
        cli, cli_reason = "unknown", f"'opencode auth list' error: {extra}"

    if cli == "stored":
        if env_set:
            return ("env+stored", "FASTMETAL_API_KEY is set; also stored in opencode auth")
        return ("stored", "fastmetal is stored in opencode auth")
    if cli == "missing":
        if env_set:
            return ("env", "FASTMETAL_API_KEY is set (not stored in opencode auth)")
        return ("missing",
                "neither FASTMETAL_API_KEY nor stored fastmetal credential "
                "(models will fail without credentials)")
    if env_set:
        return ("env", f"FASTMETAL_API_KEY is set (opencode auth unverifiable: {cli_reason})")
    return ("unknown",
            f"cannot verify fastmetal credential ({cli_reason}). "
            "registration not confirmed — check with 'opencode auth list'")
```

`cmd_doctor` 側の差し替え (cli.py:453-458 →):

```python
    state, detail = _fastmetal_auth_status()
    # "missing" (env も保存済みも無い) のみ FAIL。確認不能 (unknown) は
    # degraded として ok=True にし、理由を detail で明示する (設計メモ 0009 §3.2)。
    checks.append(("fastmetal credential hint", state != "missing", detail))
```

- `_tool_env_key` (cli.py:494-497) は削除しない (差分最小。参照は doctor のみで不変)。
- `os.environ` の参照経路を変えないため、テストは os.environ を直接操作する (§4)。

### 3.2 状態と ok / rc への割当

doctor の checks は 2 値 `(name, ok, detail)` で、ok=False が 1 つでもあれば rc=1
(cli.py:483-491)。5 状態を次に割り当てる:

| 状態 | 意味 | ok | detail (要点) |
| --- | --- | --- | --- |
| `env+stored` | env あり + 登録確認 | True | FASTMETAL_API_KEY is set; also stored in opencode auth |
| `stored` | env なし + 登録確認 | True | fastmetal is stored in opencode auth |
| `env` | env あり (CLI は未登録 or 確認不能) | True | FASTMETAL_API_KEY is set (...) |
| `missing` | env なし + CLI 成功で未登録 | **False** | neither FASTMETAL_API_KEY nor stored fastmetal credential (...) |
| `unknown` | env なし + CLI 確認不能 (不在・rc≠0・timeout・解釈不能) | **True (degraded)** | cannot verify fastmetal credential (理由). registration not confirmed — ... |

判断と理由:

- **unknown を ok=False にしない。** rc=1 の意味を「認証情報が無い (要対応)」に保つ。
  CLI の一時的不調で診断全体が NG になる誤検知を避ける。opencode 不在は既存の
  `opencode on PATH` チェックがすでに FAIL にするため、二重報告にならない。
  → 代替案 §6 (4) を不採用にした根拠。
- **unknown を「未登録」と混同しない。** detail に理由と「登録確認ではない」ことを
  明示し、確認手順 (`opencode auth list`) を案内する。missing と unknown は
  状態名で区別され、テストから assert できる (§4)。
- **チェック名は `fastmetal credential hint` を維持する。** `--json` 出力
  (cli.py:490、形状 `{"check", "ok", "detail"}`) を名前で拾う利用者との互換性を
  優先。detail は自由記述なので変更しない。意味的に「登録の確認」になったが、
  「登録確認は API 接続成功の保証ではない」ため hint の語も依然として正しい。
  ok と detail の語彙で degraded を区別する。

### 3.3 subprocess 実行の仕様

- コマンド: `[exe, "auth", "list", "--format", "json"]`。exe は `shutil.which("opencode")`
  の解決値 (cli.py:419 の `_tool_version` と同じ流儀)。
- **追加オプション (`--standalone` 等) を使わない。** T2 で検証済みなのは
  `auth list --format json` のみ。未検証のフラグに依存しない。
- timeout: 10 秒 (`_AUTH_LIST_TIMEOUT`)。`_tool_version` と整合。
- stderr: capture する。rc≠0 時に stderr (空なら stdout) の先頭 1 行・200 文字を
  detail に入れる。**stdout/stderr の全文は detail に出さない** (意図しない内容の
  漏えいとログ肥大を防ぐ)。
- rc≠0 / timeout / OSError / which None はいずれも `unknown` に写像し、
  `("ok", ...)` 以外では stdout を解釈しようとしない。

### 3.4 出力の解釈規則 (`_fastmetal_in_auth_list`)

- `json.loads` 失敗 → None (解釈不能)。
- 配列でない (`{"fastmetal": ...}` 等の object) → None。
- 配列であっても dict 以外の要素を含む → None (未知の形式進化に備えた厳格解釈)。
  「解釈不能」を「未登録」に落とさないのが原則。
- FastMetal 判定: `id` が `"fastmetal"` (大文字小文字を無視) に一致、または
  `name` が `"fastmetal"` に一致。実機出力では id が `fastmetal` (T2)。name は
  id 形式変更時のフォールバック。部分一致は使わない (誤一致防止)。
- **connections の中身 (認証値・method) は読まない・条件に使わない。**
  エントリの存在のみを見る (形状変化に強くする)。ログアウト時にエントリが
  残る/connections が空になるかは未検証であり、その場合は「登録あり」と扱う
  (過検知の側に倒す)。問題が出れば別 issue で対処する。

### 3.5 旧 auth.json 存在チェックの廃止

- cli.py:454-457 の auth.json 存在チェックは**廃止する**。理由:
  (a) 存在 ≠ fastmetal 登録 (#68 の原因そのもの)、
  (b) CLI 確認不能時のフォールバックに使うと「存在=登録」の誤判定が残る、
  (c) `platformdirs.user_data_dir("opencode")/auth.json` という保存場所の前提が
  将来変わった場合の誤判定の再発源になる。
- `import platformdirs` (cli.py:15) は cli.py:454 が唯一の用途なので**削除する**
  (grep 確認済み。store.py:23 の利用は継続するため pyproject の依存は変更しない)。
- 未知の状態は `unknown` として正直に報告する (精度の低い推測より良い)。

## 4. テスト設計 (tests/test_cli.py)

新しい TestCase クラス `TestDoctorFastmetalAuth(IsolatedEnv)` を追加する
(配置は `TestProvider` の後あたり)。リポジトリに mock/patch の前例は無いが、
stdlib の `unittest.mock` を初めて導入してよい (subprocess の実際の起動を
テストで行わないため API 呼び出し・課金ゼロを構造的に保証する)。

モックの当て方:

- **解釈・統合層のテスト**: `mock.patch.object(cli, "_run_opencode_auth_list",
  return_value=(...))` で CLI 実行を差し替える。
- **実行層 (`_run_opencode_auth_list`) のテスト**: `mock.patch("shutil.which")` と
  `mock.patch("nepoforge.cli.subprocess.run")` を使い argv・timeout・rc・例外の
  写像を検証する。
- **env の後片付け**: IsolatedEnv.tearDown はテスト内で新規に set した
  FASTMETAL_API_KEY を復元しない (tests/_base.py:44-45 は元値があった場合のみ
  復元)。テスト内で set する場合は必ず `self.addCleanup(os.environ.pop,
  "FASTMETAL_API_KEY", None)` を使う。値はダミー (`nf-test-dummy-key` 等の
  実在しない文字列) にする。
- **doctor の統合確認**: `cli.main(["doctor", "--json"])` を
  `contextlib.redirect_stdout` で受けて JSON を parse し、
  `check == "fastmetal credential hint"` の要素を検証する (既存テスト
  tests/test_cli.py:104-115 の流儀に合わせる)。rc は他チェック (sandbox に
  provider 設定が無い等) の影響を受けるため、チェック単位で ok を見る。

ケース一覧 (実装可能な粒度):

| # | ケース名 (案) | 準備 | 期待 |
| --- | --- | --- | --- |
| 1 | `test_stored_fastmetal_ok` | helper → `("ok", T2 実測と同形の JSON)`、env なし | 状態 `stored`。doctor JSON の該当 check が ok=True、detail に `stored in opencode auth` |
| 2 | `test_name_fallback_matches` | `("ok", '[{"name": "FastMetal"}]')` | 状態 `stored` |
| 3 | `test_openai_only_is_missing` | `("ok", '[{"id": "openai", "name": "OpenAI", "connections": [...]}]')` | 状態 `missing`、ok=False。detail に `neither` (誤 PASS の回帰 = #68 の本体) |
| 4 | `test_empty_list_is_missing` | `("ok", "[]")` | 状態 `missing`、ok=False |
| 5 | `test_env_only_ok_even_if_cli_missing` | env にダミーキーを set + `("ok", "[]")` | 状態 `env`、ok=True。detail に `FASTMETAL_API_KEY is set` |
| 6 | `test_env_and_stored` | env set + ケース 1 と同じ JSON | 状態 `env+stored`、ok=True |
| 7 | `test_env_with_unverifiable_cli` | env set + `("failed", "boom")` | 状態 `env`、ok=True、detail に `unverifiable` |
| 8 | `test_cli_not_found_is_unknown_not_missing` | `("not-found", "")` | 状態 `unknown`、ok=True (degraded)。detail に `cannot verify` と `opencode not found`。`neither` を含まない |
| 9 | `test_cli_rc_nonzero_is_unknown` | `("failed", "boom: ...")` | 状態 `unknown`、detail に理由、`neither` を含まない |
| 10 | `test_cli_timeout_is_unknown` | `("timeout", "")` | 状態 `unknown`、detail に `timed out` |
| 11 | `test_non_json_output_is_unknown` | `("ok", "FastMetal  FastMetal  stored")` (default 形式) | 状態 `unknown` (未登録にしない) |
| 12 | `test_json_object_is_unknown` | `("ok", '{"fastmetal": {}}')` | 状態 `unknown` |
| 13 | `test_non_dict_element_is_unknown` | `("ok", '["fastmetal"]')` | 状態 `unknown` |
| 14 | `test_runner_argv_and_mapping` | `shutil.which` → `/fake/opencode`、`subprocess.run` → CompletedProcess(rc=0, stdout="[]") | run が `[ "/fake/opencode", "auth", "list", "--format", "json" ]` かつ `timeout=10`・`capture_output=True`・`text=True` で呼ばれる |
| 15 | `test_runner_not_found` | `shutil.which` → None | `("not-found", "")`。`subprocess.run` が呼ばれない (`assert_not_called`) |
| 16 | `test_runner_rc_nonzero` | run → CompletedProcess(rc=1, stderr="no auth") | `("failed", "no auth")` (先頭 1 行・200 文字) |
| 17 | `test_runner_timeout` | run の side_effect=`subprocess.TimeoutExpired("opencode", 10)` | `("timeout", "")` |
| 18 | `test_runner_oserror` | run の side_effect=`OSError("spawn failed")` | `("error", ...)` |
| 19 | `test_no_secret_values_in_output` | ケース既定 | doctor 出力 (`--json` 込み) にダミー env 値・fixture 内の `"cred_DO_NOT_LEAK"` 等のマーカー値が現れないこと。detail に auth list の stdout 全文が出ないことの担保 |

既存テストへの影響:

- `TestLifecycle.test_full_cycle` (tests/test_cli.py:104-115) の doctor 実行は、
  新コードでは実 `opencode auth list` を呼びうる (sandbox でも opencode は PATH 上
  にある)。実ユーザーの認証ストアに依存して出力が非決定的になるため、**推奨**:
  doctor 実行部分を `mock.patch.object(cli, "_run_opencode_auth_list",
  return_value=("ok", "[]"))` で囲み、密閉化する (`missing` → ok=False → rc=1 は
  既存の `assertIn(rc, (0, 1))` と矛盾しない)。省略しても既存 assert は壊れないが、
  非決定性を残さないために patch を推奨する。

API 呼び出しゼロの担保:

- プロダクトコードが実行するのは `opencode --version` 系と
  `opencode auth list --format json` のみで、モデル API を呼ぶ経路は存在しない。
- テストは subprocess をモックするため実行すらしない。実機の認証一覧確認
  (`opencode auth list --format json`) は課金なしのローカル確認であり許容されるが、
  実装・テストで必須ではない。

## 5. 影響範囲とドキュメント更新 (別タスクとして提案)

コード側の影響範囲は次のみ (変更範囲の限定 = issue 要件):

- `nepoforge/cli.py`: `_run_opencode_auth_list` / `_fastmetal_in_auth_list` /
  `_fastmetal_auth_status` の追加 (配置は `_tool_version` 付近が自然) と
  cmd_doctor の cli.py:453-458 差し替え、`import platformdirs` 削除。
- `tests/test_cli.py`: `TestDoctorFastmetalAuth` 追加 + lifecycle の doctor 部分の
  密閉化 (推奨)。
- pyproject・store.py・data/ 配下: 変更なし。

**ドキュメント更新は implementer の write_scope 外のため、別タスクで提案する:**

- `docs/design/0003-package-cli.md:125-130` (doctor 仕様): 「provider 定義と認証の
  手がかり (環境変数・auth ストアの存在のみ。値は表示しない)」→「provider 定義と
  FastMetal 認証の確認 (環境変数または `opencode auth list --format json` による
  登録確認。値は表示しない。CLI で確認不能な場合は理由付きで報告する — 設計メモ
  0009)」への差し替え。
- `README.md:99`: 「認証の手がかり」→「認証 (env または保存済み登録の確認)」程度の
  最小修正。README.md:50-51 は手順案内であり変更不要。

## 6. 代替案と不採用理由

| 案 | 判定 | 理由 |
| --- | --- | --- |
| (1) default 形式テキストのパースのみ | 不採用 | 列位置・空白に依存し壊れやすい。公式に機械可読な `--format json` がある (T2 実測) |
| (2) auth.json 直読み (JSON 内の id 解析込み) | 不採用 | OpenCode 非公開のストレージ形式への結合。保存場所の前提 (platformdirs) も不要にできる。公式 CLI の出力が安定した契約 |
| (3) CLI 失敗時に auth.json 存在へフォールバック | 不採用 | 「存在=登録」の誤判定を残す。§3.5 (b)(c)。unknown を正直に報告する |
| (4) unknown を ok=False (FAIL) にする | 不採用 | CLI の一時的不調で rc=1 が増え、opencode 不在時は `opencode on PATH` と二重報告。§3.2 のとおり degraded ok=True + 明示 detail |
| (5) env があるとき CLI を呼ばない (短絡) | 不採用 | `stored` の報告を失い分岐が増える。常時 1 回のローカル呼び出しは軽く、挙動が一様でテストしやすい |
| (6) `opencode auth export` による確認 | **禁止** | 秘密を含む出力 (T2)。認証値を読まない要件に反する |

## 7. 実装者への注意

1. 秘密値 (API キー・トークン) を読まない・出力しない。`auth export` は使わない。
   detail に CLI 出力の全文を出さない (理由文は 1 行・200 文字まで)。
2. OpenCode の認証ストア (`auth.json` 等) は読まない・書かない。変更は
   `opencode auth list` (読み取り専用) の実行のみ。
3. 「登録確認は API 接続成功の保証ではない」— detail・ドキュメントの文言に
   この意味合い (hint であること) を保つこと。
4. テストで env を set したら `addCleanup` で必ず消す (_base.py の tearDown は
   復元しない)。実在キーを使わない。
5. テスト実行: `.venv/bin/python -m unittest discover -s tests -t .`
