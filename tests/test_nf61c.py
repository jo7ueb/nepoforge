"""#61-c の修正 (slug 衝突・削除経路・dry-run・プロファイル名検証) のテスト。

API 課金なし。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from nepoforge import cli, derive, profiles, store
from nepoforge.store import NepoforgeError

from ._base import IsolatedEnv


def _fake_config(task_rules: list[dict]) -> profiles.ProfileConfig:
    """derive_plan に渡す最小限の ProfileConfig を作る。"""
    from nepoforge import jsonc as _jsonc  # noqa: F401  (明示は不要だが一貫性のため)

    rules = []
    for raw in task_rules:
        rules.append(
            profiles.TaskRule(when=raw["when"], model=raw["model"])
        )
    roles = {r: "fastmetal/japan-glm-5.3-flash" for r in store.USER_AGENT_IDS}
    return profiles.ProfileConfig(name="test", roles=roles, task_rules=tuple(rules))


class TestSlugCollision(IsolatedEnv):
    def test_same_slug_different_provider_is_rejected(self) -> None:
        # provider を除いた slug が一致する 2 モデルは、派生定義を黙って
        # 上書きするため拒否する (#61 項目 4)
        config = _fake_config(
            [
                {"when": {"role": "expert", "complexity": "high"}, "model": "fastmetal/beam-1.0"},
                {"when": {"role": "expert", "complexity": "low"}, "model": "openai/beam-1.0"},
            ]
        )
        with self.assertRaisesRegex(NepoforgeError, "collision") as ctx:
            derive.derive_plan(config, target_dir=self.home)
        self.assertIn("beam-1.0", str(ctx.exception))

    def test_distinct_slows_do_not_collide(self) -> None:
        config = _fake_config(
            [
                {"when": {"role": "expert", "complexity": "high"}, "model": "fastmetal/beam-1.0"},
                {"when": {"role": "expert", "complexity": "low"}, "model": "fastmetal/glm-5.3"},
            ]
        )
        items = derive.derive_plan(config, target_dir=self.home)
        ids = [it.agent_id for it in items]
        self.assertEqual(len(ids), len(set(ids)))


class TestProfileNameValidation(IsolatedEnv):
    def test_path_traversal_rejected(self) -> None:
        for name in ("../../etc/passwd", "a/b", r"a\\b", "..", ".hidden", "x y"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(NepoforgeError, "invalid profile name"):
                    profiles.load_profile(name)
                with self.assertRaisesRegex(NepoforgeError, "invalid profile name"):
                    profiles.load_profile_config(name)

    def test_valid_names_still_work(self) -> None:
        for name in ("balanced", "economy", "performance", "my-profile_1.2"):
            with self.subTest(name=name):
                if name in ("balanced", "economy", "performance"):
                    profiles.load_profile(name)
                else:
                    # 未登録でも「unknown profile」で止まる (パス検証ではない)
                    with self.assertRaisesRegex(NepoforgeError, "unknown profile"):
                        profiles.load_profile(name)

    def test_cli_rejects_traversal(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["profile", "show", "../../../etc/passwd"])


class TestUninstallDryRunWritesNothing(IsolatedEnv):
    def _install_and_modify(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        agents_dir = store.opencode_dir() / "agents"
        # 管理対象ファイルを利用者改変する (uninstall で backup+削除の経路)
        target = agents_dir / "expert.md"
        target.write_text("modified by user\n", encoding="utf-8")
        # 派生定義らしき改変ファイルも置く (--force で backup+削除の経路)
        (agents_dir / "nf-explore-beam-1.0.md").write_text("user content\n", encoding="utf-8")

    def _snapshot(self) -> list[str]:
        roots = (store.opencode_dir(), Path(self.xdg_state))
        return sorted(str(p) for r in roots for p in r.rglob("*"))

    def test_dry_run_creates_no_backup(self) -> None:
        self._install_and_modify()
        before = self._snapshot()
        self.assertEqual(
            cli.main(["uninstall", "--dry-run", "--force"]), 0
        )
        self.assertEqual(self._snapshot(), before)

    def test_real_run_backs_up_modified_files(self) -> None:
        self._install_and_modify()
        before = self._snapshot()
        self.assertEqual(cli.main(["uninstall", "--force"]), 0)
        after = self._snapshot()
        # backup が作られている (dry-run ではなく実行時のみ)
        self.assertTrue(set(after) - set(before), "backup files should exist after real run")


if __name__ == "__main__":
    unittest.main()
