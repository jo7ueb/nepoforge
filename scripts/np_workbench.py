#!/usr/bin/env python3
"""Agent Workbench の導入・更新・アンインストールを行う (#9)。

エージェント定義・権限ガードレール・コマンド・プロファイル・スクリプトを、
対象リポジトリの `.opencode/` と `profiles/`・`scripts/` 配下にコピーする。
既存の OpenCode 環境 (~/.config/opencode) には一切書き込まない。

使い方:
    np_workbench.py install [--to DIR] [--dry-run]
    np_workbench.py update  [--to DIR] [--from DIR] [--dry-run] [--force]
    np_workbench.py uninstall [--to DIR] [--dry-run] [--force]
    np_workbench.py status  [--to DIR]

安全性の設計:
    - dry-run で計画と差分を表示し、何も変更しない。
    - 管理対象ファイルの記録を対象リポジトリの
      `.opencode/.agent-workbench.json` (マニフェスト) に置く。
    - 衝突 (既存の非管理ファイル) は対象・原因を表示して停止し、上書きしない。
    - 更新時に利用者が管理対象ファイルを改変していた場合も停止 (--force で
      バックアップを作ってから上書き)。
    - アンインストールはマニフェストに記録された管理対象だけを削除する。
      利用者が改変したファイルは既定で残す (--force でバックアップ後に削除)。
    - 冪等: 導入済みの状態で install を繰り返しても変更しない。

完了条件 (#9):
    - コマンド名衝突 (np プリフィックス) の検出と既存コマンドの保護
    - 既存設定を破壊しない / 冪等 / 更新時の競合検出 / 管理対象だけ削除
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.0"

# 配布対象のファイル (リポジトリルート相対)。順序は表示順でもある。
MANAGED_FILES = (
    ".opencode/agents/architect.md",
    ".opencode/agents/expert.md",
    ".opencode/agents/explore.md",
    ".opencode/agents/implementer.md",
    ".opencode/agents/reviewer.md",
    ".opencode/commands/npgo.md",
    ".opencode/opencode.jsonc",
    "profiles/economy.json",
    "profiles/balanced.json",
    "profiles/performance.json",
    "scripts/switch_profile.py",
    "scripts/np_workbench.py",
)

# .gitignore に管理ブロックとして追記する行 (対象リポジトリ側で必要な除外)。
GITIGNORE_BLOCK_BEGIN = "# >>> agent-workbench (managed) >>>"
GITIGNORE_BLOCK_END = "# <<< agent-workbench (managed) <<<"
GITIGNORE_ENTRIES = (
    "# Agent Workbench (#9): 個人上書きプロファイルと導入時バックアップ",
    "profiles/local.json",
    ".opencode/backup/",
)

MANIFEST_PATH = ".opencode/.agent-workbench.json"
BACKUP_DIRNAME = ".opencode/backup"

# 配布コマンドの名前空間 (#9 共通ルール)。
COMMAND_PREFIX = "np"
COMMANDS_DIR = ".opencode/commands"


class WorkbenchError(Exception):
    """明確なメッセージを持つ停止エラー。"""


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def sha256_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(target: Path) -> dict | None:
    path = target / MANIFEST_PATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"manifest is broken: {path} ({exc})")
    if data.get("tool") != "agent-workbench":
        fail(f"manifest belongs to another tool: {path}")
    return data


def classify(target: Path, manifest: dict | None) -> dict[str, str]:
    """各管理対象ファイルの状態を分類する。

    - missing       : 対象に存在しない
    - unchanged     : マニフェスト記録のハッシュと一致 (導入済みで無改変)
    - modified      : 存在するがマニフェスト記録と一致しない
    - conflict      : 存在するがマニフェストに記録がない (非管理ファイル)
    """
    state: dict[str, str] = {}
    for rel in MANAGED_FILES:
        current = sha256_of(target / rel)
        if current is None:
            state[rel] = "missing"
        elif manifest is None or rel not in manifest.get("files", {}):
            state[rel] = "conflict"
        elif manifest["files"][rel] == current:
            state[rel] = "unchanged"
        else:
            state[rel] = "modified"
    return state


def diff_text(old: Path | None, new: Path) -> str:
    """既存ファイルと配布物の差分 (unified diff) を返す。"""
    new_text = new.read_text(encoding="utf-8").splitlines(keepends=True)
    if old is not None:
        old_text = old.read_text(encoding="utf-8").splitlines(keepends=True)
        label = str(old)
    else:
        old_text = []
        label = "/dev/null"
    return "".join(difflib.unified_diff(old_text, new_text, fromfile=label, tofile=str(new)))


def gitignore_plan(target: Path) -> tuple[str, str | None]:
    """(適用後の .gitignore 全文, 現在の全文) を返す。変更不要なら同一。"""
    path = target / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if GITIGNORE_BLOCK_BEGIN in current:
        return current, current
    needed = [line for line in GITIGNORE_ENTRIES if line.lstrip("#").strip()]
    missing = [line for line in needed if line not in current]
    if not missing:
        return current, current
    block = "\n" + GITIGNORE_BLOCK_BEGIN + "\n" + "\n".join(GITIGNORE_ENTRIES) + "\n" + GITIGNORE_BLOCK_END + "\n"
    text = current if current.endswith("\n") or not current else current + "\n"
    return text + block, current


def write_gitignore(target: Path, content: str) -> None:
    if not content:
        return
    (target / ".gitignore").write_text(content, encoding="utf-8")


def check_command_namespace(target: Path, manifest: dict | None) -> list[str]:
    """np プリフィックスのコマンドとの衝突を検出する。

    検出できるのは対象リポジトリの `.opencode/commands/*.md` のみ。
    OpenCode 標準コマンド・ユーザーグローバル設定・プラグイン由来のコマンドは
    この検出の限界であり、文書化済み (docs/design/0002 §4)。
    """
    warnings: list[str] = []
    managed_commands = {
        rel.removeprefix(COMMANDS_DIR + "/") for rel in MANAGED_FILES if rel.startswith(COMMANDS_DIR + "/")
    }
    commands_dir = target / COMMANDS_DIR
    if commands_dir.is_dir():
        for file in sorted(commands_dir.glob("*.md")):
            if file.name in managed_commands:
                continue
            if file.name.startswith(COMMAND_PREFIX):
                warnings.append(
                    f"np プリフィックスのコマンドが既に存在します: {COMMANDS_DIR}/{file.name} "
                    "(Agent Workbench とは別物です。混同に注意してください)"
                )
    return warnings


def backup_file(target: Path, rel: str) -> Path | None:
    """上書き・削除前に 1 ファイルをバックアップする。"""
    src = target / rel
    if not src.is_file():
        return None
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = target / BACKUP_DIRNAME / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def prune_empty_dirs(target: Path) -> list[str]:
    """アンインストール後に空になった管理ディレクトリを削除する。"""
    pruned = []
    for d in (".opencode/agents", ".opencode/commands", "profiles", ".opencode"):
        path = target / d
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
            pruned.append(d + "/")
        except (FileNotFoundError, NotADirectoryError):
            continue
    return pruned


def report_state(state: dict[str, str]) -> None:
    for rel, kind in state.items():
        print(f"  [{kind:9}] {rel}")


def cmd_install(args: argparse.Namespace) -> int:
    target, source = args.target, args.source
    if target.resolve() == source.resolve():
        fail("target is the agent-workbench repository itself. use --to <dir> for another repository")
    manifest = load_manifest(target)
    state = classify(target, manifest)

    conflicts = [rel for rel, kind in state.items() if kind == "conflict"]
    if conflicts:
        print("conflict: 既存の非管理ファイルを上書きできないため停止しました:", file=sys.stderr)
        for rel in conflicts:
            print(f"  [conflict] {rel} (Agent Workbench の管理外。移動・退避するか、内容を確認してください)", file=sys.stderr)
        raise SystemExit(2)

    if manifest is not None and all(kind == "unchanged" for kind in state.values()):
        source_hashes = {rel: sha256_of(source / rel) for rel in MANAGED_FILES}
        if all(source_hashes[rel] == manifest["files"].get(rel) for rel in MANAGED_FILES):
            print(f"already installed (version {manifest.get('version')}). nothing to do. use 'update' to refresh")
            return 0

    warnings = check_command_namespace(target, manifest)
    for w in warnings:
        print(f"warning: {w}")

    gitignore_new, gitignore_old = gitignore_plan(target)
    plan: list[tuple[str, str]] = []
    for rel, kind in state.items():
        if kind == "missing":
            plan.append(("write", rel))
        elif kind == "unchanged" and sha256_of(target / rel) != sha256_of(source / rel):
            plan.append(("update", rel))
    if gitignore_new != gitignore_old:
        plan.append(("update", ".gitignore"))

    if not plan:
        print("nothing to install")
    for action, rel in plan:
        print(f"  [{action:6}] {rel}")
    if args.dry_run:
        print("dry-run: no changes made")
        for action, rel in plan:
            if action == "update":
                print(f"--- diff: {rel}")
                print(diff_text(target / rel, source / rel), end="")
        return 0

    for action, rel in plan:
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dest)
    if gitignore_new != gitignore_old:
        write_gitignore(target, gitignore_new)

    manifest_data = {
        "tool": "agent-workbench",
        "version": VERSION,
        "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "files": {rel: sha256_of(target / rel) for rel in MANAGED_FILES},
    }
    mpath = target / MANIFEST_PATH
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"installed (version {VERSION}). manifest: {MANIFEST_PATH}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    target, source = args.target, args.source
    manifest = load_manifest(target)
    if manifest is None:
        fail(f"not installed (no manifest: {target / MANIFEST_PATH}). run 'install' first")
    state = classify(target, manifest)

    modified = [rel for rel, kind in state.items() if kind == "modified"]
    if modified and not args.force:
        print("conflict: 管理対象ファイルが利用者によって変更されているため停止しました:", file=sys.stderr)
        for rel in modified:
            print(f"  [modified] {rel} (変更を退避するか、--force でバックアップ後に上書き)", file=sys.stderr)
        raise SystemExit(2)

    warnings = check_command_namespace(target, manifest)
    for w in warnings:
        print(f"warning: {w}")

    gitignore_new, gitignore_old = gitignore_plan(target)
    plan: list[tuple[str, str]] = []
    for rel in MANAGED_FILES:
        src_hash = sha256_of(source / rel)
        cur_hash = sha256_of(target / rel)
        if src_hash is None:
            fail(f"source file is missing: {source / rel}")
        if src_hash != cur_hash:
            plan.append(("update" if cur_hash is not None else "write", rel))
    if gitignore_new != gitignore_old:
        plan.append(("update", ".gitignore"))

    if not plan and not modified:
        print(f"up to date (version {manifest.get('version')} -> {VERSION})")
        return 0

    for action, rel in plan:
        print(f"  [{action:6}] {rel}")
    if args.dry_run:
        print("dry-run: no changes made")
        for action, rel in plan:
            if action == "update":
                print(f"--- diff: {rel}")
                print(diff_text(target / rel, source / rel), end="")
        return 0

    for action, rel in plan:
        if action == "update":
            backup_file(target, rel)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dest)
    if gitignore_new != gitignore_old:
        write_gitignore(target, gitignore_new)

    manifest_data = dict(manifest)
    manifest_data.update(
        {
            "version": VERSION,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "files": {rel: sha256_of(target / rel) for rel in MANAGED_FILES},
        }
    )
    (target / MANIFEST_PATH).write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated (version {manifest.get('version')} -> {VERSION})")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = args.target
    manifest = load_manifest(target)
    if manifest is None:
        fail(f"not installed (no manifest: {target / MANIFEST_PATH})")
    state = classify(target, manifest)

    kept: list[str] = []
    removed: list[str] = []
    for rel, kind in state.items():
        if kind == "missing":
            continue
        if kind == "modified" and not args.force:
            kept.append(rel)
            continue
        if kind == "modified":
            backup = backup_file(target, rel)
            print(f"  [backup  ] {rel} -> {backup.relative_to(target)}")
        (target / rel).unlink()
        removed.append(rel)
    for rel in removed:
        print(f"  [removed ] {rel}")
    for rel in kept:
        print(f"  [kept    ] {rel} (利用者が変更しているため残しました。不要なら手動で削除してください)")

    gitignore_path = target / ".gitignore"
    if gitignore_path.is_file():
        lines = gitignore_path.read_text(encoding="utf-8").splitlines(keepends=True)
        out, inside = [], False
        changed = False
        for line in lines:
            if line.strip() == GITIGNORE_BLOCK_BEGIN:
                inside = True
                changed = True
                continue
            if line.strip() == GITIGNORE_BLOCK_END:
                inside = False
                continue
            if not inside:
                out.append(line)
        if changed:
            text = "".join(out)
            # ブロック追記時に加えた先頭の空行を含め、末尾の空行を掃除する
            text = text.rstrip("\n")
            text = text + "\n" if text else ""
            if args.dry_run:
                print("  [removed ] .gitignore managed block (dry-run)")
            else:
                write_gitignore(target, text)
                print("  [removed ] .gitignore managed block")

    if args.dry_run:
        print("dry-run: no changes made")
        return 0
    if kept and not args.force:
        pass  # マニフェストは残す (利用者が改変した管理ファイルの記録を保持)
    else:
        (target / MANIFEST_PATH).unlink(missing_ok=True)
        pruned = prune_empty_dirs(target)
        for d in pruned:
            print(f"  [pruned  ] {d}")
    print("uninstalled (managed files only)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    target = args.target
    manifest = load_manifest(target)
    if manifest is None:
        print("not installed")
        return 1
    print(f"installed: version {manifest.get('version')} at {manifest.get('installed_at')}")
    state = classify(target, manifest)
    report_state(state)
    source_state = {rel: sha256_of(args.source / rel) for rel in MANAGED_FILES}
    newer = [
        rel
        for rel in MANAGED_FILES
        if state[rel] == "unchanged" and source_state[rel] != manifest["files"].get(rel)
    ]
    if newer:
        print("update available:")
        for rel in newer:
            print(f"  [updatable] {rel}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--to", dest="target", type=Path, default=Path.cwd(), help="target repository (default: cwd)")
        p.add_argument("--from", dest="source", type=Path, default=REPO_ROOT, help="agent-workbench source (default: script's repository)")
        p.add_argument("--dry-run", action="store_true", help="show the plan and diffs without changes")
        p.add_argument("--force", action="store_true", help="overwrite/delete user-modified managed files with backup")

    for name, fn in (("install", cmd_install), ("update", cmd_update), ("uninstall", cmd_uninstall), ("status", cmd_status)):
        p = sub.add_parser(name, help=name + " Agent Workbench files")
        common(p)
        p.set_defaults(fn=fn)

    args = parser.parse_args()
    if not args.source.is_dir():
        fail(f"source repository not found: {args.source}")
    for rel in MANAGED_FILES:
        if not (args.source / rel).is_file():
            fail(f"source file is missing: {args.source / rel}")
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
