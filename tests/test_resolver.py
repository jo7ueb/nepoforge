"""Model Resolver の単体テスト (#52)。"""

from __future__ import annotations

import unittest
import dataclasses

from nepoforge import profiles, resolver
from nepoforge.profiles import ModelRef, Candidate, ProfileConfig, TaskRule, Constraints, ProviderConstraint, Budget
from nepoforge.resolver import TaskInput, resolve
from nepoforge.store import NepoforgeError


def _cfg_with_rules(task_rules=(), candidates=None, roles=None):
    if roles is None:
        roles = {
            "architect": "fastmetal/japan-glm-5.3-flash",
            "expert": "fastmetal/japan-glm-5.3-flash",
            "explore": "fastmetal/japan-glm-5.3-flash",
            "implementer": "fastmetal/beam-1.0",
            "reviewer": "fastmetal/japan-glm-5.3-flash",
        }
    if candidates is None:
        candidates = (
            Candidate(ref=ModelRef("fastmetal", "japan-glm-5.3-flash", None), capabilities=("code", "reasoning"), context_tokens=1000),
            Candidate(ref=ModelRef("fastmetal", "beam-1.0", None), capabilities=("code",), context_tokens=2000),
            Candidate(ref=ModelRef("fastmetal", "kimi-k3.1-pro", None), capabilities=("code", "reasoning"), context_tokens=4000),
        )
    providers = (ProviderConstraint(id="fastmetal", approved=True, destination="d", retention="r"),)
    constraints = Constraints(providers=providers, max_parallel=3, budget=Budget(per_task=None, note="n"))
    return ProfileConfig(name="t", roles=roles, candidates=candidates, task_rules=tuple(task_rules), constraints=constraints)


