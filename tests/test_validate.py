"""同梱データの整合性検証と不正設定の検出 (#11)。

CI の回帰として、`nepoforge validate` が現行の配布データを受理することと、
合成した不正設定をそれぞれ検出して失敗することを確かめる (課金なし)。
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from nepoforge import cli, store, validate


def problems_for(mutate) -> list[str]:
    """同梱データを一時ディレクトリへ複製し、mutate(ルート) の改変後の問題一覧を返す。

    パッケージの正本には書き込まない。
    """
    with tempfile.TemporaryDirectory(prefix="nepoforge-validate-") as tmp:
        root = Path(tmp) / "data"
        shutil.copytree(store.DATA_DIR, root)
        mutate(root)
        data_dir = mock.patch.multiple(
            store, DATA_DIR=root, TEMPLATE_DIR=root / "templates"
        )
        with data_dir:
            return [validate.format_problem(p) for p in validate.collect_problems()]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"test setup: {old!r} not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


class TestBundledDataValidation(unittest.TestCase):
    def test_current_data_is_consistent(self) -> None:
        self.assertEqual(validate.collect_problems(), [])

    def test_cli_reports_success(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["validate"]), 0)
        self.assertIn("consistent", out.getvalue())

    def test_cli_json_output(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["validate", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"valid": True, "problems": []})

    def test_frontmatter_parser(self) -> None:
        data = validate.parse_block_scalars(
            [
                "description: 例",
                "mode: subagent",
                "model: fastmetal/gemma-4-26b-a4b-it",
                "permissions:",
                "  - action: shell",
                '    resource: "git push *"',
                "    effect: deny",
            ]
        )
        self.assertEqual(data["mode"], "subagent")
        self.assertEqual(data["permissions"], [{"action": "shell", "resource": "git push *", "effect": "deny"}])


class TestInvalidSettingsAreDetected(unittest.TestCase):
    """不正設定の代表例ごとに、CI (validate) が失敗することを確かめる。"""

    def assert_detected(self, mutate, keyword: str) -> None:
        found = problems_for(mutate)
        self.assertTrue(found, "the invalid setting was not detected")
        self.assertTrue(any(keyword in p for p in found), f"{keyword!r} not in {found}")

    def test_broken_frontmatter(self) -> None:
        def mutate(root: Path) -> None:
            edit(root / "agents/implementer.md", "mode: subagent", "mode: subagent\nnot-a-pair")

        # 不正な行は ValueError → 問題として報告される
        self.assert_detected(mutate, "frontmatter")

    def test_missing_frontmatter(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "agents/reviewer.md"
            path.write_text("本文だけ\n", encoding="utf-8")

        self.assert_detected(mutate, "frontmatter")

    def test_invalid_model_notation(self) -> None:
        self.assert_detected(
            lambda root: edit(root / "agents/implementer.md", "model: fastmetal/gemma-4-26b-a4b-it", "model: gemma-4"),
            "invalid model notation",
        )

    def test_unknown_frontmatter_key(self) -> None:
        self.assert_detected(
            lambda root: edit(root / "agents/explore.md", "mode: subagent", "mode: subagent\npermisions: []"),
            "unknown frontmatter keys",
        )

    def test_invalid_permission_effect(self) -> None:
        self.assert_detected(
            lambda root: edit(root / "agents/implementer.md", "effect: allow", "effect: permit"),
            "effect is not allowed",
        )

    def test_missing_managed_agent(self) -> None:
        self.assert_detected(lambda root: (root / "agents/expert.md").unlink(), "managed agent is missing")

    def test_missing_managed_runtime_plugin(self) -> None:
        self.assert_detected(
            lambda root: (root / "plugins/runtime-guard.ts").unlink(),
            "managed plugin is missing",
        )

    def test_missing_runtime_plugin_dependency(self) -> None:
        self.assert_detected(
            lambda root: (root / "plugins/runtime-guard-core.mjs").unlink(),
            "managed plugin is missing",
        )

    def test_profile_with_unknown_role(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "profiles/balanced.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["roles"]["planner"] = data["roles"]["architect"]
            path.write_text(json.dumps(data), encoding="utf-8")

        self.assert_detected(mutate, "unknown roles")

    def test_profile_role_model_not_in_candidates(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "profiles/economy.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["roles"]["reviewer"] = "fastmetal/not-a-candidate"
            path.write_text(json.dumps(data), encoding="utf-8")

        self.assert_detected(mutate, "is not a candidate")

    def test_task_rule_model_not_in_candidates(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "profiles/balanced.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["task_rules"][0]["model"] = "fastmetal/another-model"
            path.write_text(json.dumps(data), encoding="utf-8")

        self.assert_detected(mutate, "not in candidates")

    def test_candidate_without_route(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "profiles/performance.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["candidates"].append({"model": "fastmetal/no-route", "capabilities": []})
            path.write_text(json.dumps(data), encoding="utf-8")

        self.assert_detected(mutate, "no route entry")

    def test_broken_route_entry(self) -> None:
        def mutate(root: Path) -> None:
            path = root / "model-routes.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["fastmetal/gemma-4-26b-a4b-it"]["retention"] = "forever"
            path.write_text(json.dumps(data), encoding="utf-8")

        self.assert_detected(mutate, "retention")

    def test_guardrail_loses_merge_deny(self) -> None:
        self.assert_detected(
            lambda root: edit(root / "templates/guardrail.jsonc", '"gh pr merge*", "effect": "deny"', '"gh pr merge*", "effect": "ask"'),
            "guardrail must deny",
        )

    def test_unbalanced_managed_marker(self) -> None:
        self.assert_detected(
            lambda root: edit(root / "templates/guardrail.jsonc", store.MANAGED_MARKER_END, "// (removed)"),
            "managed markers",
        )

    def test_cli_fails_exit_code_1_on_invalid_data(self) -> None:
        """`nepoforge validate` は不正設定で終了コード 1 (CI のチェックを赤にする)。"""
        with mock.patch.object(validate, "collect_problems", return_value=[validate.problem("agents", "x.md", "broken")]):
            with redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
                code = cli.main(["validate"])
        self.assertEqual(code, 1)
        self.assertIn("[agents] x.md: broken", err.getvalue())


if __name__ == "__main__":
    unittest.main()
