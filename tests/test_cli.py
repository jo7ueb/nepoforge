"""CLI ライフサイクルの統合テスト (#47)。

init → status → profile → enable (2 リポジトリ) → update → doctor →
disable → uninstall を、一時 XDG 環境で課金なしに検証する。
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from nepoforge import cli, jsonc, store
from nepoforge.store import NepoforgeError

from ._base import IsolatedEnv

MODEL_RE = re.compile(r"^model:\s*(.+)$", re.MULTILINE)


def models_of(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for agent in store.USER_AGENT_IDS:
        text = (path / "agents" / f"{agent}.md").read_text(encoding="utf-8")
        out[agent] = MODEL_RE.search(text).group(1).strip()  # type: ignore[union-attr]
    return out


class TestLifecycle(IsolatedEnv):
    def test_full_cycle(self) -> None:
        # init (既定プロファイル: balanced)
        self.assertEqual(cli.main(["init"]), 0)
        agents_dir = store.opencode_dir() / "agents"
        for agent in store.USER_AGENT_IDS:
            self.assertTrue((agents_dir / f"{agent}.md").is_file())
        self.assertTrue((store.opencode_dir() / "commands" / "nfgo.md").is_file())
        manifest = store.load_manifest(store.manifest_path())
        self.assertEqual(manifest["kind"], "user")
        self.assertEqual(manifest["profile"], "balanced")
        self.assertEqual(manifest["version"], store.VERSION)

        # init は冪等
        self.assertEqual(cli.main(["init"]), 0)

        # status (導入済み)
        self.assertEqual(cli.main(["status"]), 0)

        # profile use で model 行が差し替わる
        before = models_of(store.opencode_dir())
        self.assertEqual(cli.main(["profile", "use", "economy"]), 0)
        after = models_of(store.opencode_dir())
        self.assertNotEqual(before, after)
        self.assertEqual(cli.main(["profile", "use", "economy"]), 0)  # 冪等
        manifest = store.load_manifest(store.manifest_path())
        self.assertEqual(manifest["profile"], "economy")

        # profile show / list
        self.assertEqual(cli.main(["profile", "show"]), 0)
        self.assertEqual(cli.main(["profile", "list"]), 0)

        # 2 つの別リポジトリで enable (共通ファイルの複製なし)
        repo1 = self.make_git_repo("repo1")
        self.cd(repo1)
        self.assertEqual(cli.main(["enable"]), 0)
        self.assertTrue((repo1 / ".opencode" / "opencode.jsonc").is_file())
        pm = store.load_manifest(repo1 / ".opencode" / ".nepoforge.json")
        self.assertEqual(pm["kind"], "project")
        self.assertEqual([p["path"] for p in store.load_projects()], [str(repo1)])

        repo2 = self.make_git_repo("repo2")
        self.cd(repo2)
        self.assertEqual(cli.main(["enable"]), 0)
        self.assertEqual(len(store.load_projects()), 2)
        # enable はリポジトリにエージェント等を複製しない
        self.assertFalse((repo1 / ".opencode" / "agents").exists())

        # update: 共通更新が一度で両方に反映される (テンプレートは実ツリーを
        # 書き換えず、一時コピーに差し替えて「新しいパッケージ」を再現する)
        import tempfile

        original_template = cli.GUARDRAIL_TEMPLATE
        original_text = original_template.read_text(encoding="utf-8")
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".jsonc", delete=False) as tf:
                tf.write(original_text.replace("#47", "#47 revised"))
                revised = Path(tf.name)
            cli.GUARDRAIL_TEMPLATE = revised
            self.cd(repo1)
            self.assertEqual(cli.main(["update"]), 0)
            text1 = (repo1 / ".opencode" / "opencode.jsonc").read_text(encoding="utf-8")
            self.assertIn("#47 revised", text1)
            self.cd(repo2)
            self.assertEqual(cli.main(["update"]), 0)
            self.assertIn("#47 revised", (repo2 / ".opencode" / "opencode.jsonc").read_text(encoding="utf-8"))
        finally:
            cli.GUARDRAIL_TEMPLATE = original_template
            revised.unlink(missing_ok=True)

        # doctor (サンドボックスには provider・認証が無いため全体の終了コードは
        # 環境依存になる。個別のチェック行を検証する)
        self.cd(repo1)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = cli.main(["doctor"])
        out = stdout.getvalue()
        self.assertIn("[ok  ] opencode on PATH", out)
        self.assertIn("[ok  ] nepoforge installed", out)
        self.assertIn("[ok  ] managed files intact", out)
        self.assertIn("[ok  ] guardrail enabled (this project)", out)
        self.assertIn(rc, (0, 1))

        # disable → 管理対象のみ撤去
        self.assertEqual(cli.main(["disable"]), 0)
        self.assertFalse((repo1 / ".opencode" / "opencode.jsonc").exists())
        self.assertFalse((repo1 / ".opencode" / ".nepoforge.json").exists())
        self.assertEqual(len(store.load_projects()), 1)

        # uninstall (--projects で残り 1 件も撤去)
        self.cd(self.home)
        self.assertEqual(cli.main(["uninstall", "--projects"]), 0)
        self.assertFalse(store.manifest_path().is_file())
        self.assertFalse((store.opencode_dir() / "agents" / "architect.md").is_file())
        self.assertEqual(store.load_projects(), [])

    def test_init_dry_run_makes_no_changes(self) -> None:
        self.assertEqual(cli.main(["init", "--dry-run"]), 0)
        self.assertFalse((store.opencode_dir() / "agents").exists())
        self.assertFalse(store.manifest_path().is_file())

    def test_status_not_installed(self) -> None:
        self.assertEqual(cli.main(["status"]), 1)
        self.assertEqual(cli.main(["status", "--json"]), 1)

    def test_update_requires_init(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cli.main(["update"])
        self.assertEqual(cm.exception.code, 1)

    def test_conflict_existing_user_agent_file(self) -> None:
        target = store.opencode_dir() / "agents"
        target.mkdir(parents=True)
        (target / "architect.md").write_text("user owns this\n")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["init"])
        self.assertEqual(cm.exception.code, 2)
        # 利用者のファイルは無傷
        self.assertEqual((target / "architect.md").read_text(), "user owns this\n")

    def test_modified_managed_file_stops_without_force(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        target = store.opencode_dir() / "agents" / "architect.md"
        target.write_text("user tweak\n")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["update"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(target.read_text(), "user tweak\n")
        # --force でバックアップ後に上書き
        self.assertEqual(cli.main(["update", "--force"]), 0)
        self.assertIn("description:", target.read_text(encoding="utf-8"))
        backups = list(store.backup_root().rglob("architect.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "user tweak\n")

    def test_package_update_is_not_user_modification(self) -> None:
        """パッケージ更新 (updatable) は --force 不要で正規に update できる (#47 レビュー指摘 2)。"""
        self.assertEqual(cli.main(["init"]), 0)
        agent = store.opencode_dir() / "agents" / "architect.md"
        # パッケージ同梱ファイルだけを差し替えて「新しいパッケージ」を再現する
        # (render は model 行しか触らないため、本文を変えて差分を作る)
        before = agent.read_text(encoding="utf-8")
        data_agent = store.DATA_DIR / "agents" / "architect.md"
        data_backup = data_agent.read_text(encoding="utf-8")
        try:
            data_agent.write_text(data_backup.replace("description: 調査結果から設計をまとめる。", "description: (v2) 調査結果から設計をまとめる。"), encoding="utf-8")
            status = io.StringIO()
            with contextlib.redirect_stdout(status):
                cli.main(["status"])
            self.assertIn("update available", status.getvalue())
            # --force 無しで update が成功し、新しいパッケージ内容に同期される
            self.assertEqual(cli.main(["update"]), 0)
            self.assertIn("(v2)", agent.read_text(encoding="utf-8"))
            status2 = io.StringIO()
            with contextlib.redirect_stdout(status2):
                cli.main(["status"])
            self.assertNotIn("update available", status2.getvalue())
        finally:
            data_agent.write_text(data_backup, encoding="utf-8")

    def test_uninstall_refuses_while_projects_enabled(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        repo = self.make_git_repo()
        self.cd(repo)
        self.assertEqual(cli.main(["enable"]), 0)
        self.cd(self.home)
        with self.assertRaises(SystemExit) as cm:
            cli.main(["uninstall"])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue(store.manifest_path().is_file())


class TestEnableSafety(IsolatedEnv):
    def test_enable_refuses_existing_non_managed_config(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        repo = self.make_git_repo()
        (repo / ".opencode").mkdir()
        (repo / ".opencode" / "opencode.jsonc").write_text('{"model": "x"}\n')
        self.cd(repo)
        with self.assertRaises(SystemExit) as cm:
            cli.main(["enable"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual((repo / ".opencode" / "opencode.jsonc").read_text(), '{"model": "x"}\n')

    def test_enable_stops_on_legacy_manifest(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        repo = self.make_git_repo()
        (repo / ".opencode").mkdir()
        (repo / ".opencode" / ".nepoforge.json").write_text(
            json.dumps({"tool": "nepoforge", "version": "0.1.0", "files": {}})
        )
        self.cd(repo)
        with self.assertRaises(SystemExit) as cm:
            cli.main(["enable"])
        self.assertEqual(cm.exception.code, 2)

    def test_enable_dry_run(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        repo = self.make_git_repo()
        self.cd(repo)
        self.assertEqual(cli.main(["enable", "--dry-run"]), 0)
        self.assertFalse((repo / ".opencode").exists())

    def test_enabled_guardrail_updated_by_force(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        repo = self.make_git_repo()
        self.cd(repo)
        self.assertEqual(cli.main(["enable"]), 0)
        config = repo / ".opencode" / "opencode.jsonc"
        config.write_text("user tweak\n")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["update"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(cli.main(["update", "--force"]), 0)
        self.assertIn("nepoforge (managed)", config.read_text(encoding="utf-8"))


class TestProvider(IsolatedEnv):
    def test_fresh_config_created(self) -> None:
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        config = store.opencode_dir() / "opencode.jsonc"
        self.assertTrue(config.is_file())
        data = json.loads(
            # コメントを除いて素の JSON としても読めることを確認
            "\n".join(l for l in config.read_text().splitlines() if "//" not in l)
        )
        self.assertIn("fastmetal", data["providers"])
        # 秘密情報を書かない
        self.assertNotIn("apiKey", config.read_text())

    def test_insertion_preserves_existing_settings(self) -> None:
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        config = store.opencode_dir() / "opencode.jsonc"
        config.write_text(
            '{\n  "$schema": "https://opencode.ai/config.json",\n'
            '  "model": "other/model" // user comment\n'
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        text = config.read_text(encoding="utf-8")
        self.assertIn('"model": "other/model"', text)
        self.assertIn("user comment", text)
        self.assertIn("fastmetal", text)
        # 書き込み結果が有効な JSONC として再パースできる (レビュー指摘 1)
        from nepoforge import jsonc

        self.assertIn("fastmetal", jsonc.loads(text)["providers"])

    def test_insertion_with_trailing_comma_member(self) -> None:
        config = store.opencode_dir() / "opencode.jsonc"
        config.parent.mkdir(parents=True)
        config.write_text(
            '{\n  "$schema": "https://opencode.ai/config.json",\n'
            '  "model": "other/model",\n}\n',
            encoding="utf-8",
        )
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        from nepoforge import jsonc

        self.assertIn("fastmetal", jsonc.loads(config.read_text(encoding="utf-8"))["providers"])

    def test_unverifiable_insertion_writes_nothing(self) -> None:
        """挿入結果が検証できない入力では、書き込まずに停止する (レビュー指摘 1)。"""
        config = store.opencode_dir() / "opencode.jsonc"
        config.parent.mkdir(parents=True)
        # 文字列リテラル内の // や、解析を壊す入力
        broken = '{\n  "a": "x",\n  "b": [1, 2\n}\n'  # 配列が未閉鎖
        config.write_text(broken, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["init", "--provider", "fastmetal"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(config.read_text(), broken)

    def test_strict_json_file_is_refused(self) -> None:
        """.json はコメント非対応のため書き換えず手動手順を案内する (レビュー指摘 1)。"""
        config = store.opencode_dir() / "opencode.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"model": "other/model"}\n', encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["init", "--provider", "fastmetal"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(config.read_text(), '{"model": "other/model"}\n')

    def test_existing_fastmetal_is_noop(self) -> None:
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        config = store.opencode_dir() / "opencode.jsonc"
        before = config.read_text()
        self.assertEqual(cli.main(["init", "--provider", "fastmetal"]), 0)
        self.assertEqual(config.read_text(), before)

    def test_providers_without_fastmetal_stops(self) -> None:
        config = store.opencode_dir() / "opencode.jsonc"
        config.parent.mkdir(parents=True)
        config.write_text(
            '{\n  "$schema": "https://opencode.ai/config.json",\n'
            '  "providers": {"other": {"name": "Other"}}\n}\n',
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit) as cm:
            cli.main(["init", "--provider", "fastmetal"])
        self.assertEqual(cm.exception.code, 2)
        # 利用者の設定は無傷
        self.assertNotIn("fastmetal", config.read_text())

    def test_broken_config_stops(self) -> None:
        config = store.opencode_dir() / "opencode.jsonc"
        config.parent.mkdir(parents=True)
        config.write_text("{broken", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["init", "--provider", "fastmetal"])
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(config.read_text(), "{broken")


class TestCanonicalData(IsolatedEnv):
    """正本のみを利用し、自身の clone と同じ条件で導入・有効化する (#57)。"""

    REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_clean_source_checkout_enable_and_ignore(self) -> None:
        # source の生成物の有無ではなく、Git の追跡対象を検証する。
        tracked = subprocess.check_output(
            ["git", "ls-files", "--", ".opencode", "profiles"],
            cwd=self.REPO_ROOT, text=True,
        )
        self.assertEqual(tracked, "")
        repo = self.make_git_repo("nepoforge")
        (repo / ".gitignore").write_text(
            (self.REPO_ROOT / ".gitignore").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
        self.cd(repo)
        self.assertEqual(cli.main(["init"]), 0)
        self.assertEqual(cli.main(["enable", "--dry-run"]), 0)
        self.assertFalse((repo / ".opencode").exists())
        self.assertEqual(cli.main(["enable"]), 0)
        self.assertEqual(cli.main(["enable"]), 0)
        config = repo / ".opencode" / "opencode.jsonc"
        manifest = repo / ".opencode" / ".nepoforge.json"
        self.assertTrue(manifest.is_file())
        self.assertEqual(config.read_text(encoding="utf-8"),
                         cli.GUARDRAIL_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(cli.main(["update"]), 0)
        self.assertEqual(subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo, text=True,
        ), "")
        self.assertFalse((repo / ".opencode" / "agents").exists())
        self.assertFalse((repo / ".opencode" / "commands").exists())
        self.assertEqual(cli.main(["disable"]), 0)
        self.assertFalse(config.exists())
        self.assertFalse(manifest.exists())

    def test_install_renders_canonical_data(self) -> None:
        from nepoforge import profiles
        self.assertEqual(cli.main(["init"]), 0)
        profile = profiles.load_profile("balanced")
        for agent in store.USER_AGENT_IDS:
            rel = f"agents/{agent}.md"
            self.assertEqual(
                (store.opencode_dir() / rel).read_text(encoding="utf-8"),
                profiles.render(rel, profile),
            )
        rel = "commands/nfgo.md"
        self.assertEqual(
            (store.opencode_dir() / rel).read_text(encoding="utf-8"),
            (store.DATA_DIR / rel).read_text(encoding="utf-8"),
        )

    def test_guardrail_retains_critical_denies(self) -> None:
        rules = jsonc.loads(cli.GUARDRAIL_TEMPLATE.read_text(encoding="utf-8"))["permissions"]
        for resource in ("git push --force*", "gh pr merge*", "gh repo delete*"):
            self.assertIn({"action": "shell", "resource": resource, "effect": "deny"}, rules)

    def test_legacy_install_and_safe_uninstall(self) -> None:
        repo = self.make_git_repo("legacy")
        script = self.REPO_ROOT / "scripts" / "nf_workbench.py"
        def run(command: str, *args: str) -> None:
            subprocess.run(
                [sys.executable, str(script), command, "--to", str(repo), *args],
                check=True, capture_output=True, text=True,
            )
        run("install")
        config = repo / ".opencode" / "opencode.jsonc"
        self.assertEqual(
            config.read_text(encoding="utf-8"),
            cli.GUARDRAIL_TEMPLATE.read_text(encoding="utf-8"),
        )
        run("status")
        run("update")
        run("uninstall", "--dry-run")
        self.assertTrue(config.is_file())
        run("uninstall")
        self.assertFalse(config.exists())
        self.assertFalse((repo / ".opencode" / ".nepoforge.json").exists())

    def test_legacy_uninstall_refuses_current_manifest(self) -> None:
        repo = self.make_git_repo("modern")
        self.cd(repo)
        self.assertEqual(cli.main(["enable"]), 0)
        config = repo / ".opencode" / "opencode.jsonc"
        before = config.read_bytes()
        result = subprocess.run(
            [sys.executable, str(self.REPO_ROOT / "scripts" / "nf_workbench.py"),
             "uninstall", "--to", str(repo)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(config.read_bytes(), before)

    def test_legacy_update_refuses_unmanaged_file(self) -> None:
        repo = self.make_git_repo("legacy-conflict")
        script = self.REPO_ROOT / "scripts" / "nf_workbench.py"
        subprocess.run([sys.executable, str(script), "install", "--to", str(repo)],
                       check=True, capture_output=True, text=True)
        manifest = repo / ".opencode" / ".nepoforge.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        del data["files"][".opencode/opencode.jsonc"]
        manifest.write_text(json.dumps(data), encoding="utf-8")
        config = repo / ".opencode" / "opencode.jsonc"
        config.write_text('{"model": "user/model"}', encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script), "update", "--to", str(repo), "--force"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(config.read_text(encoding="utf-8"), '{"model": "user/model"}')


if __name__ == "__main__":
    unittest.main()
