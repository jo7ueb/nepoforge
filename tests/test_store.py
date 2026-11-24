"""store (パス解決・分類・バックアップ) のテスト (#47)。"""

from __future__ import annotations

import unittest
from pathlib import Path

from nepoforge import store

from ._base import IsolatedEnv


class TestPaths(IsolatedEnv):
    def test_opencode_dir_respects_xdg(self) -> None:
        self.assertEqual(store.opencode_dir(), self.xdg_config / "opencode")

    def test_state_dir_respects_xdg(self) -> None:
        self.assertEqual(store.state_dir(), self.xdg_state / "nepoforge")


class TestClassify(IsolatedEnv):
    def test_states(self) -> None:
        target = self.xdg_config / "opencode"
        (target / "agents").mkdir(parents=True)
        (target / "agents/a.md").write_text("hello\n")
        expected = {"agents/a.md": store.sha256_of(target / "agents/a.md"), "agents/b.md": "x"}
        manifest = {"files": dict(expected)}

        state = store.classify(target, manifest, expected)
        self.assertEqual(state["agents/a.md"], "unchanged")
        self.assertEqual(state["agents/b.md"], "missing")

        # パッケージ更新由来の正規の差分 = updatable (--force 不要)
        expected2 = dict(expected, **{"agents/a.md": "newhash"})
        state = store.classify(target, manifest, expected2)
        self.assertEqual(state["agents/a.md"], "updatable")

        # マニフェスト記録と一致しない = 利用者改変 (modified)
        (target / "agents/a.md").write_text("changed\n")
        state = store.classify(target, manifest, expected)
        self.assertEqual(state["agents/a.md"], "modified")

        # マニフェストに記録がない = conflict
        manifest2 = {"files": {"agents/other.md": "x"}}
        state = store.classify(target, manifest2, expected)
        self.assertEqual(state["agents/a.md"], "conflict")

        # マニフェスト無しも conflict
        state = store.classify(target, None, expected)
        self.assertEqual(state["agents/a.md"], "conflict")


class TestBackup(IsolatedEnv):
    def test_backup_file_copies_under_state(self) -> None:
        target = self.xdg_config / "opencode"
        (target / "agents").mkdir(parents=True)
        (target / "agents/a.md").write_text("v1\n")
        backup = store.backup_file(target, "agents/a.md")
        self.assertIsNotNone(backup)
        self.assertTrue(str(backup).startswith(str(store.backup_root())))
        self.assertEqual(backup.read_text(), "v1\n")

    def test_backup_missing_is_none(self) -> None:
        self.assertIsNone(store.backup_file(self.xdg_config / "opencode", "agents/nope.md"))


class TestFindProjectRoot(IsolatedEnv):
    def test_finds_git_root(self) -> None:
        repo = self.make_git_repo()
        (repo / "sub").mkdir()
        self.cd(repo / "sub")
        self.assertEqual(store.find_project_root(), repo)

    def test_outside_git_returns_cwd(self) -> None:
        plain = self.home / "plain"
        plain.mkdir()
        self.cd(plain)
        self.assertEqual(store.find_project_root(), plain)


if __name__ == "__main__":
    unittest.main()
