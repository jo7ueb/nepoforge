"""PR #82 匿名 CSV を使う無課金 runtime guard 回帰テスト。"""

import csv
import unittest
from decimal import Decimal
from pathlib import Path

from nepoforge.budget import PRICES
from nepoforge.runtime_guard import MessageUsage, RuntimeGuard


FIXTURE = Path(__file__).parent / "fixtures" / "runtime-incident" / "messages.csv"


class TestRuntimeIncidentFixture(unittest.TestCase):
    def test_t1_reservation_is_exceeded_at_fifth_message(self):
        rows = []
        with FIXTURE.open(encoding="utf-8", newline="") as stream:
            rows = [
                row for row in csv.DictReader(stream)
                if row["session_alias"] == "child-T1-a1"
            ]

        self.assertEqual(len(rows), 152)
        input_rate, output_rate = PRICES["fastmetal/gemma-4-26b-a4b-it"]
        guard = RuntimeGuard(limit_yen=Decimal("1.3911"))
        cumulative = Decimal(0)

        for row in rows:
            input_tokens = int(row["input"] or 0) + int(row["cache_read"] or 0)
            output_tokens = int(row["output"] or 0) + int(row["reasoning"] or 0)
            message_cost = (
                Decimal(input_tokens) * Decimal(str(input_rate))
                + Decimal(output_tokens) * Decimal(str(output_rate))
            ) / Decimal(1_000_000)
            cumulative += message_cost
            guard.update_usage(
                "child-T1-a1",
                [MessageUsage(
                    id=f"child-T1-a1-{row['message_seq']}",
                    session_id="child-T1-a1",
                    model="fastmetal/gemma-4-26b-a4b-it",
                    created_ms=int(row["created_rel_ms"]),
                    input_tokens=int(row["input"] or 0),
                    output_tokens=int(row["output"] or 0),
                    reasoning_tokens=int(row["reasoning"] or 0),
                    cache_read_tokens=int(row["cache_read"] or 0),
                    cache_write_tokens=int(row["cache_write"] or 0),
                    reported_cost=Decimal(row["cost_recorded"] or 0),
                    estimated_cost_yen=message_cost,
                )],
            )
            if guard.is_latched:
                break

        self.assertEqual(row["message_seq"], "5")
        self.assertEqual(cumulative.quantize(Decimal("0.0001")), Decimal("1.5802"))
        self.assertTrue(guard.is_latched)
        self.assertFalse(guard.can_launch()[0])

    def test_t1_total_cost_and_repeat_failure_threshold(self):
        rows = []
        with FIXTURE.open(encoding="utf-8", newline="") as stream:
            rows = [
                row for row in csv.DictReader(stream)
                if row["session_alias"] == "child-T1-a1"
            ]
        input_rate, output_rate = PRICES["fastmetal/gemma-4-26b-a4b-it"]
        total = Decimal(0)
        for row in rows:
            input_tokens = int(row["input"] or 0) + int(row["cache_read"] or 0)
            output_tokens = int(row["output"] or 0) + int(row["reasoning"] or 0)
            total += (
                Decimal(input_tokens) * Decimal(str(input_rate))
                + Decimal(output_tokens) * Decimal(str(output_rate))
            ) / Decimal(1_000_000)
        self.assertEqual(total.quantize(Decimal("0.0001")), Decimal("148.7266"))

        guard = RuntimeGuard(limit_yen=Decimal("1000"))
        for _ in range(2):
            self.assertFalse(guard.record_failure("edit-oldstring-mismatch"))
        self.assertTrue(guard.record_failure("edit-oldstring-mismatch"))
        self.assertFalse(guard.can_launch()[0])
        # read/tool の異なる動作は同じ fingerprint の失敗状態を解除しない。
        self.assertTrue(guard.is_latched)

    def test_t1_third_oldstring_failure_trips_breaker_despite_intervening_read(self):
        with (FIXTURE.parent / "tools.csv").open(encoding="utf-8", newline="") as stream:
            tools = [
                row for row in csv.DictReader(stream)
                if row["session_alias"] == "child-T1-a1"
                and row["message_seq"] in {"11", "12", "13"}
            ]
        self.assertEqual(len(tools), 3)
        guard = RuntimeGuard(Decimal("1000"))
        for row in tools:
            stopped = guard.record_tool_result(
                "child-T1-a1", row["tool"], status=row["status"], error=row["error"],
            )
        self.assertTrue(stopped)
        self.assertTrue(guard.is_latched)


if __name__ == "__main__":
    unittest.main()
