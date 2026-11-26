from __future__ import annotations

import unittest
from decimal import Decimal

from nepoforge.delegation_run import evaluate_run, summarize_report
from nepoforge.planning import PlannedTask
from nepoforge.profiles import ModelRef
from nepoforge.resolver import ModelResolution


class DelegationRunTests(unittest.TestCase):
    def test_acceptance_requires_observed_model_and_binding(self):
        task = PlannedTask("T1", "explore")
        resolution = ModelResolution(
            "T1", "agent", "explore", ModelRef("p", "m"), "explore", "default"
        )
        doc = {
            "run_id": "r", "plan_sha256": "h", "started_ms": 1, "as_of_ms": 4,
            "budget_error": False,
            "primary": {"session_id": "parent", "spent_yen": 1, "outcome": "accepted",
                        "success": True, "ended_ms": 4,
                        "messages": [{"id": "p1", "session_id": "parent", "created_ms": 2, "agent": "build",
                                      "model": {"providerID": "p", "id": "m"}}]},
            "attempts": [{
                "task_id": "T1", "attempt": 1, "session_id": "child", "started_ms": 2,
                "ended_ms": 3, "model": "p/m", "reserved_yen": 1, "spent_yen": 2,
                "outcome": "accepted",
                "messages": [{"id": "c1", "session_id": "child", "parent_session_id": "parent",
                              "created_ms": 2, "agent": "explore",
                              "model": {"providerID": "p", "id": "m"}}],
            }],
        }
        state = evaluate_run(
            doc, run_id="r", plan_sha256="h", tasks=[task], executors={"T1": "agent"},
            resolutions={"T1": resolution}, primary_model="p/m",
        )
        self.assertEqual(state.accepted, {"T1"})
        self.assertEqual(state.spent_yen, Decimal("3"))
        self.assertEqual(state.reserved_yen, Decimal(0))

    def test_unknown_primary_cost_is_not_zero(self):
        doc = {"run_id": "r", "plan_sha256": "h", "started_ms": 1, "as_of_ms": 1,
               "budget_error": False,
               "primary": {"session_id": "p", "spent_yen": 0, "outcome": "running",
                           "reserved_yen": None, "messages": []}, "attempts": []}
        state = evaluate_run(doc, run_id="r", plan_sha256="h", tasks=[PlannedTask("T", "explore")],
                             executors={}, resolutions={}, primary_model=None)
        self.assertIsNone(state.spent_yen)
        self.assertTrue(state.report["unknown_cost"])

    def test_digest_mismatch_rejected(self):
        doc = {"run_id": "r", "plan_sha256": "wrong"}
        with self.assertRaises(ValueError):
            evaluate_run(doc, run_id="r", plan_sha256="h", tasks=[],
                         executors={}, resolutions={}, primary_model=None)

    def test_attempt_message_must_be_inside_attempt_and_binding(self):
        task = PlannedTask("T1", "explore")
        resolution = ModelResolution(
            "T1", "agent", "explore", ModelRef("p", "m"), "explore", "default"
        )
        doc = {
            "run_id": "r", "plan_sha256": "h", "started_ms": 1, "as_of_ms": 5,
            "primary": {"session_id": "parent", "spent_yen": 0, "outcome": "accepted",
                        "success": True, "ended_ms": 5,
                        "messages": [{"id": "p", "session_id": "parent", "created_ms": 1,
                                      "agent": "build", "model": {"providerID": "p", "id": "m"}}]},
            "attempts": [{"task_id": "T1", "attempt": 1, "session_id": "child",
                           "started_ms": 2, "ended_ms": 4, "model": "p/m", "outcome": "accepted",
                           "spent_yen": 1, "messages": [{
                               "id": "c", "session_id": "child", "parent_session_id": "parent",
                               "created_ms": 1, "agent": "explore",
                               "model": {"providerID": "p", "id": "m"}}],
                         }],
        }
        with self.assertRaises(ValueError):
            evaluate_run(doc, run_id="r", plan_sha256="h", tasks=[task],
                         executors={"T1": "agent"}, resolutions={"T1": resolution},
                         primary_model="p/m")

    def test_execution_failure_retry_preserves_cost_and_history(self):
        task = PlannedTask("T", "explore")
        resolution = ModelResolution(
            "T", "agent", "explore", ModelRef("p", "m"), "explore", "default"
        )

        def attempt(number, session, started, ended, cost):
            return {
                "task_id": "T", "attempt": number, "session_id": session,
                "started_ms": started, "ended_ms": ended, "model": "p/m",
                "reserved_yen": 0, "spent_yen": cost,
                "outcome": "failed", "failure_reason": "execution_failure",
                "messages": [{
                    "id": f"c{number}", "session_id": session,
                    "parent_session_id": "parent", "created_ms": started,
                    "agent": "explore", "model": {"providerID": "p", "id": "m"},
                }],
            }

        def document(attempts, budget_error=False):
            return {
                "run_id": "r", "plan_sha256": "h", "started_ms": 1, "as_of_ms": 8,
                "budget_error": budget_error,
                "primary": {
                    "session_id": "parent", "spent_yen": 3, "outcome": "accepted",
                    "success": True, "ended_ms": 8,
                    "messages": [{
                        "id": "p1", "session_id": "parent", "created_ms": 2,
                        "agent": "build", "model": {"providerID": "p", "id": "m"},
                    }],
                },
                "attempts": attempts,
            }

        first = attempt(1, "child1", 2, 3, 1)
        state = evaluate_run(
            document([first]), run_id="r", plan_sha256="h", tasks=[task],
            executors={"T": "agent"}, resolutions={"T": resolution},
            primary_model="p/m", retry_limits={"T": 1},
        )
        self.assertEqual(state.retryable, {"T"})
        self.assertEqual(state.spent_yen, Decimal("4"))
        self.assertEqual(len(state.report["tasks"]), 1)

        retry = attempt(2, "child2", 4, 5, 2)
        state = evaluate_run(
            document([first, retry]), run_id="r", plan_sha256="h", tasks=[task],
            executors={"T": "agent"}, resolutions={"T": resolution},
            primary_model="p/m", retry_limits={"T": 1},
        )
        self.assertEqual(state.spent_yen, Decimal("6"))
        self.assertEqual([row["attempt"] for row in state.report["tasks"]], [1, 2])
        self.assertEqual([row["cost_yen"] for row in state.report["tasks"]], ["1", "2"])
        self.assertEqual(state.retryable, set())

        stopped = evaluate_run(
            document([first, retry], budget_error=True), run_id="r", plan_sha256="h",
            tasks=[task], executors={"T": "agent"}, resolutions={"T": resolution},
            primary_model="p/m", retry_limits={"T": 1},
        )
        self.assertEqual(stopped.report["tasks"], state.report["tasks"])
        self.assertEqual(stopped.retryable, set())
        with self.assertRaises(ValueError):
            evaluate_run(
                document([first, retry]), run_id="r", plan_sha256="h", tasks=[task],
                executors={"T": "agent"}, resolutions={"T": resolution},
                primary_model="p/m",
            )

    def test_summarize_report_normal(self):
        report = {
            "walltime_ms": 154000,
            "primary": {"outcome": "accepted"},
            "costs": {
                "total_spent_yen": "3.42",
                "primary_spent_yen": "1.20",
                "children_spent_yen": "2.22",
                "reserved_yen": "1.58"
            },
            "budget": {"limit_yen": "5.00"},
            "unknown_cost": False,
            "unknown_reason": None,
            "unknown_spent_yen": False,
            "unknown_reserved_yen": False,
        }
        summary = summarize_report(report)
        self.assertEqual(summary["walltime_text"], "2分34秒")
        self.assertEqual(summary["outcome"], "accepted")
        self.assertEqual(summary["cost"]["total"], "3.42")
        self.assertEqual(summary["cost"]["primary"], "1.20")
        self.assertEqual(summary["cost"]["children"], "2.22")
        self.assertEqual(summary["cost"]["state"], "estimated")
        self.assertEqual(summary["budget"]["remaining_yen"], "0.00")
        self.assertEqual(summary["budget"]["over"], False)

    def test_summarize_report_unknown_cost(self):
        report = {
            "walltime_ms": 1000,
            "primary": {"outcome": "failed"},
            "costs": {
                "total_spent_yen": None,
                "primary_spent_yen": "1.00",
                "children_spent_yen": None,
                "reserved_yen": None
            },
            "budget": {"limit_yen": "10.00"},
            "unknown_cost": True,
            "unknown_reason": "primary model unavailable",
            "unknown_spent_yen": True,
            "unknown_reserved_yen": True,
        }
        summary = summarize_report(report)
        self.assertEqual(summary["cost"]["total"], "unknown")
        self.assertEqual(summary["cost"]["reason"], "primary model unavailable")
        self.assertEqual(summary["cost"]["primary"], "1.00")
        self.assertEqual(summary["cost"]["children"], "unknown")
        self.assertEqual(summary["budget"]["remaining_yen"], "unknown")

    def test_summarize_report_no_limit(self):
        report = {
            "walltime_ms": 1000,
            "primary": {"outcome": "accepted"},
            "costs": {
                "total_spent_yen": "1.00",
                "primary_spent_yen": "1.00",
                "children_spent_yen": "0.00",
                "reserved_yen": "0.00"
            },
            "budget": {},  # No limit_yen
            "unknown_cost": False,
            "unknown_reason": None,
            "unknown_spent_yen": False,
            "unknown_reserved_yen": False,
        }
        summary = summarize_report(report)
        self.assertIsNone(summary["budget"])

    def test_summarize_report_walltime_formats(self):
        def get_summary(ms):
            return summarize_report({
                "walltime_ms": ms,
                "primary": {"outcome": "accepted"},
                "costs": {"total_spent_yen": "0", "primary_spent_yen": "0", "children_spent_yen": "0", "reserved_yen": "0"},
                "unknown_cost": False, "unknown_reason": None, "unknown_spent_yen": False, "unknown_reserved_yen": False,
                "budget": {"limit_yen": "10"}
            })
        self.assertEqual(get_summary(1000)["walltime_text"], "1秒")
        self.assertEqual(get_summary(61000)["walltime_text"], "1分1秒")
        self.assertEqual(get_summary(3661000)["walltime_text"], "1時間1分1秒")


if __name__ == "__main__":
    unittest.main()
