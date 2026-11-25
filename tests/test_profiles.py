"""プロファイル読み込み・適用のテスト (#47)。"""

from __future__ import annotations

import unittest

from nepoforge import profiles, store
from nepoforge.store import NepoforgeError
from tests._base import IsolatedEnv

AGENT_TMPL = """---
description: test agent
mode: subagent
model: fastmetal/japan-glm-5.3-flash
permissions:
  - action: "*"
    resource: "*"
    effect: deny
---

body
"""


class TestLoadProfile(unittest.TestCase):
    def test_load_packaged_profile(self) -> None:
        prof = profiles.load_profile("balanced")
        for role in store.USER_AGENT_IDS:
            self.assertIn(role, prof)
            self.assertRegex(prof[role], r"^[a-z0-9][a-z0-9._-]*/[a-z0-9]")

    def test_unknown_profile(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.load_profile("no-such-profile")

    def test_invalid_model_notation(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"roles": {r: "not a model!" for r in store.USER_AGENT_IDS}}))
            original = profiles.profile_dir
            profiles.profile_dir = lambda: Path(tmp)  # type: ignore[assignment]
            try:
                with self.assertRaises(NepoforgeError):
                    profiles.load_profile("bad")
            finally:
                profiles.profile_dir = original  # type: ignore[assignment]


class TestApply(unittest.TestCase):
    def test_apply_replaces_model_line_in_frontmatter(self) -> None:
        out = profiles.apply_to_agent("architect", AGENT_TMPL, "fastmetal/beam-1.0")
        self.assertIn("model: fastmetal/beam-1.0\n", out)
        # 本文 (frontmatter 以外) は無傷
        self.assertIn("effect: deny", out)
        self.assertTrue(out.endswith("body\n"))

    def test_apply_only_frontmatter(self) -> None:
        text = AGENT_TMPL + "\nmodel: should-not-change\n"
        out = profiles.apply_to_agent("architect", text, "fastmetal/beam-1.0")
        self.assertIn("model: fastmetal/beam-1.0", out)
        self.assertIn("model: should-not-change", out)

    def test_no_frontmatter_raises(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.apply_to_agent("architect", "no frontmatter here\n", "fastmetal/beam-1.0")

    def test_no_model_line_raises(self) -> None:
        text = "---\ndescription: x\n---\nbody\n"
        with self.assertRaises(NepoforgeError):
            profiles.apply_to_agent("architect", text, "fastmetal/beam-1.0")

    def test_render_command_file_is_untouched(self) -> None:
        text = profiles.render("commands/nfgo.md", {"architect": "fastmetal/beam-1.0"})
        self.assertEqual(text, (store.DATA_DIR / "commands/nfgo.md").read_text(encoding="utf-8"))


class TestParseModel(unittest.TestCase):
    def test_valid_simple(self) -> None:
        ref = profiles.parse_model("fastmetal/japan-glm-5.3-flash")
        self.assertEqual(ref.provider, "fastmetal")
        self.assertEqual(ref.model, "japan-glm-5.3-flash")
        self.assertIsNone(ref.variant)
        self.assertEqual(profiles.format_model(ref), "fastmetal/japan-glm-5.3-flash")

    def test_valid_variant(self) -> None:
        ref = profiles.parse_model("fastmetal/glm-5.3#think")
        self.assertEqual(ref.variant, "think")
        self.assertEqual(profiles.format_model(ref), "fastmetal/glm-5.3#think")

    def test_invalid_multiple_hash(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.parse_model("fastmetal/x#y#z")

    def test_invalid_no_provider(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.parse_model("justmodel")

    def test_invalid_empty(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.parse_model("")


class TestLoadProfileConfig(IsolatedEnv):
    def test_roles_only_profile_defaults(self) -> None:
        cfg = profiles.load_profile_config("balanced")
        bundled = set()
        for name in profiles.available_profiles():
            if name == "local":
                continue
            bundled.update(profiles.load_profile(name).values())
        # 既定候補は同梱プロファイルの roles の和集合
        self.assertEqual(len(cfg.candidates), len(bundled))
        roles_models = set(cfg.roles.values())
        cand_models = {profiles.format_model(c.ref) for c in cfg.candidates}
        for m in roles_models:
            self.assertIn(m, cand_models)
        self.assertEqual(cfg.constraints.max_parallel, 3)
        self.assertEqual(cfg.task_rules, ())

    def test_unknown_top_level_key_allowed(self) -> None:
        import json, tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            data = {"name": "p", "roles": {r: "fastmetal/japan-glm-5.3-flash" for r in store.USER_AGENT_IDS}, "extra_key": 1}
            path.write_text(json.dumps(data))
            orig = profiles.profile_dir
            profiles.profile_dir = lambda: Path(tmp)  # type: ignore[assignment]
            try:
                cfg = profiles.load_profile_config("p")
                self.assertEqual(cfg.name, "p")
            finally:
                profiles.profile_dir = orig  # type: ignore[assignment]

    def test_task_rule_model_not_candidate_raises(self) -> None:
        import json, tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            data = {
                "roles": {r: "fastmetal/japan-glm-5.3-flash" for r in store.USER_AGENT_IDS},
                "task_rules": [{"when": {"role": "implementer"}, "model": "fastmetal/nope"}],
            }
            path.write_text(json.dumps(data))
            orig = profiles.profile_dir
            profiles.profile_dir = lambda: Path(tmp)  # type: ignore[assignment]
            try:
                with self.assertRaises(NepoforgeError):
                    profiles.load_profile_config("p")
            finally:
                profiles.profile_dir = orig  # type: ignore[assignment]


class TestLocalProfileConfig(IsolatedEnv):
    """local プロファイルを使うテスト。XDG を隔離して実ユーザー設定に触れない。"""

    def test_local_role_model_added_to_candidates(self) -> None:
        import json

        lp = store.local_profile_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        data = {"roles": {r: "fastmetal/japan-glm-5.3-flash" for r in store.USER_AGENT_IDS}}
        data["roles"]["implementer"] = "fastmetal/beam-1.0"
        lp.write_text(json.dumps(data))
        cfg = profiles.load_profile_config("local")
        cand_models = {profiles.format_model(c.ref) for c in cfg.candidates}
        self.assertIn("fastmetal/beam-1.0", cand_models)


if __name__ == "__main__":
    unittest.main()
