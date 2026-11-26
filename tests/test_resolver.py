"""Model Resolver の単体テスト (#52)。"""

from __future__ import annotations

import unittest
import dataclasses

from nepoforge import profiles, resolver
from nepoforge.profiles import ModelRef, Candidate, ProfileConfig, TaskRule, Constraints, ProviderConstraint, Budget
from nepoforge.resolver import TaskInput
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
    providers = (ProviderConstraint(id="fastmetal", approved=True, destination="d", retention="r", endpoint="https://api.fastmetal.ai/v1"),)
    constraints = Constraints(providers=providers, max_parallel=3, budget=Budget(per_task=None, note="n"))
    return ProfileConfig(name="t", roles=roles, candidates=candidates, task_rules=tuple(task_rules), constraints=constraints)


def resolve(task, config):
    return resolver.resolve(task, config, project_policy="open")


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

    def test_tool_with_untrusted_request_unresolved(self) -> None:
        # ツール実行でも信頼できない出所の明示指定は拒否する (監査 W02)
        cfg = _cfg_with_rules()
        ti = TaskInput(
            task_id="t1",
            role="expert",
            executor="tool",
            model_request=ModelRef("fastmetal", "kimi-k3.1-pro", None),
            model_request_source="external",
        )
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")
        self.assertEqual(res.reason, "unresolved: untrusted_source")

    def test_tool_without_request_selects_tool(self) -> None:
        cfg = _cfg_with_rules()
        res = resolve(TaskInput(task_id="t1", role="expert", executor="tool"), cfg)
        self.assertEqual(res.status, "selected")
        self.assertEqual(res.binding, "-")

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

    def test_unknown_capability_does_not_satisfy_requirement(self) -> None:
        cfg = _cfg_with_rules(candidates=(
            Candidate(ref=ModelRef("fastmetal", "japan-glm-5.3-flash", None), capabilities=None, context_tokens=None),
        ), roles={"implementer": "fastmetal/japan-glm-5.3-flash"})
        ti = TaskInput(task_id="t1", role="implementer", capabilities=("code",))
        res = resolve(ti, cfg)
        self.assertEqual(res.status, "unresolved")

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
        # 一致したルールが不適格でも役割既定には戻さない。
        self.assertEqual(res.status, "unresolved")
        self.assertEqual(res.reason, "unresolved: task_rule_ineligible")
        self.assertTrue(any("skipped" in c for c in res.constraints_applied))

    def test_bundled_profiles_route_by_responsibility_kind_risk_and_eligibility(self) -> None:
        gemma = "gemma-4-26b-a4b-it"
        oss20 = "gpt-oss-20b"
        oss120 = "gpt-oss-120b"
        glm = "japan-glm-5.3-flash"
        deepseek = "japan-deepseek-v4.1-flash-1017"
        expected = {
            "economy": (glm, None),
            "balanced": (glm, deepseek),
            "performance": (deepseek, deepseek),
        }
        for name, (integration, strict) in expected.items():
            config = profiles.load_profile_config(name)
            cases = [
                ("explore", {"task_kind": "extraction", "complexity": "low", "risk": "low"}, oss20, 2048),
                ("explore", {"task_kind": "extraction", "complexity": "low", "risk": "low", "context_tokens": 140000}, gemma, 2048),
                ("explore", {"task_kind": "extraction", "complexity": "low", "risk": "high"}, gemma, 12000),
                ("implementer", {"task_kind": "implementation"}, gemma, 12000),
                ("implementer", {"complexity": "high", "risk": "high"}, gemma, 12000),
                ("architect", {"task_kind": "documentation"}, gemma, 12000),
                ("expert", {"complexity": "high"}, gemma, 12000),
                ("reviewer", {"concern": "security", "risk": "high"}, gemma, 12000),
                ("architect", {"responsibility": "integration"}, integration, 32000),
                ("expert", {"responsibility": "integration", "complexity": "high"}, integration, 32000),
                ("expert", {"responsibility": "integration", "complexity": "high", "task_kind": "specialist_analysis"},
                 integration if name == "economy" else oss120, 32000 if name == "economy" else 16000),
                ("reviewer", {"responsibility": "integration", "capabilities": ("structured_output",)}, strict, 32000),
                ("reviewer", {"capabilities": ("structured_output",)}, None, None),
                ("reviewer", {"domestic_only": True}, None, None),
                ("architect", {"responsibility": "integration", "domestic_only": True}, integration, 32000),
                ("architect", {"responsibility": "integration", "data_policy": "confidential", "domestic_only": True}, deepseek, 32000),
                ("explore", {"context_tokens": 262145}, None, None),
            ]
            for role, options, model, output in cases:
                with self.subTest(profile=name, role=role, options=options):
                    result = resolve(TaskInput("T1", role, **options), config)
                    if model is None:
                        self.assertEqual(result.reason, "unresolved: task_rule_ineligible")
                    else:
                        self.assertEqual(str(result.resolved_model), f"fastmetal/{model}")
                        self.assertTrue(result.reason.startswith("task_rule["))
                        self.assertEqual(result.max_output_tokens, output)
                        reasoning = {gemma: "off", oss20: "low"}.get(model, "provider_default")
                        self.assertEqual(result.reasoning, reasoning)

    def test_human_request_cannot_override_matching_rule(self) -> None:
        config = profiles.load_profile_config("balanced")
        result = resolve(TaskInput(
            "T1", "reviewer", complexity="low", risk="low",
            model_request=ModelRef("fastmetal", "japan-deepseek-v4.1-flash-1017"),
            model_request_source="human-config",
        ), config)
        self.assertEqual(result.reason, "unresolved: explicit_conflicts_with_task_rule")


if __name__ == "__main__":
    unittest.main()
