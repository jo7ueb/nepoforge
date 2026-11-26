"""Data Policy の承認・候補適格性・送信前照合を無課金で検証する。"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from itertools import product
from unittest.mock import patch

from nepoforge import cli, data_policy, profiles, resolver
from nepoforge.store import NepoforgeError

from ._base import IsolatedEnv


class TestPolicyMatrix(unittest.TestCase):
    def test_route_attributes_against_each_policy(self) -> None:
        expected = {
            "open": {(training, retention) for training, retention in product(
                (True, False, None), ("zero", "retained", None)
            )},
            "private": {(True, "zero"), (True, "retained"), (True, None)},
            "confidential": {(True, "zero")},
        }
        provider = profiles.ProviderConstraint("test", approved=True, endpoint="https://test.invalid/v1")
        ref = profiles.ModelRef("test", "example")
        for policy, training, retention in product(expected, (True, False, None), ("zero", "retained", None)):
            with self.subTest(policy=policy, no_training=training, retention=retention):
                config = profiles.ProfileConfig(
                    "balanced", roles={"reviewer": str(ref)},
                    candidates=(profiles.Candidate(ref, no_training=training, retention=retention),),
                    constraints=profiles.Constraints(providers=(provider,)),
                )
                result = resolver.resolve(resolver.TaskInput("T1", "reviewer"), config, project_policy=policy)
                self.assertEqual(result.resolved_model == ref, (training, retention) in expected[policy])

    def test_same_bundled_profile_selects_only_policy_eligible_routes(self) -> None:
        config = profiles.load_profile_config("balanced")
        task = resolver.TaskInput("T1", "expert", responsibility="integration",
                                  task_kind="specialist_analysis", complexity="high")
        for policy, model in (
            ("open", "gpt-oss-120b"),
            ("private", "gpt-oss-120b"),
            ("confidential", "japan-deepseek-v4.1-flash-1017"),
        ):
            with self.subTest(policy=policy):
                result = resolver.resolve(task, config, project_policy=policy)
                self.assertEqual(str(result.resolved_model), f"fastmetal/{model}")


class TestProjectPolicy(IsolatedEnv):
    def test_project_default_and_unapproved_or_changed_declaration(self) -> None:
        root = self.make_git_repo()
        self.cd(root)
        self.assertEqual(data_policy.load_project_policy(), "confidential")
        declaration = root / ".nepoforge.json"
        declaration.write_text('{"data_policy":"open"}', encoding="utf-8")
        with self.assertRaisesRegex(NepoforgeError, "not approved"):
            data_policy.load_project_policy()
        self.approve_project_policy(root, "private")
        subdir = root / "docs"
        subdir.mkdir()
        self.cd(subdir)
        self.assertEqual(data_policy.load_project_policy(), "private")
        declaration.write_text('{"data_policy":"open"}', encoding="utf-8")
        with self.assertRaisesRegex(NepoforgeError, "not approved"):
            data_policy.load_project_policy()
        declaration.write_text('{"data_policy":"public"}', encoding="utf-8")
        with self.assertRaisesRegex(NepoforgeError, "invalid data_policy"):
            data_policy.load_project_policy()

    def test_repo_cannot_supply_its_own_approval(self) -> None:
        root = self.make_git_repo()
        self.approve_project_policy(root, "open")
        self.cd(root)
        approvals = self.xdg_config / "nepoforge/project-policies.json"
        copy = root / "approvals.json"
        approvals.rename(copy)
        approvals.symlink_to(copy)
        with self.assertRaisesRegex(NepoforgeError, "outside the project"):
            data_policy.load_project_policy()

    def test_plan_cannot_relax_policy_or_fall_back_to_role_default(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init"]), 0)
        path = self.home / "plan.json"
        tasks = [
            {"task_id": "T1", "role": "reviewer", "data_policy": "open"},
            {"task_id": "T2", "role": "explore"},
            {"task_id": "T3", "role": "explore", "executor": "tool"},
        ]
        path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main(["plan", "ready", str(path), "--limit-yen", "100", "--json"]), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["data_policy"], "confidential")
        self.assertEqual(result["launch"], ["T3"])
        self.assertEqual(result["reservations_yen"], {})
        self.assertIn("cannot relax", result["denied"][0]["reason"])
        self.assertIn("task_rule_ineligible", result["denied"][1]["reason"])

        path.write_text(json.dumps({"tasks": tasks, "data_policy": "open"}), encoding="utf-8")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
            cli.main(["plan", "validate", str(path)])
        self.assertIn("cannot set the project", stderr.getvalue())

    def test_primary_and_manual_binding_require_eligible_route(self) -> None:
        glm = "japan-glm-5.3-flash"
        deepseek = "japan-deepseek-v4.1-flash-1017"
        base = "https://api.fastmetal.ai/v1"
        cases = [
            ("check-primary", "economy", glm, base, glm, "not eligible"),
            ("check-primary", "balanced", deepseek, base, deepseek, None),
            ("check-primary", "balanced", deepseek, "https://proxy.invalid/v1", deepseek, "unapproved"),
            ("check-route", "balanced", deepseek, base, "auto", "unapproved"),
            ("check-route", "balanced", "auto", base, "auto", "unapproved"),
            ("check-route", "balanced", "gpt-oss-20b", base, "gpt-oss-20b", "not eligible"),
            ("check-route", "balanced", "gemma-4-26b-a4b-it", base, "gemma-4-26b-a4b-it", "not eligible"),
        ]
        for command, profile, model, endpoint, upstream, reason in cases:
            with self.subTest(command=command, model=model, upstream=upstream, endpoint=endpoint):
                args = ["profile", command, "--profile", profile, "--model", f"fastmetal/{model}",
                        "--endpoint", endpoint, "--upstream-model", upstream]
                with patch("nepoforge.budget.expired_price", return_value=False):
                    if reason:
                        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as stderr:
                            cli.main(args)
                        self.assertIn(reason, stderr.getvalue())
                    else:
                        with redirect_stdout(io.StringIO()):
                            self.assertEqual(cli.main(args), 0)