class TestResolve(unittest.TestCase):
    def test_explicit_trusted_selected(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", model_request=ModelRef("fastmetal", "kimi-k3.1-pro", None), model_request_source="human-config")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "selected")
        self.assertEqual(res.reason, "explicit(human-config)")
        self.assertEqual(res.resolved_model, ModelRef("fastmetal", "kimi-k3.1-pro", None))

    def test_external_unresolved(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", model_request=ModelRef("fastmetal", "kimi-k3.1-pro", None), model_request_source="external")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")
        self.assertIsNone(res.resolved_model)
        self.assertEqual(res.binding, "")

    def test_explicit_not_candidate_unresolved(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", model_request=ModelRef("fastmetal", "nope", None), model_request_source="human-config")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")

    def test_unknown_source_unresolved(self) -> None:
        # 3 値以外の未知の出所も信頼しない (T5a 指摘 9)
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", model_request=ModelRef("fastmetal", "kimi-k3.1-pro", None), model_request_source="issue-body")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")
        self.assertEqual(res.reason, "unresolved: untrusted_source")

    def test_unapproved_provider_filtered(self) -> None:
        # 承認済みでない provider の候補は選ばない (設計 §4.2 step 1a)
        cfg = _cfg_with_rules()
        unapproved = dataclasses.replace(cfg, constraints=Constraints(providers=(ProviderConstraint(id="otherhost", approved=True, destination="d", retention="r"),), max_parallel=3, budget=Budget(per_task=None, note="n")))
        ti = TaskInput(task_id="t1", role="implementer")
        res = resolve(ti, unapproved)
        self.assertEqual(res.status, "unresolved")
        self.assertEqual(res.reason, "unresolved: role_default_ineligible")

    def test_task_rule_first_match(self) -> None:
        rules = (
            TaskRule(when={"role": "implementer", "complexity": "high"}, model="fastmetal/kimi-k3.1-pro"),
            TaskRule(when={"role": "implementer"}, model="fastmetal/beam-1.0"),
        )
        cfg = _cfg_with_rules(task_rules=rules)
        ti = TaskInput(task_id="t1", role="implementer", complexity="high")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "selected")
        self.assertEqual(res.reason, "task_rule[0]")
        self.assertEqual(res.resolved_model, ModelRef("fastmetal", "kimi-k3.1-pro", None))

    def test_task_rule_order_deterministic(self) -> None:
        rules = (
            TaskRule(when={"role": "implementer"}, model="fastmetal/beam-1.0"),
            TaskRule(when={"role": "implementer", "complexity": "high"}, model="fastmetal/kimi-k3.1-pro"),
        )
        cfg = _cfg_with_rules(task_rules=rules)
        ti = TaskInput(task_id="t1", role="implementer", complexity="high")
        res = resolve(ti, cfg)
        self.assertEqual(res.reason, "task_rule[0]")

    def test_ineligible_rule_skipped(self) -> None:
        # first rule model ineligible (context), second eligible
        rules = (
            TaskRule(when={"role": "implementer", "complexity": "high"}, model="fastmetal/beam-1.0"),  # capabilities? ok but context low
            TaskRule(when={"role": "implementer"}, model="fastmetal/beam-1.0"),
        )
        # make first model have low context by using candidate with context_tokens 100? Actually beam has 2000. Use context_tokens task high to filter beam.
        cfg = _cfg_with_rules(task_rules=rules)
        ti = TaskInput(task_id="t1", role="implementer", complexity="high", context_tokens=3000)
        res = resolve(ti, cfg)
        # beam filtered out by context, kimi? no rule for kimi. role default beam also filtered → unresolved? 
        # role default implementer is beam-1.0 (context 2000 < 3000) → unresolved
        self.assertEqual(res.status, "unresolved")

    def test_role_default(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "selected")
        self.assertEqual(res.reason, "role_default")
        self.assertEqual(res.resolved_model, ModelRef("fastmetal", "beam-1.0", None))

    def test_capabilities_filter_null_not_excluded(self) -> None:
        # candidate with capabilities None should not be excluded
        cfg = _cfg_with_rules(candidates=(
            Candidate(ref=ModelRef("fastmetal", "japan-glm-5.3-flash", None), capabilities=None, context_tokens=None),
        ), roles={"implementer": "fastmetal/japan-glm-5.3-flash"})
        ti = TaskInput(task_id="t1", role="implementer", capabilities=("code",))
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "selected")

    def test_context_filter(self) -> None:
        cfg = _cfg_with_rules(candidates=(
            Candidate(ref=ModelRef("fastmetal", "japan-glm-5.3-flash", None), capabilities=("code",), context_tokens=100),
        ), roles={"implementer": "fastmetal/japan-glm-5.3-flash"})
        ti = TaskInput(task_id="t1", role="implementer", context_tokens=200)
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")

    def test_executor_tool(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", executor="tool")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "selected")
        self.assertIsNone(res.resolved_model)
        self.assertEqual(res.binding, "-")

    def test_deterministic(self) -> None:
        cfg = _cfg_with_rules()
        ti = TaskInput(task_id="t1", role="implementer", complexity="high")
        r1 = resolve(ti, cfg)
        r2 = resolve(ti, cfg)
        self.assertEqual(r1, r2)

    def test_unresolved_binding_empty(self) -> None:
        cfg = _cfg_with_rules(candidates=(), roles={"implementer": "fastmetal/japan-glm-5.3-flash"})
        # no candidates → unresolved
        ti = TaskInput(task_id="t1", role="implementer")
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")
        self.assertEqual(res.binding, "")

    def test_binding_derived_vs_role(self) -> None:
        rules = (TaskRule(when={"role": "implementer", "complexity": "high"}, model="fastmetal/kimi-k3.1-pro"),)
        cfg = _cfg_with_rules(task_rules=rules)
        ti = TaskInput(task_id="t1", role="implementer", complexity="high")
        res = resolve(ti, cfg)
        self.assertEqual(res.binding, "nf-implementer-kimi-k3.1-pro")
        ti2 = TaskInput(task_id="t2", role="implementer")
        res2 = resolve(ti2, cfg)
        self.assertEqual(res2.binding, "implementer")

    def test_skipped_recorded(self) -> None:
        rules = (
            TaskRule(when={"role": "implementer"}, model="fastmetal/kimi-k3.1-pro"),
        )
        # make kimi ineligible via context
        cfg = _cfg_with_rules(task_rules=rules, candidates=(
            Candidate(ref=ModelRef("fastmetal", "japan-glm-5.3-flash", None), capabilities=None, context_tokens=None),
            Candidate(ref=ModelRef("fastmetal", "kimi-k3.1-pro", None), capabilities=None, context_tokens=100),
        ), roles={"implementer": "fastmetal/japan-glm-5.3-flash"})
        ti = TaskInput(task_id="t1", role="implementer", context_tokens=200)
        res = resolve(ti, cfg)
        # rule skipped, role default japan-glm has null context → not excluded → selected
        self.assertEqual(res.status, "selected")
        self.assertTrue(any("skipped" in c for c in res.constraints_applied))


if __name__ == "__main__":
    unittest.main()
