import unittest
from decimal import Decimal

from nepoforge.runtime_guard import MessageUsage, RuntimeGuard


class MockDispatcher:
    def __init__(self):
        self.calls = []

    def interrupt_session(self, session_id: str) -> None:
        self.calls.append(session_id)


def message(message_id="m1", session_id="s1", **kwargs):
    values = dict(
        id=message_id, session_id=session_id, model="fastmetal/gemma-4-26b-a4b-it",
        created_ms=100, input_tokens=1000, output_tokens=100, reported_cost=Decimal(0),
    )
    values.update(kwargs)
    return MessageUsage(**values)


class TestRuntimeGuard(unittest.TestCase):
    def test_usage_cost_zero_is_reestimated_and_duplicate_is_deduplicated(self):
        guard = RuntimeGuard(Decimal("10"))
        row = message()
        guard.update_usage("s1", [row])
        first_spend = guard.spent_yen
        self.assertGreater(first_spend, 0)
        guard.update_usage("s1", [row])
        self.assertEqual(guard.spent_yen, first_spend)
        self.assertEqual(guard.snapshots["s1"].input_tokens, 1000)

    def test_conflicting_duplicate_latches_and_interrupts(self):
        dispatcher = MockDispatcher()
        guard = RuntimeGuard(Decimal("10"), dispatcher=dispatcher)
        guard.register_session("s1")
        guard.update_usage("s1", [message()])
        guard.update_usage("s1", [message(input_tokens=2000)])
        self.assertTrue(guard.is_latched)
        self.assertEqual(dispatcher.calls, ["s1"])
        self.assertFalse(guard.can_launch(Decimal("0"))[0])

    def test_unknown_price_latches(self):
        guard = RuntimeGuard(Decimal("10"))
        guard.update_usage("s1", [message(model="fastmetal/not-priced")])
        self.assertTrue(guard.is_latched)
        self.assertIn("unknown price", guard.latch_reason)

    def test_api_record_normalizes_without_content_and_missing_fields_latch(self):
        guard = RuntimeGuard(Decimal("10"))
        self.assertTrue(guard.observe_api_messages("s1", [{
            "id": "api-message",
            "model": {"providerID": "fastmetal", "id": "gemma-4-26b-a4b-it"},
            "time": {"created": 101},
            "tokens": {"input": 100, "output": 10, "reasoning": 1,
                       "cache": {"read": 2, "write": 0}},
            "cost": 0,
            "content": "must not be retained",
        }]))
        snapshot = guard.snapshots["s1"]
        self.assertEqual(snapshot.input_tokens, 100)
        self.assertFalse(hasattr(snapshot, "content"))

        lost = RuntimeGuard(Decimal("10"))
        self.assertFalse(lost.observe_api_messages("s2", [{"id": "missing-token-fields"}]))
        self.assertTrue(lost.is_latched)
        self.assertFalse(lost.accounting_complete)

    def test_report_mode_does_not_latch_for_missing_budget_and_rejects_bad_token_types(self):
        report_guard = RuntimeGuard(None, enforce_limit=False)
        self.assertTrue(report_guard.observe_api_messages("s", [{
            "id": "m", "model": {"providerID": "fastmetal", "id": "gemma-4-26b-a4b-it"},
            "created_ms": 1, "tokens": {"input": 10, "output": 0},
        }]))
        self.assertFalse(report_guard.is_latched)

        bad_guard = RuntimeGuard(Decimal("10"))
        self.assertFalse(bad_guard.observe_api_messages("s", [{
            "id": "m", "model": {"providerID": "fastmetal", "id": "gemma-4-26b-a4b-it"},
            "created_ms": 1, "tokens": {"input": True, "output": 0},
        }]))
        self.assertFalse(bad_guard.accounting_complete)
        self.assertTrue(bad_guard.is_latched)

        malformed_cost_guard = RuntimeGuard(Decimal("10"))
        self.assertFalse(malformed_cost_guard.observe_api_messages("s", [{
            "id": "m", "model": {"providerID": "fastmetal", "id": "gemma-4-26b-a4b-it"},
            "created_ms": 1, "tokens": {"input": 10, "output": 0}, "cost": "invalid",
        }]))
        self.assertTrue(malformed_cost_guard.is_latched)

    def test_unknown_budget_and_request_estimate_fail_closed(self):
        self.assertFalse(RuntimeGuard(None).can_launch(Decimal("0"))[0])
        self.assertFalse(RuntimeGuard(Decimal("10")).can_launch(None)[0])
        self.assertFalse(RuntimeGuard(Decimal("10")).can_launch(Decimal("NaN"))[0])
        with self.assertRaises(ValueError):
            RuntimeGuard(Decimal("Infinity"))

    def test_next_request_reservation_included_before_send(self):
        guard = RuntimeGuard(Decimal("10"))
        allowed, _ = guard.reserve_request("s1", Decimal("8"))
        self.assertTrue(allowed)
        allowed, reason = guard.can_launch(Decimal("3"))
        self.assertFalse(allowed)
        self.assertIn("budget exceeded", reason)
        self.assertEqual(guard.reserved_yen, Decimal("8"))

    def test_ready_launch_reservations_accumulate_within_batch(self):
        guard = RuntimeGuard(Decimal("10"))
        self.assertTrue(guard.reserve_launch(Decimal("6"))[0])
        allowed, reason = guard.reserve_launch(Decimal("5"))
        self.assertFalse(allowed)
        self.assertIn("budget exceeded", reason)
        self.assertEqual(guard.reserved_yen, Decimal("6"))

    def test_reservation_settlement_requires_matching_amount(self):
        guard = RuntimeGuard(Decimal("10"))
        self.assertTrue(guard.reserve_request("s1", Decimal("8"))[0])
        guard.settle_request("s1", Decimal("9"))
        self.assertTrue(guard.is_latched)
        self.assertIn("settlement mismatch", guard.latch_reason)

    def test_observed_overspend_latches_and_interrupts_all_sessions(self):
        dispatcher = MockDispatcher()
        guard = RuntimeGuard(Decimal("0.001"), dispatcher=dispatcher)
        guard.register_session("parent")
        guard.register_session("child")
        guard.update_usage("child", [message(
            session_id="child", input_tokens=10000, output_tokens=1000,
        )])
        self.assertTrue(guard.is_latched)
        self.assertEqual(dispatcher.calls, ["child", "parent"])

    def test_failed_interrupt_dispatch_does_not_abort_other_requests(self):
        class PartiallyBrokenDispatcher:
            def __init__(self):
                self.calls = []

            def interrupt_session(self, session_id):
                self.calls.append(session_id)
                if session_id == "a":
                    raise OSError("unavailable")

        dispatcher = PartiallyBrokenDispatcher()
        guard = RuntimeGuard(Decimal("0"), dispatcher=dispatcher)
        guard.register_session("a")
        guard.register_session("b")
        guard.stop("budget exceeded")
        self.assertEqual(dispatcher.calls, ["a", "b"])
        self.assertEqual(guard.interrupt_results, {"a": "error", "b": "requested"})

    def test_session_registered_after_latch_is_interrupted_once(self):
        dispatcher = MockDispatcher()
        guard = RuntimeGuard(Decimal("10"), dispatcher=dispatcher)
        guard.register_session("parent")
        guard.stop("provider budget error")
        guard.register_session("child")
        guard.register_session("child")
        self.assertEqual(dispatcher.calls, ["parent", "child"])
        self.assertEqual(guard.interrupt_results["child"], "requested")

    def test_repeated_failure_latches_and_does_not_reset(self):
        guard = RuntimeGuard(Decimal("10"), failure_threshold=3)
        self.assertFalse(guard.record_failure("same-edit-failure"))
        self.assertFalse(guard.record_failure("same-edit-failure"))
        self.assertTrue(guard.record_failure("same-edit-failure"))
        self.assertTrue(guard.is_latched)
        self.assertFalse(guard.can_launch(Decimal("0"))[0])

    def test_session_mismatch_latches(self):
        guard = RuntimeGuard(Decimal("10"))
        guard.update_usage("s1", [message(session_id="different")])
        self.assertTrue(guard.is_latched)
        self.assertIn("session id mismatch", guard.latch_reason)

    def test_direct_usage_update_rejects_non_integer_timestamp_and_tokens(self):
        for values in (
            {"created_ms": "1"},
            {"input_tokens": 1.5},
        ):
            guard = RuntimeGuard(Decimal("10"))
            with self.subTest(values=values):
                guard.update_usage("s1", [message(**values)])
                self.assertTrue(guard.is_latched)
                self.assertFalse(guard.accounting_complete)

    def test_direct_usage_update_rejects_non_finite_estimated_cost(self):
        for amount in (Decimal("NaN"), Decimal("Infinity")):
            guard = RuntimeGuard(Decimal("10"))
            guard.update_usage("s1", [message(estimated_cost_yen=amount)])
            self.assertTrue(guard.is_latched)
            self.assertFalse(guard.accounting_complete)
            self.assertIn("invalid estimated cost", guard.latch_reason or "")


if __name__ == "__main__":
    unittest.main()
