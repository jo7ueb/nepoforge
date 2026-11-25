"""タスク計画の検証と ready 判定 (#61)。

`/nfgo` の「タスク計画の管理」が定める規則を、Coordinator の手作業ではなく
純粋関数として検証する。課金を伴う実行はせず、計画の受理可否だけを判定する。

扱う規則 (設計メモ 0005 §4.4・nfgo「タスク計画の管理」):

- depends_on の循環・自己参照・不明な task_id を拒否する
- 調査専任の役割 (expert / explore / reviewer) に書き込み範囲を許さない
- write_scope の競合 (glob の包含関係を含む) を検出する
- 並列上限・受理状態から ready なタスクを決める

ここでは permissions や送信先の検証は行わない。TaskSpec の入力段階で
拒否済みであることが前提で、本モジュールは計画の構造だけを扱う。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


class PlanError(ValueError):
    """計画が規則に違反している (起動前に拒否する)。"""


# 書き込みを許さない役割 (#17 / #16)
READ_ONLY_ROLES = frozenset({"expert", "explore", "reviewer"})

# 既定の並列上限 (設計メモ 0005 §4.4 規則 1)
DEFAULT_MAX_PARALLEL = 10


@dataclass(frozen=True)
class PlannedTask:
    """計画上の 1 タスク。TaskSpec のうち計画判定に必要な部分だけを持つ。"""

    task_id: str
    role: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    write_scope: tuple[str, ...] = field(default_factory=tuple)


def _scope_patterns(scope: str) -> tuple[str, ...]:
    """scope の表記を比較用のパターン列にする。

    `docs/**` と `docs/design/x.md` のような包含関係を見落とさないため、
    ディレクトリ表記 (`docs/`) は再帰 glob (`docs/**`) として扱う。
    """
    text = scope.strip()
    if not text:
        return ()
    if text.endswith("/"):
        return (text + "**",)
    return (text,)


def _matches(a: str, b: str) -> bool:
    """2 つの scope が同じファイルに触れ得るかを判定する。

    どちらかが他方を包含する場合 (glob の包含関係) も競合とみなす。
    """
    # 直接の一致 (同じパターン、または一方が他方にマッチ)。
    # glob の `*` はパス区切りをまたがない (fnmatch はまたぐため使わない)。
    if a == b or _glob_match(a, b) or _glob_match(b, a):
        return True
    # 一方が他方のパス接頭辞 (祖先ディレクトリ) なら配下に触れ得る
    if a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/"):
        return True
    # ディレクトリ配下の包含: `x/**` は x 配下のすべてを包含する。
    # `x/*` は直下だけなので、`x/**` のような再帰拡張はしない。
    for pat, other in ((a, b), (b, a)):
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if other == prefix or other.startswith(prefix + "/"):
                return True
            if _glob_match(other, prefix + "/**"):
                return True
    # 共通の祖先を持つ場合 (docs/a/** と docs/**/b.md など) は保守的に競合とみなす。
    # また、一方が他方の祖先ディレクトリである場合 (`a/b` と `a/b/c`) は、
    # 祖先側は配下のファイルに触れ得るため競合とする。
    a_prefix = a.split("/", 1)[0]
    b_prefix = b.split("/", 1)[0]
    if a_prefix == b_prefix and ("/" in a) and ("/" in b):
        return _overlap_roughly(a, b)
    return False


def _glob_match(path: str, pattern: str) -> bool:
    """パス区切りをまたがない glob 一致 (path が pattern にマッチするか)。

    `**` は任意の階層にマッチする。`*` / `?` / `[...]` は 1 階層内だけ。
    """
    p_parts = path.split("/")
    g_parts = pattern.split("/")

    def rec(i: int, j: int) -> bool:
        if j == len(g_parts):
            return i == len(p_parts)
        token = g_parts[j]
        if token == "**":
            # 0 階層以上を吸収する
            for k in range(i, len(p_parts) + 1):
                if rec(k, j + 1):
                    return True
            return False
        if i >= len(p_parts):
            return False
        if not fnmatch.fnmatchcase(p_parts[i], token):
            return False
        return rec(i + 1, j + 1)

    return rec(0, 0)


def _overlap_roughly(a: str, b: str) -> bool:
    """glob を含む 2 パターンが共有し得るパスがあるかを保守的に判定する。

    誤って「競合なし」と言うより、誤って「競合あり」と言う方が安全なので、
    判定できない場合は競合として扱う。
    """
    a_parts = a.split("/")
    b_parts = b.split("/")
    for x, y in zip(a_parts, b_parts):
        if x == y:
            continue
        if fnmatch.fnmatchcase(x, y) or fnmatch.fnmatchcase(y, x):
            continue
        # 同じ階層で両方 glob でなければ、この位置で分岐する (兄弟)。
        # 例: `x/*.md` と `x/sub/y.md` は `*.md` が `sub` にマッチしない。
        x_glob = any(ch in x for ch in "*?[")
        y_glob = any(ch in y for ch in "*?[")
        if not x_glob and not y_glob:
            return False
        if x_glob and y_glob:
            # 両方が glob の場合は一致し得る (例: `*.md` と `0*.md`)
            return True
        # 片方だけ glob。glob が「より深い層まで吸収できる」場合だけ競合と
        # みなす。`*.md` は `sub` にはマッチしないので兄弟 (競合しない)。
        i, j = a_parts.index(x), b_parts.index(y)
        if x_glob:
            rest = "/".join(b_parts[j:])
            return _glob_match(rest, x if i == len(a_parts) - 1 else x)
        rest = "/".join(a_parts[i:])
        return _glob_match(rest, y)
    # 共通接頭辞が尽きた。階層数が同じなら同じパスを指し得る。
    if len(a_parts) != len(b_parts):
        shorter = a_parts if len(a_parts) < len(b_parts) else b_parts
        if not any(any(ch in p for ch in "*?[") for p in shorter):
            return False
    return True


def scopes_conflict(a: str, b: str) -> bool:
    """2 つの write_scope が競合するか。"""
    for pa in _scope_patterns(a):
        for pb in _scope_patterns(b):
            if _matches(pa, pb):
                return True
    return False


def validate_plan(tasks: list[PlannedTask]) -> None:
    """計画全体を検証し、違反があれば PlanError を送出する。

    検証は起動前に行うため、1 件でも違反があれば全体を拒否する。
    """
    if not tasks:
        raise PlanError("plan is empty")

    ids = [t.task_id for t in tasks]
    seen: set[str] = set()
    for task_id in ids:
        if not task_id:
            raise PlanError("task_id must not be empty")
        if task_id in seen:
            raise PlanError(f"duplicate task_id: {task_id}")
        seen.add(task_id)

    for task in tasks:
        if task.role in READ_ONLY_ROLES and task.write_scope:
            raise PlanError(
                f"{task.task_id}: role {task.role} must not declare write_scope"
            )
        for dep in task.depends_on:
            if dep == task.task_id:
                raise PlanError(f"{task.task_id}: depends_on itself")
            if dep not in seen:
                raise PlanError(f"{task.task_id}: unknown depends_on {dep}")

    _check_cycles(tasks)


def _check_cycles(tasks: list[PlannedTask]) -> None:
    graph = {t.task_id: list(t.depends_on) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in graph}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for nxt in graph[node]:
            if color[nxt] == GREY:
                cycle = stack[stack.index(nxt):] + [nxt]
                raise PlanError("depends_on cycle: " + " -> ".join(cycle))
            if color[nxt] == WHITE:
                visit(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for tid in graph:
        if color[tid] == WHITE:
            visit(tid, [])


def conflicts(a: PlannedTask, b: PlannedTask) -> bool:
    """2 タスクの write_scope が競合するか。"""
    for sa in a.write_scope:
        for sb in b.write_scope:
            if scopes_conflict(sa, sb):
                return True
    return False


def ready_tasks(
    tasks: list[PlannedTask],
    *,
    accepted: set[str],
    running: set[str],
    failed: set[str] = frozenset(),
    max_parallel: int = DEFAULT_MAX_PARALLEL,
) -> list[str]:
    """起動してよいタスクの task_id を計画順で返す。

    accepted: 受理済みの task_id
    running: 実行中の task_id
    failed: 失敗した task_id (後続は blocked として起動しない)

    並列上限に空きがある場合だけ返す。write_scope が重複する running 中の
    タスクがあれば起動しない。失敗した依存を持つタスクは blocked とみなし、
    起動対象に含めない。
    """
    if max_parallel < 1:
        raise PlanError("max_parallel must be >= 1")

    by_id = {t.task_id: t for t in tasks}
    slots = max_parallel - len(running)
    if slots <= 0:
        return []

    blocked = set(failed)
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task.task_id in blocked:
                continue
            if any(dep in blocked for dep in task.depends_on):
                blocked.add(task.task_id)
                changed = True

    running_tasks = [by_id[tid] for tid in running if tid in by_id]
    result: list[str] = []
    for task in tasks:
        if len(result) >= slots:
            break
        if task.task_id in accepted or task.task_id in running or task.task_id in blocked:
            continue
        if not all(dep in accepted for dep in task.depends_on):
            continue
        if any(conflicts(task, other) for other in running_tasks):
            continue
        if any(conflicts(task, by_id[tid]) for tid in result if tid in by_id):
            continue
        result.append(task.task_id)
    return result
