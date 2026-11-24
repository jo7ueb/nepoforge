"""プロファイル読み込み・適用のテスト (#47)。"""

from __future__ import annotations

import unittest

from nepoforge import profiles, store
from nepoforge.store import NepoforgeError

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


if __name__ == "__main__":
    unittest.main()
