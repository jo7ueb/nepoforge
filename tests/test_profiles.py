"""プロファイル読み込み・適用のテスト (#47)。"""

from __future__ import annotations

import unittest

from nepoforge import budget, derive, jsonc, profiles, store
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


class TestParseModelWhitespace(unittest.TestCase):
    """モデル表記の空白・制御文字は読み込み時に拒否する (#52 監査 W01)。"""

    def test_internal_space_rejected(self) -> None:
        for text in ("p/ m", "a b/c d", "fastmetal/glm 5.3"):
            with self.assertRaises(NepoforgeError):
                profiles.parse_model(text)

    def test_surrounding_space_trimmed(self) -> None:
        ref = profiles.parse_model("  fastmetal/beam-1.0  ")
        self.assertEqual(profiles.format_model(ref), "fastmetal/beam-1.0")

    def test_control_char_rejected(self) -> None:
        with self.assertRaises(NepoforgeError):
            profiles.parse_model("fastmetal/beam\t1.0")


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
        import json

        local = store.local_profile_path()
        local.parent.mkdir(parents=True, exist_ok=True)
        roles = profiles.load_profile("economy")
        local.write_text(json.dumps({"roles": roles}))
        cfg = profiles.load_profile_config("local")
        self.assertEqual({str(c.ref) for c in cfg.candidates}, set(roles.values()))
        self.assertEqual(cfg.constraints.max_parallel, 10)
        self.assertEqual(cfg.task_rules, ())

    def test_bundled_candidates_do_not_include_legacy_premium_role_defaults(self) -> None:
        for name in ("economy", "balanced", "performance"):
            with self.subTest(profile=name):
                config = profiles.load_profile_config(name)
                candidates = {str(c.ref) for c in config.candidates}
                self.assertIn("fastmetal/gemma-4-26b-a4b-it", candidates)
                self.assertTrue(candidates.isdisjoint({
                    "fastmetal/beam-1.0", "fastmetal/kimi-k3.1-pro", "fastmetal/glm-5.3",
                }))
                self.assertTrue(set(config.roles.values()).issubset(candidates))
                for item in derive.derive_plan(config):
                    self.assertNotIn(str(item.model), derive.LEGACY_PREMIUM_MODELS)

    def test_bundled_candidate_prices_provider_and_reasoning_agree(self) -> None:
        template = (store.TEMPLATE_DIR / "provider-fastmetal.jsonc").read_text(encoding="utf-8")
        provider = jsonc.loads("{" + template + "}")["providers"]["fastmetal"]
        models = provider["models"]
        for name in ("economy", "balanced", "performance"):
            config = profiles.load_profile_config(name)
            candidates = {str(candidate.ref): candidate for candidate in config.candidates}
            self.assertIn(str(config.primary_model), candidates)
            self.assertEqual(config.constraints.providers[0].endpoint, provider["settings"]["baseURL"])
            for model, candidate in candidates.items():
                with self.subTest(profile=name, model=model):
                    self.assertIn(model, budget.PRICES)
                    self.assertTrue(candidate.no_training)
                    self.assertTrue(candidate.approval_ref)
                    self.assertTrue(candidate.verified_on)
                    provider_model = models[candidate.ref.model]
                    self.assertEqual(provider_model["limit"]["context"], candidate.context_tokens)
                    self.assertEqual(provider_model["capabilities"]["tools"], "tool_calling" in candidate.capabilities)
                    self.assertEqual(provider_model["compatibility"]["maxTokensField"], "max_tokens")
                    reasoning = provider_model.get("body", {}).get("reasoning")
                    self.assertEqual(reasoning, {
                        "off": {"enabled": False}, "low": {"effort": "low"},
                        "provider_default": None,
                    }[candidate.reasoning])
            for rule in config.task_rules:
                provider_model = models[candidates[rule.model].ref.model]
                self.assertLessEqual(rule.max_output_tokens, provider_model["limit"]["output"])

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
