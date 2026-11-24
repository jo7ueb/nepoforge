"""nepoforge CLI (#47): ユーザー単位の導入・更新・診断。

コマンド:
    init / update / status / uninstall / doctor / enable / disable / profile

設計: docs/design/0003-package-cli.md
安全方針: #9 (nf_workbench.py) の機構を引き継ぐ — dry-run、衝突時停止、
バックアップ、冪等、管理対象のみ削除。
"""

from __future__ import annotations

import argparse
import json
import platformdirs
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, jsonc, profiles, store
from .store import (
    MANAGED_MARKER_BEGIN,
    NepoforgeError,
    PROJECT_CONFIG_PATH,
    PROJECT_MANIFEST_PATH,
    USER_MANAGED_FILES,
    fail,
)

GUARDRAIL_TEMPLATE = store.TEMPLATE_DIR / "guardrail.jsonc"
PROVIDER_TEMPLATE = store.TEMPLATE_DIR / "provider-fastmetal.jsonc"

STATE = "missing   unchanged  modified   conflict"
FILE_COLS = 12


# ---------------------------------------------------------------------------
# 共通ヘルパー


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def print_state(state: dict[str, str]) -> None:
    for rel, kind in state.items():
        print(f"  [{kind:9}] {rel}")


def warn_nf_command_collision(commands_dir: Path, managed: set[str]) -> list[str]:
    """nf プリフィックスの非管理コマンドとの衝突を検出する (#9 を引き継ぎ)。"""
    warnings: list[str] = []
    if commands_dir.is_dir():
        for file in sorted(commands_dir.glob("*.md")):
            if file.name in managed:
                continue
            if file.name.startswith("nf"):
                warnings.append(
                    f"nf プリフィックスのコマンドが既に存在します: {commands_dir / file.name} "
                    "(Nepoforge の管理対象外です。利用者所有ファイルの可能性があるため確認してください)"
                )
    return warnings


# ---------------------------------------------------------------------------
# ユーザーグローバルへの導入 (init / update の共通部)


