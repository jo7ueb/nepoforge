#!/usr/bin/env python3
"""ローカルの OpenCode 実行記録を読み取り専用で集計する費用監査ツール (#12)。

API 課金を伴わない。既存のログ解析だけを行い、実行・再試行・書き込みはしない。
集計対象は、指定したプロジェクト (既定: カレントの Git リポジトリ) に属する
セッション全体、または `--root` で絞り込んだ 1 つのセッション木。

推定費用は参考値。単価は表に明記した価格基準日の掲載値であり、
請求実額・キャッシュ課金の実条件を保証しない (#12 の受入条件)。
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# OpenCode のローカル記録
DEFAULT_DB = Path.home() / ".local/share/opencode/opencode.db"

# 単価表 (円 / 100万 token、税別)。出力には推論トークンを含める。
# 価格基準日: 2026-11-26 (#61 掲載値)。記載のないモデルは "unknown"。
# cacheRead は入力単価で概算している (実条件は未確認)。
PRICES: dict[str, tuple[float, float]] = {
    "fastmetal/japan-deepseek-v4.1-flash-1017": (33.6, 134.4),
    "fastmetal/japan-glm-5.3-flash": (17.64, 58.8),
    "fastmetal/beam-1.0": (67.2, 201.6),
}
PRICE_BASIS = "2026-11-26"

# provider 側の予算超過を示す文字列 (#52 の並列試験で実測)
BUDGET_ERROR_TEXT = "Budget has been exceeded"


def git_root() -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out.stdout.strip())


def open_db_copy(path: Path) -> sqlite3.Connection:
    """原本を触らず、一時ディレクトリのコピーに対して読み取る。"""
    if not path.exists():
        raise SystemExit(f"database not found: {path}")
    tmp = Path(tempfile.mkdtemp(prefix="cost-audit-"))
    dst = tmp / "opencode.db"
    shutil.copy2(path, dst)
    for suffix in ("-wal", "-shm"):
        if (path.parent / (path.name + suffix)).exists():
            shutil.copy2(path.parent / (path.name + suffix), Path(str(dst) + suffix))
    return sqlite3.connect(f"file:{dst}?mode=ro", uri=True)


def resolve_project_id(db: sqlite3.Connection, worktree: Path) -> str:
    rows = db.execute("SELECT id, worktree FROM project").fetchall()
    matches = [pid for pid, wt in rows if wt and Path(wt) == worktree]
    if not matches:
        known = ", ".join(wt or "?" for _, wt in rows)
        raise SystemExit(f"project not found for {worktree}\nknown: {known}")
    return matches[0]


def session_tree(
    db: sqlite3.Connection, project_id: str, root_substring: str | None
) -> tuple[set[str], dict[str, dict]]:
    """対象セッション集合。root_substring があればその木に絞る。"""
    sessions = {
        rid: {"title": title, "parent": parent, "agent": agent, "created": created}
        for rid, title, parent, agent, created in db.execute(
            "SELECT id, title, parent_id, agent, time_created FROM session_v2"
            " WHERE project_id = ?",
            (project_id,),
        )
    }
    children: dict[str, list[str]] = defaultdict(list)
    for sid, s in sessions.items():
        if s["parent"]:
            children[s["parent"]].append(sid)

    if root_substring is None:
        return set(sessions), sessions

    roots = [
        rid
        for rid, s in sessions.items()
        if not s["parent"] and root_substring in (s["title"] or "")
    ]
    if not roots:
        raise SystemExit(f"no root session matching: {root_substring!r}")
    if len(roots) > 1:
        titles = ", ".join(sessions[r]["title"] or "?" for r in roots)
        raise SystemExit(f"ambiguous root ({len(roots)} matches): {titles}")

    closure: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        if cur in closure:
            continue
        closure.add(cur)
        stack.extend(children.get(cur, []))
    return closure, sessions


def audit(db: sqlite3.Connection, project_id: str, root_substring: str | None) -> None:
    target, sessions = session_tree(db, project_id, root_substring)

    per_model: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    budget_hits: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for (sid, data) in db.execute(
        "SELECT session_id, data FROM session_message"
        " WHERE session_id IN (SELECT id FROM session_v2 WHERE project_id = ?)",
        (project_id,),
    ):
        if sid not in target:
            continue
        try:
            d = json.loads(data)
        except json.JSONDecodeError:
            continue
        model = d.get("model") or {}
        key = f"{model.get('providerID', '?')}/{model.get('id', '?')}"
        tokens = d.get("tokens") or {}
        if tokens:
            cache = tokens.get("cache") or {}
            a = per_model[key]
            a["msgs"] += 1
            a["input"] += tokens.get("input", 0) or 0
            a["output"] += tokens.get("output", 0) or 0
            a["reasoning"] += tokens.get("reasoning", 0) or 0
            a["cache_read"] += cache.get("read", 0) or 0
            a["cache_write"] += cache.get("write", 0) or 0
        if d.get("finish") == "error":
            budget_hits[key]["error_finish"] += 1
        if BUDGET_ERROR_TEXT in data:
            budget_hits[key][BUDGET_ERROR_TEXT] += 1

    roots = [s for s in sessions.values() if not s["parent"]]
    scope = "1 session tree" if root_substring is not None else "all sessions"
    print(f"project: {project_id} ({scope}, {len(target)} sessions,"
          f" {sum(1 for s in sessions.values() if not s['parent'])} roots)")

    fmt = lambda n: f"{n:,}"  # noqa: E731
    print(f"\n{'model':46s} {'msgs':>5} {'input':>12} {'out':>9}"
          f" {'reason':>9} {'cacheR':>12} {'est.yen':>10}  errors")
    total = 0.0
    unknown = False
    for key, a in sorted(per_model.items(), key=lambda x: -x[1]["input"]):
        price = PRICES.get(key)
        if price:
            cost = (
                (a["input"] + a["cache_read"]) / 1e6 * price[0]
                + (a["output"] + a["reasoning"]) / 1e6 * price[1]
            )
            total += cost
            cost_str = f"{cost:10.1f}"
        else:
            unknown = True
            cost_str = "   unknown"
        errs = budget_hits.get(key, {})
        err_str = ",".join(f"{v}x{ ('finish=error' if k == 'error_finish' else 'budget-text') }"
                           for k, v in errs.items())
        print(f"{key:46s} {a['msgs']:5d} {fmt(a['input']):>12} {fmt(a['output']):>9}"
              f" {fmt(a['reasoning']):>9} {fmt(a['cache_read']):>12} {cost_str}  {err_str}")

    print(f"\nestimated total (known-price models only): {total:.0f} yen")
    print(f"price basis: {PRICE_BASIS} listing; cacheRead approximated at input rate.")
    print("NOT the billed amount. Actual provider terms for cache pricing are unconfirmed.")
    if unknown:
        print("warning: some models have no listed price; the true total is higher.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"OpenCode database (default: {DEFAULT_DB})")
    parser.add_argument("--project", type=Path, default=None,
                        help="project worktree (default: git root of cwd)")
    parser.add_argument("--root", default=None,
                        help="limit to the session tree whose root title contains this")
    args = parser.parse_args()

    worktree = args.project or git_root()
    if worktree is None:
        raise SystemExit("not inside a git repository; pass --project")

    db = open_db_copy(args.db)
    try:
        project_id = resolve_project_id(db, worktree.resolve())
        audit(db, project_id, args.root)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
