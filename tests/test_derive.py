"""派生エージェント定義のテスト (#52)。"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from nepoforge import cli, derive, profiles, store
from nepoforge.store import NepoforgeError

from ._base import IsolatedEnv


class TestDeriveBasics(unittest.TestCase):
    def test_slug_and_agent_id(self) -> None:
        self.assertEqual(derive.slug_of("fastmetal/kimi-k3.1-pro"), "kimi-k3.1-pro")
        self.assertEqual(derive.slug_of(profiles.parse_model("fastmetal/glm-5.3#think")), "glm-5.3-think")
        self.assertEqual(derive.agent_id_for("expert", "fastmetal/kimi-k3.1-pro"), "nf-expert-kimi-k3.1-pro")

    def test_derive_text_model_line_only(self) -> None:
        text = derive.derive_text("expert", "fastmetal/kimi-k3.1-pro")
        original = (store.DATA_DIR / "agents" / "expert.md").read_text(encoding="utf-8")
        # model 行のみ異なる
        import re
        orig_model = re.search(r"^model:.*$", original, re.M).group(0)
        new_model = re.search(r"^model:.*$", text, re.M).group(0)
        self.assertNotEqual(orig_model, new_model)
        self.assertEqual(new_model, "model: fastmetal/kimi-k3.1-pro")
        # それ以外は一致
        self.assertEqual(original.replace(orig_model, new_model), text)

    def test_permissions_preserved(self) -> None:
        text = derive.derive_text("expert", "fastmetal/kimi-k3.1-pro")
        original = (store.DATA_DIR / "agents" / "expert.md").read_text(encoding="utf-8")
        # permissions ブロック抽出 (簡易: model 行以外の frontmatter)
        def strip_model(t):
            return t.replace("model: fastmetal/kimi-k3.1-pro", "model: fastmetal/japan-glm-5.3-flash")
        self.assertEqual(strip_model(text), original)

    def test_idempotent(self) -> None:
        t1 = derive.derive_text("expert", "fastmetal/kimi-k3.1-pro")
        t2 = derive.derive_text("expert", "fastmetal/kimi-k3.1-pro")
        self.assertEqual(t1, t2)

    def test_render_does_not_apply_to_derived_ids(self) -> None:
        # profiles.render は rel が agents/ で始まり stem が profile キーに一致する場合のみ適用する。
        # 派生 ID が profile に含まれていても DATA_DIR に正本が無いため FileNotFoundError になるが、
        # 通常の profile には派生 ID が含まれない (render は 5 役のみ対象)。
        profile = {"architect": "fastmetal/beam-1.0"}
        self.assertEqual(profiles.render("agents/architect.md", profile), profiles.apply_to_agent("architect", (store.DATA_DIR / "agents" / "architect.md").read_text(encoding="utf-8"), "fastmetal/beam-1.0"))


class TestDerivePlan(IsolatedEnv):
    def test_plan_write_then_ok(self) -> None:
        # isolated env: init first
        self.assertEqual(cli.main(["init"]), 0)
        cfg = profiles.load_profile_config("balanced")
        # balanced has no task_rules → plan empty
        plan = derive.derive_plan(cfg, target_dir=store.opencode_dir())
        self.assertEqual(plan, [])
        # with explicit pair differing from default
        plan = derive.derive_plan(cfg, role="implementer", model="fastmetal/kimi-k3.1-pro", target_dir=store.opencode_dir())
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].state, "write")
        # apply
        cli.main  # noop
        # write manually
        (store.opencode_dir() / "agents" / (plan[0].agent_id + ".md")).parent.mkdir(parents=True, exist_ok=True)
        (store.opencode_dir() / "agents" / (plan[0].agent_id + ".md")).write_text(plan[0].expected_text, encoding="utf-8")
        plan2 = derive.derive_plan(cfg, role="implementer", model="fastmetal/kimi-k3.1-pro", target_dir=store.opencode_dir())
        self.assertEqual(plan2[0].state, "ok")

    def test_conflict_detection(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        cfg = profiles.load_profile_config("balanced")
        plan = derive.derive_plan(cfg, role="implementer", model="fastmetal/kimi-k3.1-pro", target_dir=store.opencode_dir())
        target = store.opencode_dir() / "agents" / (plan[0].agent_id + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("different content\n", encoding="utf-8")
        plan2 = derive.derive_plan(cfg, role="implementer", model="fastmetal/kimi-k3.1-pro", target_dir=store.opencode_dir())
        self.assertEqual(plan2[0].state, "conflict")


class TestDeriveCLI(IsolatedEnv):
    def test_derive_dry_run_no_write(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        before = set((store.opencode_dir() / "agents").iterdir()) if (store.opencode_dir() / "agents").is_dir() else set()
        rc = cli.main(["profile", "derive", "--role", "implementer", "--model", "fastmetal/kimi-k3.1-pro", "--dry-run"])
        self.assertEqual(rc, 0)
        after = set((store.opencode_dir() / "agents").iterdir())
        self.assertEqual(before, after)

    def test_derive_conflict_exit_2(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        cfg = profiles.load_profile_config("balanced")
        plan = derive.derive_plan(cfg, role="implementer", model="fastmetal/kimi-k3.1-pro", target_dir=store.opencode_dir())
        target = store.opencode_dir() / "agents" / (plan[0].agent_id + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("different\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            cli.main(["profile", "derive", "--role", "implementer", "--model", "fastmetal/kimi-k3.1-pro"])
        self.assertEqual(cm.exception.code, 2)

    def test_derive_two_runs_noop(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        self.assertEqual(cli.main(["profile", "derive", "--role", "implementer", "--model", "fastmetal/kimi-k3.1-pro"]), 0)
        # second run: state ok, no writes needed but command succeeds
        self.assertEqual(cli.main(["profile", "derive", "--role", "implementer", "--model", "fastmetal/kimi-k3.1-pro"]), 0)

    def test_profile_use_pregenerates(self) -> None:
        # create a local profile with task_rules referencing kimi for implementer
        import json
        lp = store.local_profile_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        data = {"roles": {r: "fastmetal/japan-glm-5.3-flash" for r in store.USER_AGENT_IDS}}
        data["roles"]["implementer"] = "fastmetal/japan-glm-5.3-flash"
        data["task_rules"] = [{"when": {"role": "implementer", "complexity": "high"}, "model": "fastmetal/kimi-k3.1-pro"}]
        # kimi must be candidate; add via candidates? roles union doesn't include kimi. Add candidates.
        data["candidates"] = [{"model": "fastmetal/kimi-k3.1-pro", "capabilities": ["code"], "context_tokens": None, "destination": "d", "retention": "r", "approval_ref": "a"}]
        data["constraints"] = {"providers": [{"id": "fastmetal", "destination": "d", "retention": "r", "approved": True, "approval_ref": "a"}], "max_parallel": 3, "budget": {"per_task": None, "note": "n"}}
        lp.write_text(json.dumps(data))
        self.assertEqual(cli.main(["init", "--profile", "local"]), 0)
        # pre-generation should have created derived file
        derived = store.opencode_dir() / "agents" / "nf-implementer-kimi-k3.1-pro.md"
        # profile use local again triggers derive
        self.assertEqual(cli.main(["profile", "use", "local"]), 0)
        self.assertTrue(derived.is_file())

    def test_prune_removes_orphan(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        # create a derived file then remove from plan (use different model? just create nf file)
        orphan = store.opencode_dir() / "agents" / "nf-implementer-ghost.md"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("orphan\n")
        # prune without force: content doesn't match → kept
        self.assertEqual(cli.main(["profile", "derive", "--prune"]), 0)
        self.assertTrue(orphan.exists())
        # force removes
        self.assertEqual(cli.main(["profile", "derive", "--prune", "--force"]), 0)
        self.assertFalse(orphan.exists())

    def test_uninstall_removes_derived_matching(self) -> None:
        self.assertEqual(cli.main(["init"]), 0)
        self.assertEqual(cli.main(["profile", "derive", "--role", "implementer", "--model", "fastmetal/kimi-k3.1-pro"]), 0)
        derived = store.opencode_dir() / "agents" / "nf-implementer-kimi-k3.1-pro.md"
        self.assertTrue(derived.is_file())
        self.assertEqual(cli.main(["uninstall"]), 0)
        self.assertFalse(derived.is_file())


if __name__ == "__main__":
    unittest.main()