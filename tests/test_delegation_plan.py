import copy
import unittest

from nepoforge.delegation_plan import validate_contract
from nepoforge.planning import PlanError


def contract():
    return {
        "schema_version": 1,
        "issue_ref": "#76",
        "deliverables": ["artifact"],
        "tasks": [{
            "task_id": "T1", "role": "implementer", "executor": "agent",
            "stage": "implementation", "issue_ref": "#76", "objective": "Implement",
            "inputs": ["design"], "expected_output": "change", "acceptance_criteria": ["tests pass"],
            "read_scope": ["docs/**"], "write_scope": ["nepoforge/file.py"],
            "depends_on": [], "deliverables": ["artifact"],
        }],
    }


class DelegationPlanTests(unittest.TestCase):
    def test_digest_is_stable_and_covers_plan(self):
        doc = contract()
        digest = validate_contract(doc)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, validate_contract(copy.deepcopy(doc)))
        doc["tasks"][0]["objective"] = "Different"
        self.assertNotEqual(digest, validate_contract(doc))

    def test_primary_requires_exception_and_approval(self):
        doc = contract()
        task = doc["tasks"][0]
        task["executor"] = "primary"
        task["exception"] = {"reason": "human_approved", "detail": "approved", "scope": task["write_scope"]}
        with self.assertRaises(PlanError):
            validate_contract(doc)
        self.assertTrue(validate_contract(doc, approved_exceptions={"T1"}))

    def test_rejects_unassigned_or_duplicate_deliverables(self):
        doc = contract()
        doc["tasks"][0]["deliverables"] = []
        with self.assertRaises(PlanError):
            validate_contract(doc)

    def test_rejects_invalid_dependency_and_tool_edit(self):
        doc = contract()
        doc["tasks"][0]["depends_on"] = ["missing"]
        with self.assertRaises(PlanError):
            validate_contract(doc)
        doc = contract()
        doc["tasks"][0].update(executor="tool", task_kind="diagnostics")
        with self.assertRaises(PlanError):
            validate_contract(doc)
        doc["tasks"][0].update(task_kind="git")
        with self.assertRaises(PlanError):
            validate_contract(doc)

    def test_accepts_resolver_attributes_but_rejects_invalid_types(self):
        doc = contract()
        doc["tasks"][0].update(
            capabilities=["text"], complexity="low", risk="low", concern="tests",
            context_tokens=100, responsibility="leaf", data_policy="default",
            domestic_only=False, max_input_tokens=1000, max_output_tokens=100,
            retries=1,
        )
        self.assertTrue(validate_contract(doc))
        doc["tasks"][0]["context_tokens"] = "100"
        with self.assertRaises(PlanError):
            validate_contract(doc)

    def test_rejects_untrusted_model_request(self):
        doc = contract()
        doc["tasks"][0]["model_request"] = "vendor/model"
        with self.assertRaises(PlanError):
            validate_contract(doc)


if __name__ == "__main__":
    unittest.main()
