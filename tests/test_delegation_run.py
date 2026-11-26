import unittest
from decimal import Decimal

from nepoforge.delegation_run import evaluate_run
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
                "messages": [{"id": "c1", "session_id": "child", "created_ms": 2, "parent_session_id": "parent",
                              "agent": "explore",
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
                              "model": {"providerID": "p", "id": "m"}}]}],
        }
        with self.assertRaises(ValueError):
            evaluate_run(doc, run_id="r", plan_sha256="h", tasks=[task],
                         executors={"T1": "agent"}, resolutions={"T1": resolution},
                         primary_model="p/m")


if __name__ == "__main__":
    unittest.main()
