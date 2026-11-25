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

from . import __version__, jsonc, profiles, store, derive
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
        elif kind == "updatable":
            plan.append(("update", rel))
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
        print(f"already installed (version {manifest.get('version')}); refreshing manifest")
    for action, rel in plan:
        print(f"  [{action:6}] {config_dir / rel}")
    if dry_run:
        print("dry-run: no changes made")
        for action, rel in plan:
            if action == "update":
                print(f"--- diff: {rel}")
                print(store.diff_text(config_dir / rel, store.DATA_DIR / rel), end="")
        # 派生定義の計画も表示
        try:
            cfg = profiles.load_profile_config(profile_name)
            dplan = derive.derive_plan(cfg, target_dir=config_dir)
            for it in dplan:
                print(f"  [derived {it.state:8}] {config_dir / it.rel}")
                if it.state in ("write", "conflict"):
                    target = config_dir / it.rel
                    print(store.diff_text_vs(target if target.is_file() else None, it.expected_text, str(target)), end="")
        except NepoforgeError:
            pass
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
    # 事前一括生成
    try:
        cfg = profiles.load_profile_config(profile_name)
        dplan = derive.derive_plan(cfg, target_dir=config_dir)
        _derive_apply(dplan, config_dir, force, False)
    except NepoforgeError as exc:
        print(f"warning: derived generation skipped: {exc}", file=sys.stderr)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    result = sync_global(args.profile, args.force, args.dry_run)
    if args.provider:
        apply_provider(args.dry_run)
    return result


