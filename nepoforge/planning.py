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
import re
from dataclasses import dataclass, field

from .store import NepoforgeError


class PlanError(NepoforgeError):
    """計画が規則に違反している (起動前に拒否する)。"""


# 書き込みを許さない役割 (#17 / #16)
READ_ONLY_ROLES = frozenset({"expert", "explore", "reviewer"})

# 書き込み範囲の宣言が必須の役割 (設計メモ 0005 §4.1)
WRITE_REQUIRED_ROLES = frozenset({"architect", "implementer"})
TASK_ROLES = READ_ONLY_ROLES | WRITE_REQUIRED_ROLES

# 既定の並列上限 (設計メモ 0005 §4.4 規則 1)
DEFAULT_MAX_PARALLEL = 10


@dataclass(frozen=True)
class PlannedTask:
    """計画上の 1 タスク。TaskSpec のうち計画判定に必要な部分だけを持つ。"""

    task_id: str
    role: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    write_scope: tuple[str, ...] = field(default_factory=tuple)


def normalize_scope(scope: str) -> str:
    """リポジトリ相対の scope を比較用に正規化する。

    `docs/../nepoforge/` と `nepoforge/` を別物として扱うと、同じファイルへの
    同時書き込みを見逃す。lexical に `.` と `..` を畳む。glob を含む階層は
    畳まない (どの実体を指すか決まらないため、比較時に競合側へ倒す)。
    """
    text = scope.strip().replace("\\", "/")
    if text.startswith("/") or re.match(r"[A-Za-z]:", text):
        # 絶対パス・ドライブ付きパスは実行環境を問わず拒否する。
        raise PlanError("write_scope must be repository-relative")
    trailing_slash = text.endswith("/")
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out and out[-1] != ".." and not any(ch in out[-1] for ch in "*?["):
                out.pop()
            else:
                out.append(part)
            continue
        out.append(part)
    result = "/".join(out)
    if trailing_slash and result:
        return result + "/"
    return result


def _seg_overlap(a: str, b: str) -> bool:
    """1 階層の比較。glob 同士は重なり得るものとして扱う。"""
    a_glob = any(ch in a for ch in "*?[")
    b_glob = any(ch in b for ch in "*?[")
    if a_glob and b_glob:
        return True
    if a_glob:
        return fnmatch.fnmatchcase(b, a)
    if b_glob:
        return fnmatch.fnmatchcase(a, b)
    return a == b


def scopes_conflict(a: str, b: str) -> bool:
    """2 つの write_scope が競合し得るか (論理パス、大文字小文字を区別)。

    一致したパスはディレクトリかもしれないため、配下にも競合する。
    `**` は 0 階層以上、それ以外の glob は 1 階層だけに一致する。
    """
    left = normalize_scope(a).rstrip("/").split("/")
    right = normalize_scope(b).rstrip("/").split("/")
    if not a.strip() or not b.strip():
        return False
    if left == [""] or right == [""] or ".." in left or ".." in right:
        # ルート、または正規化しても範囲が確定しないパス。
        return True

    pending = [(0, 0)]
    seen: set[tuple[int, int]] = set()
    while pending:
        i, j = pending.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(left) or j == len(right):
            return True  # 片方が同じパスまたは祖先まで一致した
        x, y = left[i], right[j]
        if x == "**":
            pending.append((i + 1, j))  # 0 階層
            if y != "**":
                pending.append((i, j + 1))  # 1 階層消費し、** に留まる
        if y == "**":
            pending.append((i, j + 1))
            if x != "**":
                pending.append((i + 1, j))
        if x != "**" and y != "**" and _seg_overlap(x, y):
            pending.append((i + 1, j + 1))
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
        if task.role not in TASK_ROLES:
            raise PlanError(f"{task.task_id}: unknown role {task.role!r}")
        if task.role in READ_ONLY_ROLES and task.write_scope:
            raise PlanError(
                f"{task.task_id}: role {task.role} must not declare write_scope"
            )
        if task.role in WRITE_REQUIRED_ROLES and not task.write_scope:
            raise PlanError(
                f"{task.task_id}: role {task.role} must declare write_scope"
            )
        # 空の scope は何とも競合しないため、範囲不明のまま並列実行される。
        # 黙って通さず拒否する (#61 レビュー指摘)。
        for scope in task.write_scope:
            if not scope.strip():
                raise PlanError(f"{task.task_id}: write_scope must not be empty")
            try:
                normalize_scope(scope)
            except PlanError as exc:
                raise PlanError(f"{task.task_id}: {exc}") from exc
        for dep in task.depends_on:
            if dep == task.task_id:
                raise PlanError(f"{task.task_id}: depends_on itself")
            if dep not in seen:
                raise PlanError(f"{task.task_id}: unknown depends_on {dep}")

    _check_cycles(tasks)


def _check_cycles(tasks: list[PlannedTask]) -> None:
    """依存グラフの循環を検出する。

    深い依存鎖で再帰上限に当たらないよう、明示的なスタックで辿る
    (#61 レビュー指摘)。
    """
    graph = {t.task_id: list(t.depends_on) for t in tasks}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in graph}

    for root in graph:
        if color[root] != WHITE:
            continue
        # (node, 次の依存の index) と、その時点の経路
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        color[root] = GREY
        path.append(root)
        while stack:
            node, index = stack[-1]
            if index < len(graph[node]):
                stack[-1] = (node, index + 1)
                nxt = graph[node][index]
                if color[nxt] == GREY:
                    cycle = path[path.index(nxt):] + [nxt]
                    raise PlanError("depends_on cycle: " + " -> ".join(cycle))
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, 0))
                continue
            stack.pop()
            path.pop()
            color[node] = BLACK


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

    不正な計画は候補選択前に拒否する。並列上限に空きがある場合だけ返す。
    write_scope が重複する running 中のタスクがあれば起動しない。
    失敗した依存を持つタスクは blocked とみなし、起動対象に含めない。
    """
    if max_parallel < 1:
        raise PlanError("max_parallel must be >= 1")
    validate_plan(tasks)

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
