"""テスト共通の分離ヘルパ (#47)。

XDG_CONFIG_HOME / XDG_STATE_HOME を一時ディレクトリに向けて、
実ユーザーの ~/.config/opencode を一切触れずに CLI を試験する。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class IsolatedEnv(unittest.TestCase):
    """各テストに独立した XDG 環境と cwd を与える。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="nepoforge-test-")
        base = Path(self._tmp.name)
        self.xdg_config = base / "config"
        self.xdg_state = base / "state"
        self.home = base / "home"
        for d in (self.xdg_config, self.xdg_state, self.home):
            d.mkdir()
        self._orig_env = {
            k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "HOME")
        }
        os.environ["XDG_CONFIG_HOME"] = str(self.xdg_config)
        os.environ["XDG_STATE_HOME"] = str(self.xdg_state)
        os.environ["HOME"] = str(self.home)
        self._orig_cwd = Path.cwd()
        os.chdir(self.home)
        # doctor が FASTMETAL_API_KEY の有無を見るため、テストでは必ず消す
        self._orig_key = os.environ.pop("FASTMETAL_API_KEY", None)

    def tearDown(self) -> None:
        os.chdir(self._orig_cwd)
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._orig_key is not None:
            os.environ["FASTMETAL_API_KEY"] = self._orig_key
        self._tmp.cleanup()

    # helpers ------------------------------------------------------------

    def make_git_repo(self, name: str = "repo") -> Path:
        """テスト用の空 Git リポジトリを作り、そこへ移動する。"""
        import subprocess

        repo = self.home / name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        return repo

    def cd(self, path: Path) -> None:
        os.chdir(path)
