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
        with self.assertRaises(PlanError):
            validate_plan(
                [
                    PlannedTask("T1", "explore", depends_on=("T2",)),
                    PlannedTask("T2", "expert", depends_on=("T3",)),
                    PlannedTask("T3", "architect", depends_on=("T1",)),
                ]
            )

    def test_diamond_is_not_a_cycle(self) -> None:
        validate_plan(
            [
                PlannedTask("T1", "explore"),
                PlannedTask("T2", "expert", depends_on=("T1",)),
                PlannedTask("T3", "architect", depends_on=("T1",)),
                PlannedTask("T4", "implementer", depends_on=("T2", "T3"), write_scope=("nepoforge/",)),
            ]
        )

    def test_read_only_role_with_write_scope(self) -> None:
        for role in ("expert", "explore", "reviewer"):
            with self.assertRaises(PlanError) as ctx:
                validate_plan([PlannedTask("T1", role, write_scope=("docs/x.md",))])
            self.assertIn(role, str(ctx.exception))

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