def cmd_update(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        return not_installed("update")
    result = sync_global(manifest.get("profile", "balanced"), args.force, args.dry_run)
    if args.no_projects:
        return result
    return sync_projects(args.force, args.dry_run)


# ---------------------------------------------------------------------------
# 有効化済みプロジェクトの同期


def not_installed(command: str) -> int:
    """未導入時の終了 (設計 0003 §5: 終了コード 1)。"""
    print(f"error: not installed (no manifest: {store.manifest_path()}). run 'nepoforge init' first", file=sys.stderr)
    raise SystemExit(1)


def sync_projects(force: bool, dry_run: bool = False) -> int:
    """guardrail テンプレートを有効化済みプロジェクトへ再配布する (#47 §5 update)。"""
    projects = store.load_projects()
    if not projects:
        return 0
    template = GUARDRAIL_TEMPLATE.read_text(encoding="utf-8")
    template_hash = sha256_text(template)
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
        recorded = pm.get("files", {})
        current = store.sha256_of(config)
        if current is None:
            if dry_run:
                print(f"  [missing ] {config} (再配布予定)")
                continue
            print(f"  [missing ] {config} (再配布します)")
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(template, encoding="utf-8")
            pm["files"] = {PROJECT_CONFIG_PATH: store.sha256_of(config)}
            store.write_json(root / PROJECT_MANIFEST_PATH, pm)
            print(f"  [updated ] {config}")
            continue
        if recorded.get(PROJECT_CONFIG_PATH) == current:
            if current == template_hash:
                print(f"  [ok      ] {root}")
                continue
            action = "would update" if dry_run else "update"
            if dry_run:
                print(f"  [plan    ] {config} ({action})")
                print(store.diff_text_vs(config, template, str(config)), end="")
                continue
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
        if dry_run:
            print(f"  [plan    ] {config} (改変をバックアップ後に上書き予定: --force)")
            continue
        backup = store.backup_file(root, PROJECT_CONFIG_PATH)
        if backup:
            print(f"  [backup  ] {config} -> {backup}")
        config.write_text(template, encoding="utf-8")
        pm["files"] = {PROJECT_CONFIG_PATH: store.sha256_of(config)}
        store.write_json(root / PROJECT_MANIFEST_PATH, pm)
        print(f"  [updated ] {config}")
    if not dry_run:
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
    newer = [rel for rel, kind in state.items() if kind == "updatable"]
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
    """プロジェクト側の管理対象 (guardrail) を撤去する。改変時は既定で残す。

    戻り値は「管理記録を撤去できる状態になったか」。
    利用者改変を既定で残した場合は記録 (マニフェスト・中央記録) も残す。
    """
    pm = store.load_manifest(root / PROJECT_MANIFEST_PATH)
    if pm is None or pm.get("kind") != "project":
        print(f"  [skip    ] {root} (Nepoforge のプロジェクト マニフェストがないため触れません)")
        return True
    config = root / PROJECT_CONFIG_PATH
    current = store.sha256_of(config)
    kept = False
    if current is None:
        pass
    elif pm.get("files", {}).get(PROJECT_CONFIG_PATH) != current and not force:
        print(f"  [kept    ] {config} (利用者が変更しているため残しました。不要なら手動で削除してください)")
        kept = True
    else:
        if pm.get("files", {}).get(PROJECT_CONFIG_PATH) != current:
            backup = store.backup_file(root, PROJECT_CONFIG_PATH)
            if backup:
                print(f"  [backup  ] {config} -> {backup}")
        if dry_run:
            print(f"  [removed ] {config} (dry-run)")
        else:
            config.unlink()
            print(f"  [removed ] {config}")
    if dry_run or kept:
        return not kept
    (root / PROJECT_MANIFEST_PATH).unlink(missing_ok=True)
    store.forget_project(root)
    return True


def cmd_uninstall(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        return not_installed("uninstall")
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

    # 派生定義 (agents/nf-*.md) の撤去 (#52 §5.5)
    # 現行生成規則と内容が一致するものを通常削除、不一致は既定で残す
    safe_texts = derive.safe_texts()
    agents_dir = config_dir / "agents"
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("nf-*.md")):
            rel = f"agents/{f.name}"
            content = f.read_text(encoding="utf-8")
            safe = content in safe_texts
            if not safe and not args.force:
                print(f"  [kept    ] {rel} (内容が生成規則と一致しないため残しました。不要なら --force)")
                continue
            if not safe and args.force:
                backup = store.backup_file(config_dir, rel)
                if backup:
                    print(f"  [backup  ] {rel} -> {backup}")
            if args.dry_run:
                print(f"  [removed ] {rel} (dry-run)")
            else:
                f.unlink()
                print(f"  [removed ] {rel}")

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
        intact = current is not None and current == pm.get("files", {}).get(PROJECT_CONFIG_PATH)
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
    if not isinstance(pm.get("files"), dict):
        fail(f"manifest is broken ('files' mapping missing): {root / PROJECT_MANIFEST_PATH}")
    return pm


def cmd_enable(args: argparse.Namespace) -> int:
    root = store.find_project_root()
    for sibling in ("opencode.json", "opencode.jsonc"):
        if sibling != PROJECT_CONFIG_PATH.rsplit("/", 1)[1] and (root / sibling).is_file():
            print(f"warning: 既存の {root / sibling} が OpenCode に読み込まれます。guardrail とマージされます (無効化はされません)")
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
        try:
            cfg = profiles.load_profile_config(name)
            print(f"  max_parallel: {cfg.constraints.max_parallel}")
            print(f"  task_rules:   {len(cfg.task_rules)}")
        except NepoforgeError:
            pass
        return 0
    if args.profile_command == "derive":
        return cmd_profile_derive(args)
    # use
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        return not_installed("profile use")
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
    modified = [rel for rel, kind in state.items() if kind == "modified"]
    if modified and not args.force:
        print("conflict: 管理対象ファイルが利用者によって変更されているため停止しました:", file=sys.stderr)
        for rel in modified:
            print(f"  [modified] {config_dir / rel} (変更を退避するか、--force でバックアップ後に上書き)", file=sys.stderr)
        raise SystemExit(2)
    changes = [rel for rel, kind in state.items() if kind in ("missing", "updatable", "modified")]
    if not changes:
        print(f"profile is already '{args.name}'")
    else:
        for rel in changes:
            print(f"  [update  ] {config_dir / rel}")
    if args.dry_run:
        # 派生定義の計画も表示
        try:
            cfg = profiles.load_profile_config(args.name)
            plan = derive.derive_plan(cfg, target_dir=config_dir)
            for it in plan:
                print(f"  [derived {it.state:8}] {config_dir / it.rel}")
        except NepoforgeError:
            pass
        print("dry-run: no changes made")
        return 0
    if changes:
        for rel in changes:
            backup = store.backup_file(config_dir, rel)
            if backup:
                print(f"  [backup  ] {rel} -> {backup}")
            dest = config_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(profiles.render(rel, prof), encoding="utf-8")
        store.store_write_manifest(args.name, expected)
    # 事前一括生成
    try:
        cfg = profiles.load_profile_config(args.name)
        plan = derive.derive_plan(cfg, target_dir=config_dir)
        _derive_apply(plan, config_dir, args.force, False)
    except NepoforgeError as exc:
        print(f"warning: derived generation skipped: {exc}", file=sys.stderr)
    return 0


def _derive_apply(plan, config_dir, force, dry_run):
    """派生定義の計画を適用する共通処理 (profile derive / use / init で再利用)。"""
    conflicts = [it for it in plan if it.state == "conflict"]
    if conflicts and not force and not dry_run:
        print("conflict: 派生定義が既存ファイルと異なるため停止しました:", file=sys.stderr)
        for it in conflicts:
            print(f"  [conflict] {config_dir / it.rel}", file=sys.stderr)
        raise SystemExit(2)
    if dry_run:
        for it in plan:
            print(f"  [derived {it.state:8}] {config_dir / it.rel}")
            if it.state in ("write", "conflict"):
                target = config_dir / it.rel
                print(store.diff_text_vs(target if target.is_file() else None, it.expected_text, str(target)), end="")
        return 0
    for it in plan:
        if it.state == "ok":
            continue
        if it.state == "conflict" and not force:
            continue
        target = config_dir / it.rel
        if it.state == "conflict" and force:
            backup = store.backup_file(config_dir, it.rel)
            if backup:
                print(f"  [backup  ] {it.rel} -> {backup}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(it.expected_text, encoding="utf-8")
        print(f"  [derived {it.state:8}] {target}")
    return 0


def cmd_profile_derive(args: argparse.Namespace) -> int:
    manifest = store.load_manifest(store.manifest_path())
    name = args.profile or (manifest.get("profile") if manifest else None) or "balanced"
    config = profiles.load_profile_config(name)
    config_dir = store.opencode_dir()

    role = args.role
    model = args.model
    model_ref = None
    try:
        if model is not None:
            model_ref = profiles.parse_model(model)
        plan = derive.derive_plan(config, role=role, model=model_ref, target_dir=config_dir)
    except NepoforgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    if not plan:
        print("nothing to derive (all pairs match role defaults or already present)")
        # prune 処理は継続
        if not args.prune:
            return 0

    print(f"profile: {name}")
    for it in plan:
        print(f"  [{it.state:8}] {config_dir / it.rel}")

    # conflict 停止
    if any(it.state == "conflict" for it in plan) and not args.force and not args.dry_run:
        print("conflict: 派生定義が既存ファイルと異なるため停止しました:", file=sys.stderr)
        for it in plan:
            if it.state == "conflict":
                print(f"  [conflict] {config_dir / it.rel}", file=sys.stderr)
        raise SystemExit(2)

    if not args.dry_run:
        _derive_apply(plan, config_dir, args.force, False)
    else:
        _derive_apply(plan, config_dir, args.force, True)

    # prune
    if args.prune:
        plan_ids = {it.agent_id for it in plan}
        # 正規 orphan (旧プロファイル由来の生成物) は現行 plan に含まれないため、
        # 内容一致の判定には生成規則全体の集合を使う (#52 レビュー指摘)
        regenerable = derive.safe_texts()
        agents_dir = config_dir / "agents"
        if agents_dir.is_dir():
            for f in sorted(agents_dir.glob("nf-*.md")):
                if f.stem in plan_ids:
                    continue
                content = f.read_text(encoding="utf-8")
                rel = f"agents/{f.name}"
                if content in regenerable:
                    # 安全に削除
                    if args.dry_run:
                        print(f"  [prune   ] {f} (dry-run)")
                    else:
                        backup = store.backup_file(config_dir, rel)
                        if backup:
                            print(f"  [backup  ] {rel} -> {backup}")
                        f.unlink()
                        print(f"  [pruned  ] {f}")
                else:
                    if not args.force:
                        print(f"  [kept    ] {f} (内容が生成規則と一致しないため残しました。--force でバックアップ後に削除)")
                    else:
                        if args.dry_run:
                            print(f"  [prune   ] {f} (dry-run, --force)")
                        else:
                            backup = store.backup_file(config_dir, rel)
                            if backup:
                                print(f"  [backup  ] {rel} -> {backup}")
                            f.unlink()
                            print(f"  [pruned  ] {f}")

    if args.dry_run:
        print("dry-run: no changes made")
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
        if config_file.suffix == ".json":
            fail(
                f"{config_file} is strict JSON. comments in the FastMetal block would break it. "
                "add the provider manually (template: nepoforge/data/templates/provider-fastmetal.jsonc) "
                "or rename the file to opencode.jsonc"
            )
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
        # 書き込み前に結果を検証する (壊れた設定を成功として書かない)
        try:
            parsed = jsonc.loads(new_text)
        except NepoforgeError as exc:
            fail(
                f"auto-insertion would produce invalid JSONC for {config_file} ({exc}). "
                "no changes were made. add the fastmetal block manually "
                "(template: nepoforge/data/templates/provider-fastmetal.jsonc)"
            )
        if not isinstance(parsed, dict) or "fastmetal" not in (parsed.get("providers") or {}):
            fail(f"auto-insertion could not be verified for {config_file}. no changes were made")
    else:
        block = dedent_two(PROVIDER_TEMPLATE.read_text(encoding="utf-8").rstrip("\n"))
        new_text = '{\n  "$schema": "https://opencode.ai/config.json",\n' + block + "\n}\n"

    if dry_run:
        print(f"[dry-run] would add FastMetal provider to: {config_file}")
        print(store.diff_text_vs(config_file if config_file.is_file() else None, new_text, str(config_file)), end="")
        print("dry-run: no changes made")
        return
    backup = store.backup_file(config_dir, config_file.name)
    if backup:
        print(f"  [backup  ] {config_file} -> {backup}")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(new_text, encoding="utf-8")
    print(f"provider added (fastmetal): {config_file}")
    print("認証は既存の仕組みを使ってください (例: export FASTMETAL_API_KEY=... )。キーを設定ファイルに書かないこと。")


def _comment_start(line: str) -> int | None:
    """行内で文字列リテラルの外側にある最初の `//` の位置を返す (無ければ None)。"""
    in_string = False
    escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if in_string:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return i
    return None


def insert_provider_block(text: str) -> str:
    """ルートオブジェクトの閉じ括弧直前に provider ブロックを挿入した全文を返す。

    末尾メンバーの後ろに同行コメントがあってもカンマをコメントの前に置き、
    trailing comma が既にあれば追加しない。結果の検証は呼び出し側で行う。
    """
    stripped = text.rstrip()
    if not stripped.endswith("}"):
        raise NepoforgeError("config does not end with '}'. refusing to edit")
    head = stripped[:-1].rstrip()
    lines = head.split("\n")
    last = lines[-1]
    cut = _comment_start(last)
    trailing_comment = ""
    if cut is not None:
        trailing_comment = last[cut:].strip()
        last = last[:cut].rstrip()
    base = "\n".join(lines[:-1] + [last]).rstrip()
    needs_comma = bool(base) and not base.endswith(",") and not base.endswith("{")
    if needs_comma:
        base += ","
    joined = base
    if trailing_comment:
        joined += " " + trailing_comment
    joined += "\n" + PROVIDER_TEMPLATE.read_text(encoding="utf-8").rstrip("\n") + "\n}\n"
    return joined


def dedent_two(text: str) -> str:
    return "\n".join(line[2:] if line.startswith("  ") else line for line in text.splitlines())


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
    puse.add_argument("--force", action="store_true", help="利用者が改変した管理対象をバックアップ後に上書きする")
    pderive = pprofile.add_parser("derive", help="派生エージェント定義を生成する")
    pderive.add_argument("--profile", default=None, help="対象プロファイル (既定: 現行または balanced)")
    pderive.add_argument("--role", default=None, help="生成対象の役割")
    pderive.add_argument("--model", default=None, help="生成対象のモデル")
    pderive.add_argument("--prune", action="store_true", help="不要な派生定義を削除する")
    pderive.add_argument("--dry-run", action="store_true", help="計画と差分のみ表示し、変更しない")
    pderive.add_argument("--force", action="store_true", help="衝突・改変ファイルをバックアップ後に上書き/削除する")
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
