"""Strict delegation CLI の合成 run fixture (#76)。"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from nepoforge import cli
from nepoforge import profiles

from ._base import IsolatedEnv


ISSUE = "#76"


def task(task_id: str, stage: str, deliverable: str, **extra: object) -> dict:
    return {
        "task_id": task_id,
        "role": "explore",
        "executor": "tool",
        "task_kind": "test",
        "stage": stage,
        "issue_ref": ISSUE,
        "objective": f"{task_id} の目的",
        "inputs": ["固定fixture"],
        "expected_output": f"{deliverable} の結果",
        "acceptance_criteria": ["終了コード0"],
        "read_scope": ["tests/**"],
        "write_scope": [],
        "depends_on": [],
        "deliverables": [deliverable],
        **extra,
    }


class TestStrictDelegationCLI(IsolatedEnv):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.home / "work"
        self.work.mkdir()
        self.approve_project_policy(self.home, "private")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init"]), 0)

    def plan(self, tasks: list[dict]) -> tuple[str, str]:
        document = {
            "schema_version": 1,
            "issue_ref": ISSUE,
            "deliverables": [d for item in tasks for d in item["deliverables"]],
            "tasks": tasks,
        }
        path = self.work / "plan.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        # validate CLI による正規 digest を ready の gate に反映する
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["plan", "validate", str(path), "--contract", "--json"]), 0)
        digest = json.loads(output.getvalue())["plan_sha256"]
        return str(path), digest

    def write_gate(
        self, digest: str, attempts: list[dict], *, run_id: str = "r1",
        spent_yen: int = 0,
    ) -> str:
        primary_model = profiles.load_profile_config("balanced").primary_for("private")
        primary_message = {
            "id": "parent-message", "session_id": "ses-parent", "created_ms": 120,
            "agent": "build",
            "model": {"providerID": primary_model.provider, "id": primary_model.model,
                      "variant": primary_model.variant},
        } if primary_model else None
        gate = self.work / "gate.json"
        gate.write_text(json.dumps({
            "run_id": run_id,
            "plan_sha256": digest,
            "started_ms": 100,
            "as_of_ms": 300,
            "primary": {
                "session_id": "ses-parent", "outcome": "running",
                "spent_yen": spent_yen, "reserved_yen": 0,
                "messages": [primary_message] if primary_message else [],
            },
            "attempts": attempts,
            "budget_error": False,
        }), encoding="utf-8")
        return str(gate)

    @staticmethod
    def attempt(tid: str, session: str, began: int, ended: int | None,
                *, outcome: str = "accepted", exit_code: int | None = 0) -> dict:
        return {
            "task_id": tid, "attempt": 1, "session_id": session,
            "parent_session_id": "ses-parent", "created_ms": began,
            "started_ms": began, "ended_ms": ended, "outcome": outcome,
            "exit_code": exit_code, "messages": [],
        }

    def ready(self, plan: str, gate: str, *extra: str) -> dict:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main([
                "plan", "ready", plan, "--contract", "--run-id", "r1",
                "--gate", gate, "--limit-yen", "100", "--json", *extra,
            ]), 0)
        return json.loads(output.getvalue())

    def test_contract_entry_accepts_evidenced_tool_dependency_without_stage_barrier(self) -> None:
        tasks = [
            task("setup", "requirements", "D1"),
            task("independent", "research", "D2"),
            task("later", "implementation", "D3", depends_on=["setup"]),
        ]
        plan, digest = self.plan(tasks)
        gate = self.write_gate(digest, [
            self.attempt("setup", "ses-setup", 110, 150),
            self.attempt("independent", "ses-independent", 110, 160),
        ])
        result = self.ready(plan, gate)
        self.assertEqual(result["launch"], ["later"])
        self.assertEqual(result["not_ready"], ["independent", "setup"])
        self.assertEqual(result["run_report"]["walltime_ms"], 200)
        self.assertEqual(result["run_report"]["parallel_overlaps"][0]["walltime_ms"], 40)

    def test_self_report_without_success_evidence_does_not_unlock_dependency(self) -> None:
        tasks = [
            task("first", "implementation", "D1"),
            task("next", "test", "D2", depends_on=["first"]),
        ]
        plan, digest = self.plan(tasks)
        gate = self.write_gate(digest, [
            self.attempt("first", "ses-first", 110, 150, exit_code=1),
        ])
        result = self.ready(plan, gate)
        self.assertEqual(result["launch"], [])
        self.assertIn("next", result["not_ready"])

    def test_scope_conflict_and_unapproved_primary_exception_are_rejected(self) -> None:
        conflict = [
            task("one", "implementation", "D1", role="implementer", executor="agent",
                 task_kind=None, write_scope=["src/**"]),
            task("two", "implementation", "D2", role="implementer", executor="agent",
                 task_kind=None, write_scope=["src/main.py"]),
        ]
        plan, digest = self.plan(conflict)
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                self.ready(plan, self.write_gate(digest, [
                    self.attempt("one", "ses-one", 110, 200),
                    self.attempt("two", "ses-two", 150, 220),
                ]))

        exception = task(
            "primary", "implementation", "D1", role="implementer",
            executor="primary", write_scope=["src/main.py"],
            exception={"reason": "human_approved", "detail": "承認済み", "scope": ["src/main.py"]},
        )
        path = self.work / "exception.json"
        path.write_text(json.dumps({
            "schema_version": 1, "issue_ref": ISSUE,
            "deliverables": ["D1"], "tasks": [exception],
        }), encoding="utf-8")
        plan = str(path)
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                cli.main(["plan", "validate", plan, "--contract"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([
                "plan", "validate", plan, "--contract",
                "--approved-exception", "primary",
            ]), 0)

    def test_strict_rejects_manual_state_and_model_override_fields(self) -> None:
        plan, digest = self.plan([task("one", "test", "D1")])
        gate = self.write_gate(digest, [])
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                cli.main([
                    "plan", "ready", plan, "--contract", "--run-id", "r1",
                    "--gate", gate, "--limit-yen", "100", "--accepted", "one",
                ])
        item = task("one", "test", "D1")
        item["model_request"] = "fastmetal/beam-1.0"
        path = self.work / "override.json"
        path.write_text(json.dumps({
            "schema_version": 1, "issue_ref": ISSUE,
            "deliverables": ["D1"], "tasks": [item],
        }), encoding="utf-8")
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                cli.main(["plan", "validate", str(path), "--contract"])

    def test_tool_dependencies_and_verified_agent_model_unlock_in_order(self) -> None:
        tasks = [
            task("validate", "implementation", "D1", role="implementer", executor="agent",
                 task_kind=None, responsibility="leaf", write_scope=["src/validate.py"]),
            task("guide", "implementation", "D2", role="implementer", executor="agent",
                 task_kind=None, responsibility="leaf", write_scope=["docs/guide.md"]),
            task("tests", "test", "D3", depends_on=["validate"], role="explore",
                 executor="tool", read_scope=["tests/**"]),
            task("ci", "test", "D4", depends_on=["tests"], role="explore",
                 executor="tool", task_kind="ci-status", read_scope=["tests/**"]),
        ]
        plan, digest = self.plan(tasks)
        gate = self.write_gate(digest, [])
        empty = self.ready(plan, gate)
        self.assertEqual(empty["launch"], ["validate", "guide"])
        self.assertEqual(empty["resolved"]["validate"], "fastmetal/gemma-4-26b-a4b-it")
        self.assertNotIn("tests", empty["launch"])
        self.assertNotIn("ci", empty["launch"])

        actual = {
            "id": "validate-message", "session_id": "ses-validate",
            "parent_session_id": "ses-parent", "created_ms": 180,
            "agent": empty["model_resolutions"]["validate"]["binding"],
            "model": {"providerID": "fastmetal", "id": "gemma-4-26b-a4b-it", "variant": None},
        }
        validate_attempt = self.attempt("validate", "ses-validate", 150, 200)
        validate_attempt.update({
            "model": empty["resolved"]["validate"], "spent_yen": 1,
            "messages": [actual],
        })
        guide_running = self.attempt(
            "guide", "ses-guide", 150, None, outcome="running", exit_code=None,
        )
        succeeded = self.ready(plan, self.write_gate(digest, [guide_running, validate_attempt]))
        self.assertEqual(succeeded["launch"], ["tests"])

        wrong_model_attempt = dict(validate_attempt)
        wrong_model_attempt["messages"] = [dict(actual, model={
            "providerID": "other", "id": "wrong-model", "variant": None,
        })]
        blocked = self.ready(plan, self.write_gate(digest, [guide_running, wrong_model_attempt]))
        self.assertNotIn("tests", blocked["launch"])
        self.assertIn("tests", blocked["not_ready"])

    def test_budget_gate_blocks_at_limit_but_allows_light_agent_with_budget(self) -> None:
        agent = task(
            "light", "implementation", "D1", role="explore", executor="agent",
            task_kind=None, responsibility="leaf", complexity="low", risk="low",
            write_scope=[],
        )
        plan, digest = self.plan([agent])
        exhausted = self.ready(plan, self.write_gate(digest, [], spent_yen=100))
        self.assertEqual(exhausted["launch"], [])

        available = self.ready(plan, self.write_gate(digest, [], spent_yen=0))
        self.assertEqual(available["launch"], ["light"])

    def test_primary_exception_completion_unlocks_child_and_charges_parent_once(self) -> None:
        primary = task(
            "primary-work", "implementation", "D1", role="implementer",
            executor="primary", task_kind=None, responsibility="integration",
            write_scope=["src/main.py"],
            exception={
                "reason": "inseparable", "detail": "変更を一体として実施する必要がある",
                "scope": ["src/main.py"],
            },
        )
        child = task(
            "child-check", "test", "D2", role="implementer", executor="agent",
            task_kind=None, responsibility="leaf", write_scope=["tests/main_test.py"],
            depends_on=["primary-work"],
        )
        plan, digest = self.plan([primary, child])

        initial = self.ready(plan, self.write_gate(digest, [], spent_yen=2))
        self.assertEqual(initial["launch"], ["primary-work"])
        self.assertIn("child-check", initial["not_ready"])

        primary_attempt = self.attempt("primary-work", "ses-parent", 110, 180)
        primary_attempt.update({"success": True, "spent_yen": 3})
        completed_parent_gate = self.write_gate(
            digest, [primary_attempt], spent_yen=3,
        )
        gate_document = json.loads(Path(completed_parent_gate).read_text(encoding="utf-8"))
        gate_document["primary"].update({
            "outcome": "accepted", "success": True, "ended_ms": 180,
        })
        Path(completed_parent_gate).write_text(json.dumps(gate_document), encoding="utf-8")
        released = self.ready(
            plan, completed_parent_gate,
        )
        self.assertEqual(released["launch"], ["child-check"], released)

        child_model = released["resolved"]["child-check"]
        provider, model_id = child_model.split("/", 1)
        child_message = {
            "id": "child-message", "session_id": "ses-child",
            "parent_session_id": "ses-parent", "created_ms": 195,
            "agent": released["model_resolutions"]["child-check"]["binding"],
            "model": {"providerID": provider, "id": model_id, "variant": None},
        }
        completed_child = self.attempt("child-check", "ses-child", 190, 220)
        completed_child.update({
            "model": child_model, "spent_yen": 4, "messages": [child_message],
        })
        final_gate = self.write_gate(digest, [primary_attempt, completed_child], spent_yen=3)
        gate_document = json.loads(Path(final_gate).read_text(encoding="utf-8"))
        gate_document["primary"].update({
            "outcome": "accepted", "success": True, "ended_ms": 180,
        })
        Path(final_gate).write_text(json.dumps(gate_document), encoding="utf-8")
        completed = self.ready(
            plan, final_gate,
        )
        accepted = {
            row["task_id"] for row in completed["run_report"]["tasks"]
            if row["decision"] == "accepted"
        }
        self.assertEqual(accepted, {"primary-work", "child-check"})
        self.assertEqual(completed["run_report"]["costs"]["total_spent_yen"], "7")
        self.assertEqual(completed["run_report"]["costs"]["primary_spent_yen"], "3")


if __name__ == "__main__":
    unittest.main()
