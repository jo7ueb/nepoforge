"""`nepoforge plan` サブコマンドの統合テスト (#49)。

計画検証・モデル解決・ready 判定・予算ゲートを、一時環境で課金なしに検証する。
"""

from __future__ import annotations

import json
import unittest

from nepoforge import cli

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
        # 予算ゲートに必要な既定見積り (200k in / 50k out / 再試行 1) で
        # economy プロファイル (japan-glm-5.3-flash) が許可される上限
        self.limit = 100.0

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
        self.assertEqual(result["resolved"]["T1"], "fastmetal/japan-glm-5.3-flash")

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
        # economy の implementer も japan-glm-5.3-flash に解決される
        self.assertEqual(result["resolved"]["T2"], "fastmetal/japan-glm-5.3-flash")

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