def sync_global(profile_name: str | None, force: bool, dry_run: bool) -> int:
    config_dir = store.opencode_dir()
    manifest = store.load_manifest(store.manifest_path())
    if profile_name is None:
        profile_name = manifest.get("profile") if manifest else "balanced"
    prof = profiles.load_profile(profile_name)
    expected = {rel: sha256_text(profiles.render(rel, prof)) for rel in USER_MANAGED_FILES}
    state = store.classify(config_dir, manifest, expected)

    conflicts = [rel for rel, kind in state.items() if kind == "conflict"]
    if conflicts:
        print("conflict: 既存の非管理ファイルを上書きできないため停止しました:", file=sys.stderr)
        for rel in conflicts:
            print(f"  [conflict] {config_dir / rel} (Nepoforge の管理外。移動・退避するか確認してください)", file=sys.stderr)
        raise SystemExit(2)

    for w in warn_nf_command_collision(
        config_dir / "commands", {Path(rel).name for rel in USER_MANAGED_FILES}
    ):
        print(f"warning: {w}")

    plan: list[tuple[str, str]] = []
    for rel, kind in state.items():
        if kind == "missing":
            plan.append(("write", rel))
        elif kind == "modified":
            if not force:
                print("conflict: 管理対象ファイルが利用者によって変更されているため停止しました:", file=sys.stderr)
                for r, k in state.items():
                    if k == "modified":
                        print(f"  [modified] {config_dir / r} (変更を退避するか、--force でバックアップ後に上書き)", file=sys.stderr)
                raise SystemExit(2)
            plan.append(("update", rel))

    if manifest is not None and not plan:
        if manifest.get("version") == store.VERSION and manifest.get("profile") == profile_name:
            print(f"up to date (version {manifest.get('version')}, profile {profile_name}). nothing to do")
            return 0
        print(f"already installed (version {manifest.get('version')}); refreshing manifest only")

    if not plan:
        print("nothing to install")
    for action, rel in plan:
        print(f"  [{action:6}] {config_dir / rel}")
    if dry_run:
        print("dry-run: no changes made")
        for action, rel in plan:
            if action == "update":
                print(f"--- diff: {rel}")
                print(store.diff_text(config_dir / rel, store.DATA_DIR / rel), end="")
        return 0

    for action, rel in plan:
        if action == "update":
            backup = store.backup_file(config_dir, rel)
            if backup:
                print(f"  [backup  ] {rel} -> {backup}")
        dest = config_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(profiles.render(rel, prof), encoding="utf-8")

    store.store_write_manifest(profile_name, expected)
    print(f"installed (version {store.VERSION}, profile {profile_name}). manifest: {store.manifest_path()}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    result = sync_global(args.profile, args.force, args.dry_run)
    if args.provider:
        apply_provider(args.dry_run)
    return result


def cmd_update(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        fail(f"not installed (no manifest: {store.manifest_path()}). run 'nepoforge init' first")
    result = sync_global(manifest.get("profile", "balanced"), args.force, args.dry_run)
    if args.no_projects or args.dry_run:
        return result
    return sync_projects(args.force)


# ---------------------------------------------------------------------------
# 有効化済みプロジェクトの同期


def sync_projects(force: bool) -> int:
    """guardrail テンプレートを有効化済みプロジェクトへ再配布する (#47 §5 update)。"""
    projects = store.load_projects()
    if not projects:
        return 0
    template = GUARDRAIL_TEMPLATE.read_text(encoding="utf-8")
    remaining: list[dict] = []
    stopped = False
    for entry in projects:
        root = Path(entry.get("path", ""))
        if not root.is_dir():
            print(f"  [gone    ] {root} (記録から削除します)")
            continue
        remaining.append(entry)
        config = root / PROJECT_CONFIG_PATH
        pm = store.load_manifest(root / PROJECT_MANIFEST_PATH)
        if pm is None or pm.get("kind") != "project":
            print(f"  [skip    ] {root} (プロジェクト マニフェストがないため触れません)")
            continue
        current = store.sha256_of(config)
        if current is None:
            print(f"  [missing ] {config} (再配布します)")
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(template, encoding="utf-8")
            pm["files"] = {PROJECT_CONFIG_PATH: store.sha256_of(config)}
            store.write_json(root / PROJECT_MANIFEST_PATH, pm)
            print(f"  [updated ] {config}")
            continue
        if pm["files"].get(PROJECT_CONFIG_PATH) == current:
            if current == sha256_text(template):
                print(f"  [ok      ] {root}")
                continue
            # テンプレート更新後の初回同期
            backup = store.backup_file(root, PROJECT_CONFIG_PATH)
            if backup:
                print(f"  [backup  ] {config} -> {backup}")
            config.write_text(template, encoding="utf-8")
            pm["files"] = {PROJECT_CONFIG_PATH: store.sha256_of(config)}
            store.write_json(root / PROJECT_MANIFEST_PATH, pm)
            print(f"  [updated ] {config}")
            continue
        # 利用者が改変している
        if not force:
            print(f"  [modified] {config} (利用者変更があるため停止。--force でバックアップ後に上書き)")
            stopped = True
            continue
        backup = store.backup_file(root, PROJECT_CONFIG_PATH)
        if backup:
            print(f"  [backup  ] {config} -> {backup}")
        config.write_text(template, encoding="utf-8")
        pm["files"] = {PROJECT_CONFIG_PATH: store.sha256_of(config)}
        store.write_json(root / PROJECT_MANIFEST_PATH, pm)
        print(f"  [updated ] {config}")
    store.save_projects(remaining)
    if stopped:
        fail("user-modified guardrail(s) found. resolve them and re-run")
    return 0


# ---------------------------------------------------------------------------
# status / uninstall / doctor


def cmd_status(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        if args.json:
            print(json.dumps({"installed": False}))
        else:
            print("not installed. run 'nepoforge init'")
        return 1
    prof = profiles.load_profile(manifest.get("profile", "balanced"))
    expected = {rel: sha256_text(profiles.render(rel, prof)) for rel in USER_MANAGED_FILES}
    state = store.classify(store.opencode_dir(), manifest, expected)
    projects = store.load_projects()
    if args.json:
        print(
            json.dumps(
                {
                    "installed": True,
                    "version": manifest.get("version"),
                    "profile": manifest.get("profile"),
                    "files": state,
                    "projects": [p.get("path") for p in projects],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"installed: version {manifest.get('version')} at {manifest.get('installed_at')}")
    print(f"profile:   {manifest.get('profile')}")
    print_state(state)
    newer = [rel for rel, kind in state.items() if kind == "unchanged" and expected[rel] != manifest["files"].get(rel)]
    if newer:
        print("update available: run 'nepoforge update'")
        for rel in newer:
            print(f"  [updatable] {rel}")
    if projects:
        print("enabled projects:")
        for p in projects:
            print(f"  {p.get('path')}")
    return 0


def remove_project_installation(root: Path, force: bool, dry_run: bool) -> bool:
    """プロジェクト側の管理対象 (guardrail) を撤去する。改変時は既定で残す。"""
    pm = store.load_manifest(root / PROJECT_MANIFEST_PATH)
    if pm is None or pm.get("kind") != "project":
        print(f"  [skip    ] {root} (Nepoforge のプロジェクト マニフェストがないため触れません)")
        return True
    config = root / PROJECT_CONFIG_PATH
    current = store.sha256_of(config)
    if current is None:
        pass
    elif pm["files"].get(PROJECT_CONFIG_PATH) != current and not force:
        print(f"  [kept    ] {config} (利用者が変更しているため残しました。不要なら手動で削除してください)")
    else:
        if pm["files"].get(PROJECT_CONFIG_PATH) != current:
            backup = store.backup_file(root, PROJECT_CONFIG_PATH)
            if backup:
                print(f"  [backup  ] {config} -> {backup}")
        if dry_run:
            print(f"  [removed ] {config} (dry-run)")
        else:
            config.unlink()
            print(f"  [removed ] {config}")
    if dry_run:
        return True
    (root / PROJECT_MANIFEST_PATH).unlink(missing_ok=True)
    store.forget_project(root)
    return True


def cmd_uninstall(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        fail(f"not installed (no manifest: {store.manifest_path()})")
    config_dir = store.opencode_dir()
    projects = store.load_projects()
    if projects and not args.projects and not args.dry_run:
        print("conflict: 有効化済みプロジェクトがあります。--projects で同時に撤去するか、先に各プロジェクトで 'nepoforge disable' を実行してください:", file=sys.stderr)
        for p in projects:
            print(f"  {p.get('path')}", file=sys.stderr)
        raise SystemExit(2)

    recorded: dict[str, str] = manifest.get("files", {})
    removed: list[str] = []
    for rel in USER_MANAGED_FILES:
        target = config_dir / rel
        current = store.sha256_of(target)
        if current is None:
            continue
        if current != recorded.get(rel) and not args.force:
            print(f"  [kept    ] {rel} (利用者が変更しているため残しました。不要なら手動で削除してください)")
            continue
        if current != recorded.get(rel):
            backup = store.backup_file(config_dir, rel)
            if backup:
                print(f"  [backup  ] {rel} -> {backup}")
        if args.dry_run:
            print(f"  [removed ] {rel} (dry-run)")
        else:
            target.unlink()
            print(f"  [removed ] {rel}")
        removed.append(rel)

    if args.projects:
        for entry in projects:
            root = Path(entry.get("path", ""))
            print(f"project: {root}")
            remove_project_installation(root, args.force, args.dry_run)

    if args.dry_run:
        print("dry-run: no changes made")
        return 0
    if len(removed) == len(USER_MANAGED_FILES):
        store.manifest_path().unlink(missing_ok=True)
        for d in ("agents", "commands"):
            path = config_dir / d
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
                print(f"  [pruned  ] {path}")
            except (FileNotFoundError, NotADirectoryError):
                continue
    print("uninstalled (managed files only)")
    print("CLI 自体の削除はパッケージマネージャーで行ってください (例: uv tool uninstall nepoforge / pip uninstall nepoforge)")
    return 0


def _tool_version(name: str) -> str | None:
    exe = shutil.which(name)
    if exe is None:
        return None
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr) else "unknown"


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    for tool in ("opencode", "git", "gh"):
        version = _tool_version(tool)
        ok = version is not None and version != "unknown"
        checks.append((f"{tool} on PATH", ok, version or "not found"))

    global_config = store.opencode_dir()
    config_file = next(
        (p for p in (global_config / "opencode.jsonc", global_config / "opencode.json") if p.is_file()), None
    )
    if config_file is None:
        checks.append(("global opencode config", False, f"{global_config}/opencode.json(c) not found"))
    else:
        try:
            data = jsonc.loads(config_file.read_text(encoding="utf-8"))
            provider = (data.get("providers") or {}).get("fastmetal")
            checks.append(("global opencode config parses", True, str(config_file)))
            checks.append(("fastmetal provider defined", bool(provider), "providers.fastmetal" if provider else "missing. run 'nepoforge init --provider fastmetal'"))
        except NepoforgeError as exc:
            checks.append(("global opencode config parses", False, str(exc)))
            checks.append(("fastmetal provider defined", False, "config unreadable"))

    has_env_key = bool(_tool_env_key())
    auth_store = platformdirs.user_data_dir("opencode") + "/auth.json"
    checks.append(
        ("fastmetal credential hint", has_env_key or Path(auth_store).is_file(),
         "FASTMETAL_API_KEY is set" if has_env_key else (f"auth store exists: {auth_store}" if Path(auth_store).is_file() else "neither FASTMETAL_API_KEY nor auth store found (models will fail without credentials)")),
    )

    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        checks.append(("nepoforge installed", False, "not installed. run 'nepoforge init'"))
    else:
        prof = profiles.load_profile(manifest.get("profile", "balanced"))
        expected = {rel: sha256_text(profiles.render(rel, prof)) for rel in USER_MANAGED_FILES}
        state = store.classify(store.opencode_dir(), manifest, expected)
        bad = [rel for rel, kind in state.items() if kind in ("missing", "modified", "conflict")]
        checks.append(("nepoforge installed", True, f"version {manifest.get('version')}, profile {manifest.get('profile')}"))
        checks.append(("managed files intact", not bad, ", ".join(f"{r}={state[r]}" for r in bad) or "all unchanged"))

    project_root = store.find_project_root()
    pconf = project_root / PROJECT_CONFIG_PATH
    pm = store.load_manifest(project_root / PROJECT_MANIFEST_PATH)
    if pm is not None and pm.get("kind") == "project":
        current = store.sha256_of(pconf)
        intact = current is not None and current == pm["files"].get(PROJECT_CONFIG_PATH)
        checks.append(("guardrail enabled (this project)", intact, str(pconf) if intact else f"{pconf} is missing or modified"))
    else:
        checks.append(("guardrail enabled (this project)", False, "not enabled here. run 'nepoforge enable' (optional)"))
    for w in warn_nf_command_collision(project_root / ".opencode" / "commands", {"nfgo.md"}):
        checks.append(("no nf* command collision", False, w))

    ok_all = True
    for name, ok, detail in checks:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            ok_all = False
        print(f"  [{mark}] {name}: {detail}")
    if args.json:
        print(json.dumps([{"check": n, "ok": o, "detail": d} for n, o, d in checks], ensure_ascii=False, indent=2))
    return 0 if ok_all else 1


def _tool_env_key() -> str | None:
    import os

    return "set" if os.environ.get("FASTMETAL_API_KEY") else None


# ---------------------------------------------------------------------------
# enable / disable


def load_project_manifest(root: Path) -> dict | None:
    """プロジェクト側マニフェストを読む。旧方式 (#9) なら移行を案内して停止する。"""
    pm = store.load_manifest(root / PROJECT_MANIFEST_PATH)
    if pm is None:
        return None
    if pm.get("kind") != "project":
        fail(
            f"legacy (per-repository) installation detected: {root / PROJECT_MANIFEST_PATH}. "
            "migrate first: run the old uninstaller in this repository "
            "(python3 scripts/nf_workbench.py uninstall or the installed copy), then 'nepoforge enable'. "
            "see docs/design/0003-package-cli.md §8"
        )
    return pm


def cmd_enable(args: argparse.Namespace) -> int:
    root = store.find_project_root()
    config = root / PROJECT_CONFIG_PATH
    pm = load_project_manifest(root)
    template = GUARDRAIL_TEMPLATE.read_text(encoding="utf-8")
    current = store.sha256_of(config)

    if current is not None and (pm is None or MANAGED_MARKER_BEGIN not in config.read_text(encoding="utf-8")):
        print("conflict: 既存の設定ファイルを上書きできないため停止しました:", file=sys.stderr)
        print(f"  {config} (Nepoforge の管理外。プロジェクト固有設定はこのファイルに維持してください)", file=sys.stderr)
        print("  guardrail を足す場合は手動で permissions を統合するか、ファイルを退避してください。", file=sys.stderr)
        raise SystemExit(2)

    if pm is not None and current is not None and pm["files"].get(PROJECT_CONFIG_PATH) != current and not args.force:
        print("conflict: guardrail が利用者によって変更されているため停止しました:", file=sys.stderr)
        print(f"  {config} (変更を退避するか、--force でバックアップ後に上書き)", file=sys.stderr)
        raise SystemExit(2)

    if current == sha256_text(template):
        print(f"already enabled: {config}")
        return 0

    if args.dry_run:
        print(f"[dry-run] would write: {config}")
        print(store.diff_text(config, GUARDRAIL_TEMPLATE), end="")
        print("dry-run: no changes made")
        return 0

    if current is not None:
        backup = store.backup_file(root, PROJECT_CONFIG_PATH)
        if backup:
            print(f"  [backup  ] {config} -> {backup}")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(template, encoding="utf-8")
    store.write_json(
        root / PROJECT_MANIFEST_PATH,
        {
            "tool": "nepoforge",
            "kind": "project",
            "version": store.VERSION,
            "enabled_at": store.now_iso(),
            "files": {PROJECT_CONFIG_PATH: store.sha256_of(config)},
        },
    )
    store.record_project(root)
    print(f"enabled (guardrail): {config}")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    root = store.find_project_root()
    pm = load_project_manifest(root)
    if pm is None:
        print(f"not enabled here (no manifest: {root / PROJECT_MANIFEST_PATH})")
        return 1
    if args.dry_run:
        print(f"[dry-run] would remove: {root / PROJECT_CONFIG_PATH}")
        print("dry-run: no changes made")
        return 0
    remove_project_installation(root, args.force, False)
    print("disabled (managed files only)")
    return 0


# ---------------------------------------------------------------------------
# profile


def cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "list":
        current = None
        manifest = store.load_manifest(store.manifest_path())
        if manifest:
            current = manifest.get("profile")
        for name in profiles.available_profiles():
            mark = " (current)" if name == current else ""
            print(f"{name}{mark}")
        return 0
    if args.profile_command == "show":
        manifest = store.load_manifest(store.manifest_path())
        name = args.name or (manifest.get("profile") if manifest else None) or "balanced"
        prof = profiles.load_profile(name)
        print(f"profile: {name}")
        for role in store.USER_AGENT_IDS:
            print(f"  {role:12} {prof[role]}")
        return 0
    # use
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        fail(f"not installed (no manifest: {store.manifest_path()}). run 'nepoforge init' first")
    config_dir = store.opencode_dir()
    prof = profiles.load_profile(args.name)
    expected = {rel: sha256_text(profiles.render(rel, prof)) for rel in USER_MANAGED_FILES}
    state = store.classify(config_dir, manifest, expected)
    conflicts = [rel for rel, kind in state.items() if kind == "conflict"]
    if conflicts:
        print("conflict: 非管理ファイルがあるため停止しました:", file=sys.stderr)
        for rel in conflicts:
            print(f"  [conflict] {config_dir / rel}", file=sys.stderr)
        raise SystemExit(2)
    changes = [rel for rel, kind in state.items() if kind in ("missing", "modified")]
    if not changes:
        print(f"profile is already '{args.name}'")
        return 0
    for rel in changes:
        print(f"  [update  ] {config_dir / rel}")
    if args.dry_run:
        print("dry-run: no changes made")
        return 0
    for rel in changes:
        backup = store.backup_file(config_dir, rel)
        if backup:
            print(f"  [backup  ] {rel} -> {backup}")
        dest = config_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(profiles.render(rel, prof), encoding="utf-8")
    store.store_write_manifest(args.name, expected)
    print(f"profile switched to '{args.name}'")
    return 0


# ---------------------------------------------------------------------------
# provider 追記 (#47 §5 init --provider fastmetal)


def apply_provider(dry_run: bool) -> None:
    config_dir = store.opencode_dir()
    config_file = next(
        (p for p in (config_dir / "opencode.jsonc", config_dir / "opencode.json") if p.is_file()),
        config_dir / "opencode.jsonc",
    )
    if config_file.is_file():
        text = config_file.read_text(encoding="utf-8")
        try:
            data = jsonc.loads(text)
        except NepoforgeError as exc:
            fail(f"cannot edit {config_file}: {exc}. fix or merge the FastMetal provider manually (see README)")
        providers = data.get("providers") if isinstance(data, dict) else None
        if isinstance(providers, dict) and "fastmetal" in providers:
            print(f"provider already configured: {config_file}")
            return
        if isinstance(providers, dict):
            fail(
                f"{config_file} has a 'providers' key without fastmetal. "
                "auto-merge is not attempted to protect your settings. add the fastmetal block manually "
                "(template: nepoforge/data/templates/provider-fastmetal.jsonc)"
            )
        if not isinstance(data, dict):
            fail(f"{config_file} is not a JSON object. refusing to edit")

        new_text = insert_provider_block(text)
    else:
        block = dedent_two(PROVIDER_TEMPLATE.read_text(encoding="utf-8").rstrip("\n"))
        new_text = '{\n  "$schema": "https://opencode.ai/config.json",\n' + block + "\n}\n"

    if dry_run:
        print(f"[dry-run] would add FastMetal provider to: {config_file}")
        print(store.diff_text(config_file if config_file.is_file() else None, _as_tmp_file(new_text, config_file.suffix)), end="")
        print("dry-run: no changes made")
        return
    backup = store.backup_file(config_dir, config_file.name)
    if backup:
        print(f"  [backup  ] {config_file} -> {backup}")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(new_text, encoding="utf-8")
    print(f"provider added (fastmetal): {config_file}")
    print("認証は既存の仕組みを使ってください (例: export FASTMETAL_API_KEY=... )。キーを設定ファイルに書かないこと。")


def insert_provider_block(text: str) -> str:
    """ルートオブジェクトの閉じ括弧直前に provider ブロックを挿入した全文を返す。"""
    stripped = text.rstrip()
    if not stripped.endswith("}"):
        raise NepoforgeError(f"config does not end with '}}'. refusing to edit")
    head = stripped[:-1].rstrip()
    needs_comma = not head.endswith("{")
    block = PROVIDER_TEMPLATE.read_text(encoding="utf-8").rstrip("\n")
    joined = head
    if needs_comma:
        joined += ","
    joined += "\n" + block + "\n}\n"
    return joined


def dedent_two(text: str) -> str:
    return "\n".join(line[2:] if line.startswith("  ") else line for line in text.splitlines())


def _as_tmp_file(text: str, suffix: str) -> Path:
    import tempfile

    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    tmp.write_text(text, encoding="utf-8")
    return tmp


# ---------------------------------------------------------------------------
# entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nepoforge",
        description="Nepoforge のユーザー単位導入・管理 CLI (#47)。設計: docs/design/0003-package-cli.md",
    )
    parser.add_argument("--version", action="version", version=f"nepoforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="ユーザー単位で初期導入する (グローバルに agents/commands を配置)")
    p.add_argument("--profile", default=None, help="適用するプロファイル (既定: balanced / 導入済みなら現行)")
    p.add_argument("--provider", choices=["fastmetal"], default=None, help="provider 定義が無い場合にバックアップ付きで追記する")
    p.add_argument("--dry-run", action="store_true", help="計画と差分のみ表示し、変更しない")
    p.add_argument("--force", action="store_true", help="利用者が改変した管理対象をバックアップ後に上書きする")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("update", help="管理下の共通配布物を最新版に同期する")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-projects", action="store_true", help="有効化済みプロジェクトの guardrail 更新を skip する")
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("status", help="導入状況を読み取り専用で表示する")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("uninstall", help="Nepoforge 所有の導入物のみ撤去する")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--projects", action="store_true", help="有効化済みプロジェクトの guardrail も同時に撤去する")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("doctor", help="環境を診断する (読み取り専用・課金なし)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("enable", help="現在のプロジェクトで guardrail を有効化する")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_enable)

    p = sub.add_parser("disable", help="現在のプロジェクトの guardrail を撤去する")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_disable)

    p = sub.add_parser("profile", help="モデルプロファイルを一元管理する")
    pprofile = p.add_subparsers(dest="profile_command", required=True)
    pprofile.add_parser("list", help="利用可能なプロファイル一覧")
    pshow = pprofile.add_parser("show", help="プロファイルの内容を表示する")
    pshow.add_argument("name", nargs="?", default=None)
    puse = pprofile.add_parser("use", help="プロファイルを適用する")
    puse.add_argument("name")
    puse.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except NepoforgeError as exc:
        fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
