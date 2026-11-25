"""タスク計画の検証と ready 判定の単体テスト (#61)。

課金を伴う実行はしない。計画の受理可否だけを検証する。
"""

from __future__ import annotations

import unittest

from nepoforge.planning import (
    DEFAULT_MAX_PARALLEL,
    PlanError,
    PlannedTask,
    conflicts,
    normalize_scope,
    ready_tasks,
    scopes_conflict,
    validate_plan,
)


class TestValidatePlan(unittest.TestCase):
    def test_valid_plan(self) -> None:
        validate_plan(
            [
                PlannedTask("T1", "explore"),
                PlannedTask("T2", "implementer", depends_on=("T1",), write_scope=("nepoforge/x.py",)),
            ]
        )

    def test_empty_plan_rejected(self) -> None:
        with self.assertRaises(PlanError):
            validate_plan([])

    def test_duplicate_task_id(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            validate_plan([PlannedTask("T1", "explore"), PlannedTask("T1", "explore")])
        self.assertIn("duplicate", str(ctx.exception))

    def test_empty_task_id(self) -> None:
        with self.assertRaises(PlanError):
            validate_plan([PlannedTask("", "explore")])

    def test_self_dependency(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            validate_plan([PlannedTask("T1", "explore", depends_on=("T1",))])
        self.assertIn("itself", str(ctx.exception))

    def test_unknown_dependency(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            validate_plan([PlannedTask("T1", "explore", depends_on=("T9",))])
        self.assertIn("unknown", str(ctx.exception))

    def test_cycle_detected(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            validate_plan(
                [
                    PlannedTask("T1", "explore", depends_on=("T2",)),
                    PlannedTask("T2", "expert", depends_on=("T1",)),
                ]
            )
        self.assertIn("cycle", str(ctx.exception))

    def test_longer_cycle_detected(self) -> None:
        with self.assertRaisesRegex(PlanError, "cycle"):
            validate_plan(
                [
                    PlannedTask("T1", "explore", depends_on=("T2",)),
                    PlannedTask("T2", "expert", depends_on=("T3",)),
                    PlannedTask("T3", "architect", depends_on=("T1",), write_scope=("docs/",)),
                ]
            )

    def test_diamond_is_not_a_cycle(self) -> None:
        validate_plan(
            [
                PlannedTask("T1", "explore"),
                PlannedTask("T2", "expert", depends_on=("T1",)),
                PlannedTask("T3", "architect", depends_on=("T1",), write_scope=("docs/design/",)),
                PlannedTask("T4", "implementer", depends_on=("T2", "T3"), write_scope=("nepoforge/",)),
            ]
        )

    def test_read_only_role_with_write_scope(self) -> None:
        for role in ("expert", "explore", "reviewer"):
            with self.assertRaises(PlanError) as ctx:
                validate_plan([PlannedTask("T1", role, write_scope=("docs/x.md",))])
            self.assertIn(role, str(ctx.exception))

    def test_unknown_role_rejected_with_or_without_scope(self) -> None:
        for scopes in ((), ("docs/x.md",)):
            with self.subTest(scopes=scopes):
                with self.assertRaisesRegex(PlanError, "T1: unknown role.*implementr"):
                    validate_plan([PlannedTask("T1", "implementr", write_scope=scopes)])

    def test_write_required_role_without_scope_rejected(self) -> None:
        # architect / implementer は write_scope 必須 (0005 §4.1)
        for role in ("architect", "implementer"):
            with self.assertRaises(PlanError) as ctx:
                validate_plan([PlannedTask("T1", role)])
            self.assertIn("must declare write_scope", str(ctx.exception))

    def test_architect_with_root_scope_ok(self) -> None:
        # 計画外の編集をしない architect も、範囲を明示すれば通る
        validate_plan([PlannedTask("T1", "architect", write_scope=("docs/",))])

    def test_empty_write_scope_entry_rejected(self) -> None:
        # 空文字は何とも競合しないため、範囲不明のまま走ってしまう
        with self.assertRaises(PlanError) as ctx:
            validate_plan([PlannedTask("T1", "implementer", write_scope=("",))])
        self.assertIn("empty", str(ctx.exception))

    def test_blank_write_scope_entry_rejected(self) -> None:
        with self.assertRaises(PlanError):
            validate_plan([PlannedTask("T1", "implementer", write_scope=("  ",))])

    def test_anchored_write_scope_rejected_on_any_platform(self) -> None:
        for scope in (
            " /repo/docs/x.md ",
            "/",
            r"\docs\x.md",
            r"C:\repo\docs\x.md",
            "C:/repo/docs/x.md",
            "C:docs/x.md",
            r"\\server\share\docs\x.md",
        ):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(PlanError, "T1:.*repository-relative"):
                    validate_plan([PlannedTask("T1", "implementer", write_scope=(scope,))])

    def test_deep_chain_does_not_recurse(self) -> None:
        # 依存鎖が深くても再帰上限に当たらない
        n = 2000
        plan = [
            PlannedTask(f"T{i}", "explore", depends_on=((f"T{i - 1}",) if i else ()))
            for i in range(n)
        ]
        validate_plan(plan)

    def test_deep_cycle_detected(self) -> None:
        n = 2000
        plan = [PlannedTask(f"T{i}", "explore", depends_on=(f"T{i + 1}",)) for i in range(n)]
        plan.append(PlannedTask(f"T{n}", "explore", depends_on=("T0",)))
        with self.assertRaises(PlanError) as ctx:
            validate_plan(plan)
        self.assertIn("cycle", str(ctx.exception))

    def test_implementer_with_write_scope_ok(self) -> None:
        validate_plan([PlannedTask("T1", "implementer", write_scope=("nepoforge/x.py",))])


class TestScopes(unittest.TestCase):
    def test_same_file(self) -> None:
        self.assertTrue(scopes_conflict("nepoforge/cli.py", "nepoforge/cli.py"))

    def test_different_files(self) -> None:
        self.assertFalse(scopes_conflict("nepoforge/a.py", "nepoforge/b.py"))

    def test_different_trees(self) -> None:
        self.assertFalse(scopes_conflict("tests/test_a.py", "nepoforge/a.py"))

    def test_recursive_glob_contains_file(self) -> None:
        # 並列試験で見落としていた包含関係 (#61 の主目的)
        self.assertTrue(scopes_conflict("docs/**", "docs/design/x.md"))
        self.assertTrue(scopes_conflict("nepoforge/**", "nepoforge/cli.py"))

    def test_recursive_glob_does_not_match_other_tree(self) -> None:
        self.assertFalse(scopes_conflict("docs/**", "nepoforge/cli.py"))

    def test_wildcard_matches_file(self) -> None:
        self.assertTrue(scopes_conflict("nepoforge/*.py", "nepoforge/cli.py"))

    def test_wildcard_does_not_cross_directory(self) -> None:
        # `*` はパス区切りをまたがない
        self.assertFalse(scopes_conflict("docs/design/*.md", "docs/design/sub/x.md"))

    def test_trailing_slash_treated_as_recursive(self) -> None:
        self.assertTrue(scopes_conflict("docs/", "docs/design/0007.md"))
        self.assertTrue(scopes_conflict("docs/", "docs/a/b/c/d.md"))
        # `docs/` は `docs` と同じく配下全体を指す
        self.assertTrue(scopes_conflict("docs/", "docs/x.md"))
        self.assertTrue(scopes_conflict("docs/", "docs*"))

    def test_ancestor_directory_conflicts(self) -> None:
        self.assertTrue(scopes_conflict("a/b", "a/b/c"))

    def test_two_recursive_globs_same_root(self) -> None:
        self.assertTrue(scopes_conflict("docs/a/**", "docs/**/b.md"))

    def test_sibling_recursive_globs(self) -> None:
        self.assertFalse(scopes_conflict("src/a/**", "src/b/**"))

    def test_deep_recursive_glob(self) -> None:
        self.assertTrue(scopes_conflict("src/**/test.py", "src/a/b/test.py"))
        self.assertTrue(scopes_conflict("src/a/**/x.py", "src/a/b/c/x.py"))

    def test_suffix_in_different_depth(self) -> None:
        self.assertTrue(scopes_conflict("src/*/test.py", "src/a/test.py"))
        self.assertFalse(scopes_conflict("src/*/test.py", "src/a/b/test.py"))

    def test_double_star_matches_zero_segments(self) -> None:
        # `**` は 0 階層も吸収する (変異テストで検出されなかった分岐)
        self.assertTrue(scopes_conflict("src/**/test.py", "src/test.py"))

    def test_both_wildcards_overlapping(self) -> None:
        # 両方が glob で、互いの文字列にはマッチしないが同時に成立し得る
        self.assertTrue(scopes_conflict("x/a*", "x/*b"))

    def test_dotdot_normalized(self) -> None:
        # `..` を畳まないと同じファイルへの書き込みを見逃す
        self.assertTrue(scopes_conflict("docs/../nepoforge/", "nepoforge/"))
        self.assertTrue(scopes_conflict("docs/../nepoforge/cli.py", "nepoforge/cli.py"))

    def test_lone_wildcard_segment_covers_remaining_levels(self) -> None:
        # `*` が指し得るディレクトリの配下は競合する
        self.assertTrue(scopes_conflict("src/*", "src/a/b"))
        self.assertTrue(scopes_conflict("src/a/*", "src/a/b/c"))

    def test_single_star_does_not_skip_levels(self) -> None:
        # `*` は 1 階層。深い側は越えられない
        self.assertFalse(scopes_conflict("src/*/test.py", "src/a/b/test.py"))
        self.assertTrue(scopes_conflict("src/*/test.py", "src/a/test.py"))

    def test_match_anything(self) -> None:
        self.assertTrue(scopes_conflict("*", "anything"))

    def test_symmetric(self) -> None:
        pairs = [
            ("docs/**", "docs/design/x.md"),
            ("docs/design/*.md", "docs/design/sub/x.md"),
            ("a/b", "a/b/c"),
            ("src/a/**", "src/b/**"),
        ]
        for a, b in pairs:
            self.assertEqual(scopes_conflict(a, b), scopes_conflict(b, a), f"{a} vs {b}")


    def test_review_regressions_in_both_orders(self) -> None:
        for a, b in (
            ("nepoforge/data", "nepoforge/*/cli.py"),
            ("doc*/x.md", "docs/**"),
            ("doc*/**/x.md", "docs/**"),
            ("do*", "docs/x.md"),
        ):
            for left, right in ((a, b), (b, a)):
                with self.subTest(left=left, right=right):
                    self.assertTrue(scopes_conflict(left, right))

    def test_recursive_globs_can_align_at_different_depths(self) -> None:
        self.assertTrue(scopes_conflict("src/**/a/b", "src/a/**/b"))
        self.assertTrue(scopes_conflict("src/**/a/**/b", "src/x/a/y/b"))
        self.assertFalse(scopes_conflict("src/a/**/b", "src/x/**/b"))

    def test_unresolved_parent_is_conservative(self) -> None:
        self.assertTrue(scopes_conflict("docs/*/../x.md", "docs/x.md"))
        self.assertTrue(scopes_conflict("../../x.md", "docs/x.md"))


class TestNormalizeScope(unittest.TestCase):
    def test_dot_segments_removed(self) -> None:
        self.assertEqual(normalize_scope("./docs//x.md"), "docs/x.md")

    def test_dotdot_folded(self) -> None:
        self.assertEqual(normalize_scope("docs/../nepoforge/"), "nepoforge/")

    def test_backslash_converted(self) -> None:
        self.assertEqual(normalize_scope("docs\\x.md"), "docs/x.md")

    def test_glob_segment_blocks_folding(self) -> None:
        # glob の階層は実体が決まらないので畳まない
        self.assertEqual(normalize_scope("docs/*/../x.md"), "docs/*/../x.md")


class TestConflicts(unittest.TestCase):
    def test_task_level_conflict(self) -> None:
        a = PlannedTask("T1", "implementer", write_scope=("nepoforge/",))
        b = PlannedTask("T2", "implementer", write_scope=("nepoforge/cli.py",))
        self.assertTrue(conflicts(a, b))

    def test_task_level_no_conflict(self) -> None:
        a = PlannedTask("T1", "implementer", write_scope=("nepoforge/",))
        b = PlannedTask("T2", "implementer", write_scope=("tests/",))
        self.assertFalse(conflicts(a, b))

    def test_read_only_tasks_never_conflict(self) -> None:
        a = PlannedTask("T1", "explore")
        b = PlannedTask("T2", "expert")
        self.assertFalse(conflicts(a, b))


class TestReadyTasks(unittest.TestCase):
    def _plan(self) -> list[PlannedTask]:
        return [
            PlannedTask("T1", "explore"),
            PlannedTask("T2", "expert"),
            PlannedTask("T3", "implementer", depends_on=("T1",), write_scope=("nepoforge/",)),
            PlannedTask("T4", "implementer", depends_on=("T2",), write_scope=("tests/",)),
        ]

    def test_roots_are_ready(self) -> None:
        ready = ready_tasks(self._plan(), accepted=set(), running=set())
        self.assertEqual(ready, ["T1", "T2"])

    def test_dependents_wait(self) -> None:
        ready = ready_tasks(self._plan(), accepted={"T1"}, running=set())
        self.assertEqual(ready, ["T2", "T3"])

    def test_all_accepted_returns_nothing(self) -> None:
        ready = ready_tasks(self._plan(), accepted={"T1", "T2", "T3", "T4"}, running=set())
        self.assertEqual(ready, [])

    def test_parallel_limit_respected(self) -> None:
        plan = [PlannedTask(f"T{i}", "explore") for i in range(1, 6)]
        ready = ready_tasks(plan, accepted=set(), running=set(), max_parallel=3)
        self.assertEqual(ready, ["T1", "T2", "T3"])

    def test_parallel_limit_minus_running(self) -> None:
        plan = [PlannedTask(f"T{i}", "explore") for i in range(1, 6)]
        ready = ready_tasks(
            plan, accepted=set(), running={"T1", "T2"}, max_parallel=3
        )
        self.assertEqual(ready, ["T3"])

    def test_no_slot_when_full(self) -> None:
        plan = [PlannedTask(f"T{i}", "explore") for i in range(1, 4)]
        ready = ready_tasks(plan, accepted=set(), running={"T1", "T2", "T3"}, max_parallel=3)
        self.assertEqual(ready, [])

    def test_write_scope_conflict_with_running(self) -> None:
        plan = [
            PlannedTask("T1", "explore"),
            PlannedTask("T2", "implementer", write_scope=("nepoforge/cli.py",)),
            PlannedTask("T3", "implementer", write_scope=("nepoforge/",)),
        ]
        ready = ready_tasks(plan, accepted={"T1"}, running={"T2"})
        self.assertEqual(ready, [])

    def test_write_scope_conflict_within_batch(self) -> None:
        # 同じバッチで同時に返す候補同士も競合させない
        plan = [
            PlannedTask("T1", "implementer", write_scope=("nepoforge/cli.py",)),
            PlannedTask("T2", "implementer", write_scope=("nepoforge/",)),
        ]
        ready = ready_tasks(plan, accepted=set(), running=set())
        self.assertEqual(ready, ["T1"])

    def test_mixed_absolute_and_relative_scopes_cannot_be_scheduled(self) -> None:
        plan = [
            PlannedTask("T1", "implementer", write_scope=("nepoforge/planning.py",)),
            PlannedTask("T2", "implementer", write_scope=("/repo/nepoforge/planning.py",)),
        ]
        for running in (set(), {"T1"}, {"T2"}):
            with self.subTest(running=running):
                with self.assertRaisesRegex(PlanError, "T2:.*repository-relative"):
                    ready_tasks(plan, accepted=set(), running=running)

    def test_unknown_role_cannot_be_scheduled(self) -> None:
        with self.assertRaisesRegex(PlanError, "T1: unknown role"):
            ready_tasks([PlannedTask("T1", "implementr")], accepted=set(), running=set())

    def test_failed_dependency_blocks_descendants(self) -> None:
        plan = [
            PlannedTask("T1", "explore"),
            PlannedTask("T2", "implementer", depends_on=("T1",), write_scope=("a/",)),
            PlannedTask("T3", "implementer", depends_on=("T2",), write_scope=("b/",)),
            PlannedTask("T4", "expert"),
        ]
        ready = ready_tasks(plan, accepted=set(), running=set(), failed={"T1"})
        # T4 は無関係なので止めない
        self.assertEqual(ready, ["T4"])

    def test_invalid_max_parallel(self) -> None:
        with self.assertRaises(PlanError):
            ready_tasks([], accepted=set(), running=set(), max_parallel=0)

    def test_default_parallel_limit(self) -> None:
        plan = [PlannedTask(f"T{i:02d}", "explore") for i in range(12)]
        ready = ready_tasks(plan, accepted=set(), running=set())
        self.assertEqual(len(ready), DEFAULT_MAX_PARALLEL)


if __name__ == "__main__":
    unittest.main()
