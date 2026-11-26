"""`nepoforge plan` サブコマンドの統合テスト (#49)。

計画検証・モデル解決・ready 判定・予算ゲートを、一時環境で課金なしに検証する。
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from nepoforge import budget, cli, store

from ._base import IsolatedEnv


def _write_plan(tmp: dict, tasks: list[dict]) -> str:
    path = tmp["dir"] / "plan.json"
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    return str(path)


class TestPlanValidate(IsolatedEnv):
    def setUp(self) -> None:
        super().setUp()
        self.dir = self.home / "work"
        self.dir.mkdir()
        self.tmp = {"dir": self.dir}

    def test_valid_plan(self) -> None:
        file = _write_plan(self.tmp, [{"task_id": "T1", "role": "explore"}])
        self.assertEqual(cli.main(["plan", "validate", file]), 0)
        self.assertEqual(cli.main(["plan", "validate", file, "--json"]), 0)

    def test_cycle_rejected(self) -> None:
        file = _write_plan(
            self.tmp,
            [
                {"task_id": "T1", "role": "explore", "depends_on": ["T2"]},
                {"task_id": "T2", "role": "explore", "depends_on": ["T1"]},
            ],
        )
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["plan", "validate", file])
        self.assertEqual(ctx.exception.code, 2)

    def test_broken_file_rejected(self) -> None:
        file = self.dir / "broken.json"
        file.write_text("not json", encoding="utf-8")
        with self.assertRaises(SystemExit):
            cli.main(["plan", "validate", str(file)])


class TestPlanReady(IsolatedEnv):
    def setUp(self) -> None:
        super().setUp()
        self.dir = self.home / "work"
        self.dir.mkdir()
        self.tmp = {"dir": self.dir}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init"]), 0)
        self.limit = 100.0

    def _ready(self, tasks: list[dict], *args: str) -> dict:
        plan = _write_plan(self.tmp, tasks)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["plan", "ready", plan, "--json", *args]), 0)
        return json.loads(output.getvalue())

    def test_reservations_accumulate_within_and_across_batches(self) -> None:
        tasks = [
            {
                "task_id": f"T{i}", "role": "reviewer",
                "max_input_tokens": 1000, "max_output_tokens": 0, "retries": 1,
            }
            for i in range(1, 4)
        ]
        with patch.dict(budget.PRICES, {model: (5000, 0) for model in budget.PRICES}):
            first = self._ready(tasks, "--limit-yen", "20")
            self.assertEqual(first["launch"], ["T1", "T2"])
            self.assertEqual(first["reservations_yen"], {"T1": 10, "T2": 10})
            self.assertEqual([d["task_id"] for d in first["denied"]], ["T3"])

            # T1 の実績は 8 円。T2 は実行中で、T3 は再判定しても起動できない。
            second = self._ready(
                tasks, "--limit-yen", "20", "--accepted", "T1",
                "--spent-yen", "8", "--running", "T2", "--reserved-yen", "10",
            )
            self.assertEqual(second["reservations_yen"], {})
            self.assertEqual(second["launch"], [])
            self.assertIn("28.0000", second["denied"][0]["reason"])

            # T2 も 2 円で終了し、実績合計 10 円に精算したら T3 に枠が空く。
            state = ("--limit-yen", "20", "--accepted", "T1,T2", "--spent-yen", "10")
            self.assertEqual(self._ready(tasks, *state)["reservations_yen"], {"T3": 10})
            stopped = self._ready(tasks, *state, "--budget-error")
            self.assertEqual(stopped["reservations_yen"], {})
            self.assertIn("already exceeded", stopped["denied"][0]["reason"])

    def _plan(self) -> str:
        return _write_plan(
            self.tmp,
            [
                {"task_id": "T1", "role": "explore"},
                {
                    "task_id": "T2",
                    "role": "implementer",
                    "depends_on": ["T1"],
                    "write_scope": ["nepoforge/x.py"],
                },
            ],
        )

    def test_roots_launch_within_budget(self) -> None:
        out = self.dir / "out.json"
        # 出力をファイルに取るため capsys の代わりに stdout を差し替える
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(["plan", "ready", self._plan(), "--limit-yen", str(self.limit), "--json"]),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], ["T1"])
        self.assertEqual(result["not_ready"], ["T2"])
        self.assertEqual(result["resolved"]["T1"], "fastmetal/gemma-4-26b-a4b-it")

    def test_dependent_launches_after_acceptance(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(
                    [
                        "plan", "ready", self._plan(),
                        "--accepted", "T1",
                        "--profile", "economy",
                        "--limit-yen", str(self.limit),
                        "--json",
                    ]
                ),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["profile"], "economy")
        self.assertEqual(result["ready"], ["T2"])
        self.assertEqual(result["launch"], ["T2"])
        self.assertEqual(result["resolved"]["T2"], "fastmetal/gemma-4-26b-a4b-it")

    def test_independent_review_bindings_are_generated_and_launchable(self) -> None:
        tasks = [
            {
                "task_id": f"T{i}", "role": "reviewer", "complexity": "low", "risk": risk,
                "concern": concern, "inputs": {"snapshot": "fixed-diff"},
                "read_scope": ["nepoforge/cli.py"], "write_scope": [],
                "capabilities": ["tool_calling", "structured_output"],
            }
            for i, (concern, risk) in enumerate(
                (("requirements", "low"), ("correctness", "low"), ("security", "medium")), 1
            )
        ]
        for profile in ("economy", "balanced", "performance"):
            with self.subTest(profile=profile):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["profile", "use", profile]), 0)
                result = self._ready(tasks, "--limit-yen", "100")
                self.assertEqual(result["launch"], ["T1", "T2", "T3"])
                for task in tasks:
                    tid = task["task_id"]
                    self.assertEqual(result["resolved"][tid], "fastmetal/gemma-4-26b-a4b-it")
                    self.assertEqual(result["model_resolutions"][tid]["binding"], "nf-reviewer-gemma-4-26b-a4b-it")
                    self.assertTrue(result["model_resolutions"][tid]["reason"].startswith("task_rule["))

    def test_plan_cannot_override_rule_even_with_spoofed_source(self) -> None:
        for source in ("coordinator", "human-config", "external"):
            with self.subTest(source=source):
                result = self._ready([{
                    "task_id": "T1", "role": "reviewer", "complexity": "low", "risk": "low",
                    "model_request": "fastmetal/japan-deepseek-v4.1-flash-1017",
                    "model_request_source": source,
                }], "--limit-yen", "100")
                self.assertEqual(result["launch"], [])
                self.assertEqual(result["reservations_yen"], {})
                self.assertIn("untrusted_source", result["denied"][0]["reason"])

    def test_missing_or_modified_binding_does_not_fall_back_or_reserve(self) -> None:
        tasks = [{"task_id": "T1", "role": "reviewer", "complexity": "low", "risk": "low"}]
        path = store.opencode_dir() / "agents/nf-reviewer-gemma-4-26b-a4b-it.md"
        path.unlink()
        missing = self._ready(tasks, "--limit-yen", "100")
        self.assertEqual(missing["launch"], [])
        self.assertEqual(missing["reservations_yen"], {})
        self.assertIn("binding unavailable", missing["denied"][0]["reason"])
        path.write_text("model: fastmetal/japan-deepseek-v4.1-flash-1017\n", encoding="utf-8")
        modified = self._ready(tasks, "--limit-yen", "100")
        self.assertEqual(modified["launch"], [])
        self.assertEqual(modified["reservations_yen"], {})
        self.assertIn("binding differs", modified["denied"][0]["reason"])

    def test_ineligible_leaf_does_not_use_role_default(self) -> None:
        result = self._ready([{
            "task_id": "T1", "role": "implementer", "complexity": "low", "risk": "low",
            "write_scope": ["nepoforge/cli.py"],
            "capabilities": ["unconfirmed_capability"],
        }], "--limit-yen", "100")
        self.assertEqual(result["resolved"], {})
        self.assertEqual(result["launch"], [])
        self.assertIn("task_rule_ineligible", result["denied"][0]["reason"])

    def test_denied_cost_does_not_consume_next_tasks_budget(self) -> None:
        tasks = [
            {"task_id": "T1", "role": "reviewer", "max_input_tokens": 2000},
            {"task_id": "T2", "role": "reviewer", "max_input_tokens": 1000},
        ]
        with patch.dict(budget.PRICES, {"fastmetal/gemma-4-26b-a4b-it": (10_000, 0)}):
            result = self._ready(tasks, "--limit-yen", "10", "--retries", "0")
        self.assertEqual(result["launch"], ["T2"])
        self.assertEqual(result["reservations_yen"], {"T2": 10})

    def test_unset_limit_denies_all(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["plan", "ready", self._plan(), "--json"]), 0)
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], [])
        self.assertTrue(all("not set" in d["reason"] for d in result["denied"]))

    def test_insufficient_budget_denies(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(["plan", "ready", self._plan(), "--limit-yen", "0.1", "--json"]),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], [])
        self.assertTrue(all("insufficient budget" in d["reason"] for d in result["denied"]))

    def test_budget_error_stop_mode(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(
                    [
                        "plan", "ready", self._plan(),
                        "--limit-yen", str(self.limit),
                        "--budget-error",
                        "--json",
                    ]
                ),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], [])
        self.assertTrue(all("already exceeded" in d["reason"] for d in result["denied"]))

    def test_tool_task_needs_no_budget(self) -> None:
        import contextlib
        import io

        plan = _write_plan(
            self.tmp,
            [{"task_id": "T1", "role": "explore", "executor": "tool"}],
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # 予算上限未設定でも、ツール実行は課金なしで起動できる
            self.assertEqual(cli.main(["plan", "ready", plan, "--json"]), 0)
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], ["T1"])

    def test_untrusted_model_request_on_tool_task_denied(self) -> None:
        import contextlib
        import io

        plan = _write_plan(
            self.tmp,
            [
                {
                    "task_id": "T1",
                    "role": "explore",
                    "executor": "tool",
                    "model_request": "fastmetal/beam-1.0",
                    "model_request_source": "child_report",
                }
            ],
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(["plan", "ready", plan, "--limit-yen", str(self.limit), "--json"]),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["launch"], [])
        self.assertIn("untrusted", result["denied"][0]["reason"])

    def test_failed_dependency_blocks_descendants(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(
                    [
                        "plan", "ready", self._plan(),
                        "--accepted", "T1", "--failed", "T1",
                        "--limit-yen", str(self.limit),
                        "--json",
                    ]
                ),
                0,
            )
        result = json.loads(buf.getvalue())
        self.assertEqual(result["ready"], [])
        self.assertIn("T2", result["not_ready"])

    def test_text_output(self) -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(
                cli.main(["plan", "ready", self._plan(), "--limit-yen", str(self.limit)]),
                0,
            )
        text = buf.getvalue()
        self.assertIn("launch:", text)
        self.assertIn("not ready:", text)


if __name__ == "__main__":
    unittest.main()
