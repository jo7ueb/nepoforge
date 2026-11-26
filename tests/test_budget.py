"""予算ゲートの単体テスト (#15・#61)。

API 課金なし。純粋関数の判定と、provider 予算超過時の扱いを検証する。
"""

from __future__ import annotations

import unittest

from nepoforge.budget import (
    PRICE_BASIS,
    BudgetError,
    BudgetState,
    LaunchEstimate,
    estimate_cost_yen,
    decide_launch,
    on_budget_error,
)

LIMIT = 100.0


def _est(model: str = "fastmetal/llama-3.1-8b-instruct", retries: int = 0) -> LaunchEstimate:
    return LaunchEstimate(
        model=model, max_input_tokens=100_000, max_output_tokens=10_000, retries=retries
    )


class TestEstimateCost(unittest.TestCase):
    def test_formula_matches_listing(self) -> None:
        # llama-3.1-8b: 3.36 / 6.72 円 per M → 100k in + 10k out = 0.336 + 0.0672
        self.assertAlmostEqual(estimate_cost_yen(_est()), 0.4032, places=4)

    def test_retries_multiply_the_estimate(self) -> None:
        one = estimate_cost_yen(_est())
        self.assertAlmostEqual(estimate_cost_yen(_est(retries=2)), one * 3, places=6)

    def test_unknown_price_raises(self) -> None:
        with self.assertRaisesRegex(BudgetError, "unknown price"):
            estimate_cost_yen(_est(model="fastmetal/kimi-k3.1-pro"))

    def test_negative_inputs_rejected(self) -> None:
        with self.assertRaises(BudgetError):
            LaunchEstimate(model="fastmetal/beam-1.0", max_input_tokens=-1, max_output_tokens=0)
        with self.assertRaises(BudgetError):
            _est(retries=-1)


class TestDecideLaunch(unittest.TestCase):
    def test_within_budget_allowed(self) -> None:
        state = BudgetState(limit_yen=LIMIT, spent_yen=10.0)
        d = decide_launch(state, _est())
        self.assertTrue(d.allowed)
        self.assertEqual(d.price_basis, PRICE_BASIS)
        self.assertAlmostEqual(d.projected_cost_yen, 10.0 + 0.4032, places=4)

    def test_unset_limit_denied(self) -> None:
        # 未設定を無制限と解釈しない
        d = decide_launch(BudgetState(limit_yen=None), _est())
        self.assertFalse(d.allowed)
        self.assertIn("not set", d.reason)

    def test_unknown_price_denied_even_with_budget(self) -> None:
        state = BudgetState(limit_yen=LIMIT)
        d = decide_launch(state, _est(model="fastmetal/kimi-k3.1-pro"))
        self.assertFalse(d.allowed)
        self.assertIn("unknown price", d.reason)

    def test_spent_plus_estimate_exceeding_limit_denied(self) -> None:
        state = BudgetState(limit_yen=LIMIT, spent_yen=99.9)
        d = decide_launch(state, _est())
        self.assertFalse(d.allowed)
        self.assertIn("insufficient budget", d.reason)
        self.assertAlmostEqual(d.projected_cost_yen, 100.3032, places=4)

    def test_reserved_amount_of_running_tasks_is_counted(self) -> None:
        # 10並列で running 中のタスクの予約額を含めて判定する
        state = BudgetState(limit_yen=LIMIT, spent_yen=0.0, reserved_yen=99.9)
        d = decide_launch(state, _est())
        self.assertFalse(d.allowed)

    def test_exact_limit_boundary_is_allowed(self) -> None:
        state = BudgetState(limit_yen=0.4032)
        self.assertTrue(decide_launch(state, _est()).allowed)
        # 1 円でも超えれば拒否
        self.assertFalse(
            decide_launch(BudgetState(limit_yen=0.4031), _est()).allowed
        )

    def test_provider_budget_error_stops_everything(self) -> None:
        # 予算残高に余裕があっても、超過検知後は新規起動を拒否する
        state = BudgetState(limit_yen=LIMIT)
        d = decide_launch(state, _est(), provider_budget_error=True)
        self.assertFalse(d.allowed)
        self.assertIn("already exceeded", d.reason)

    def test_retry_after_budget_error_is_denied(self) -> None:
        state = BudgetState(limit_yen=LIMIT)
        retry = _est(retries=1)
        d = decide_launch(state, retry, provider_budget_error=True)
        self.assertFalse(d.allowed)
        self.assertIn("auto-retries are stopped", d.reason)


class TestOnBudgetError(unittest.TestCase):
    def test_stop_instructions(self) -> None:
        stop = on_budget_error("Budget has been exceeded!")
        self.assertTrue(stop.stop_new_paid_launches)
        self.assertTrue(stop.stop_auto_retry)
        self.assertTrue(stop.stop_running_if_safe)
        self.assertTrue(stop.charge_free_work_may_continue)
        self.assertIn("human approval", stop.resume_requires)
        self.assertEqual(stop.reason, "Budget has been exceeded!")


if __name__ == "__main__":
    unittest.main()
