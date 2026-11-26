"""nepoforge CLI (#47): ユーザー単位の導入・更新・診断。

コマンド:
    init / update / status / uninstall / doctor / enable / disable / profile / plan / validate

設計: docs/design/0003-package-cli.md
安全方針: #9 (nf_workbench.py) の機構を引き継ぐ — dry-run、衝突時停止、
バックアップ、冪等、管理対象のみ削除。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from . import __version__, budget, data_policy, jsonc, planning, profiles, resolver, store, derive, validate, delegation_plan, delegation_run
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
# #70 以前の配布ブロック (末尾改行なし)。完全一致する管理ブロックだけを移行する。
LEGACY_FASTMETAL_BLOCK_HASHES = {
    "0fe7fa135597ecec69049be1e46b47f64eb4a4d96c40de25b255022eea9263ef",
    "e856da90fe0b0dc385771df461437df6c39bb54cfaba2bfbb42d0f7d3a256d25",
    "a4923a7050fb1902cf5a2c949ff7f4d695de2d1136ab9d4a7496f65aa0975906",
    "e7ca28ea2dc74f16681cbea5008cdae45dee55ee69b6e56247e727f640cb2e5f",
}


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
    symlinks = []
    for rel in expected:
        path = config_dir
        for part in Path(rel).parts:
            path = path / part
            if path.is_symlink():
                symlinks.append((rel, path))
                break
    if symlinks:
        print("conflict: managed target or parent is a symlink; refusing to write:", file=sys.stderr)
        for rel, path in symlinks:
            print(f"  [symlink] {rel}: {path}", file=sys.stderr)
        raise SystemExit(2)
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

    retired: list[Path] = []
    try:
        cfg = profiles.load_profile_config(profile_name)
        # manifest と一致する旧役割定義から、未改変の旧派生定義を識別する。
        previous_roles = {}
        for role in store.USER_AGENT_IDS:
            rel = f"agents/{role}.md"
            if state[rel] == "updatable":
                previous_roles[role] = (config_dir / rel).read_text(encoding="utf-8")
        dplan = derive.derive_plan(cfg, target_dir=config_dir, previous_roles=previous_roles)
        if profile_name in ("economy", "balanced", "performance"):
            retired = derive.legacy_premium_paths(config_dir, previous_roles)
    except NepoforgeError as exc:
        print(f"warning: derived generation skipped: {exc}", file=sys.stderr)
        dplan = []
    if not dry_run:
        _check_derive_conflicts(dplan, config_dir, force)

    if manifest is not None and not plan and not retired and all(it.state == "ok" for it in dplan):
        if manifest.get("version") == store.VERSION and manifest.get("profile") == profile_name:
            print(f"up to date (version {manifest.get('version')}, profile {profile_name}). nothing to do")
            return 0
        print(f"already installed (version {manifest.get('version')}); refreshing manifest")
    for action, rel in plan:
        print(f"  [{action:6}] {config_dir / rel}")
    for path in retired:
        print(f"  [retire] {path}")
    if dry_run:
        print("dry-run: no changes made")
        for action, rel in plan:
            if action == "update":
                print(f"--- diff: {rel}")
                print(store.diff_text(config_dir / rel, store.DATA_DIR / rel), end="")
        _derive_apply(dplan, config_dir, force, True)
        return 0

    for action, rel in plan:
        if action == "update":
            backup = store.backup_file(config_dir, rel)
            if backup:
                print(f"  [backup  ] {rel} -> {backup}")
        dest = config_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(profiles.render(rel, prof), encoding="utf-8")

    _derive_apply(dplan, config_dir, force, False)
    for path in retired:
        rel = path.relative_to(config_dir).as_posix()
        backup = store.backup_file(config_dir, rel)
        if backup:
            print(f"  [backup  ] {rel} -> {backup}")
            path.unlink()
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
            if args.dry_run:
                print(f"  [backup  ] {rel} (dry-run: バックアップ予定)")
            else:
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
                if args.dry_run:
                    print(f"  [backup  ] {rel} (dry-run: バックアップ予定)")
                else:
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
        for d in ("agents", "commands", "plugins"):
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


_AUTH_LIST_TIMEOUT = 10


def _one_line(value: object) -> str:
    """値を先頭 1 行・200 文字に切り詰める (detail の肥大・漏えい防止)。"""
    text = str(value)
    lines = text.strip().splitlines()
    line = lines[0] if lines else text.strip()
    if len(line) > 200:
        line = line[:200]
    return line


def _tool_version(name: str) -> str | None:
    return _tool_version_with_exe(name)[1]


def _tool_version_with_exe(name: str) -> tuple[str | None, str | None]:
    """(実行パス, バージョン行) を返す。PATH に無ければ (None, None)。"""
    exe = shutil.which(name)
    if exe is None:
        return None, None
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return exe, "unknown"
    output = (result.stdout or result.stderr).strip().splitlines()
    return exe, output[0] if output else "unknown"


def _run_opencode_auth_list() -> tuple[str, str]:
    """`opencode auth list --format json` を実行する (読み取り専用・課金なし)。

    戻り値: (状態, 補足)
      ("ok", stdout)         rc=0 (stdout をそのまま返す。ログには出さない)
      ("not-found", "")      opencode が PATH 上に無い (shutil.which が None)
      ("timeout", "")        _AUTH_LIST_TIMEOUT 秒でタイムアウト
      ("error", "理由")      OSError (プロセス起動失敗など)
      ("failed", "stderr先頭行")  rc != 0
    """
    exe = shutil.which("opencode")
    if exe is None:
        return ("not-found", "")
    try:
        result = subprocess.run(
            [exe, "auth", "list", "--format", "json"],
            capture_output=True, text=True, timeout=_AUTH_LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", "")
    except OSError as exc:
        return ("error", _one_line(exc))
    if result.returncode != 0:
        # stdout には触れない (rc 異常時の出力に想定外の内容が混じる場合の露出面を狭める)。
        reason = (result.stderr or "").strip().splitlines()
        return ("failed", _one_line(reason[0]) if reason else f"rc={result.returncode}")
    return ("ok", result.stdout or "")


def _fastmetal_in_auth_list(stdout: str) -> bool | None:
    """auth list の stdout に fastmetal の登録があるかを判定する (純粋関数)。

    戻り値: True (登録あり) / False (解釈成功・登録なし) / None (解釈不能)
    """
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    for entry in data:
        if not isinstance(entry, dict):
            return None
        ident = str(entry.get("id") or "").lower()
        name = str(entry.get("name") or "").lower()
        if ident == "fastmetal" or name == "fastmetal":
            return True
    return False


def _fastmetal_auth_status() -> tuple[str, str]:
    """FastMetal 認証の状態を (状態, detail) で返す。

    状態: "env" / "stored" / "env+stored" / "missing" / "unknown"
    """
    env_set = bool(_tool_env_key())
    state, extra = _run_opencode_auth_list()
    if state == "ok":
        found = _fastmetal_in_auth_list(extra)
        cli = "stored" if found else ("missing" if found is False else "unknown")
        cli_reason = ("unrecognized output from 'opencode auth list --format json'"
                      if cli == "unknown" else "")
    elif state == "not-found":
        cli, cli_reason = "unknown", "opencode not found"
    elif state == "timeout":
        cli, cli_reason = "unknown", "'opencode auth list' timed out"
    elif state == "failed":
        cli, cli_reason = "unknown", f"'opencode auth list' failed: {extra}"
    else:
        cli, cli_reason = "unknown", f"'opencode auth list' error: {extra}"

    if cli == "stored":
        if env_set:
            return ("env+stored", "FASTMETAL_API_KEY is set; also stored in opencode auth")
        return ("stored", "fastmetal is stored in opencode auth")
    if cli == "missing":
        if env_set:
            return ("env", "FASTMETAL_API_KEY is set (not stored in opencode auth)")
        return ("missing",
                "neither FASTMETAL_API_KEY nor stored fastmetal credential "
                "(models will fail without credentials)")
    if env_set:
        return ("env", f"FASTMETAL_API_KEY is set (opencode auth unverifiable: {cli_reason})")
    return ("unknown",
            f"cannot verify fastmetal credential ({cli_reason}). "
            "registration not confirmed — check with 'opencode auth list'")


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []

    for tool in ("opencode", "git", "gh"):
        # 実行パスとバージョン取得を 1 回の探索にまとめる (PATH の探索を 2 度繰り返さない)。
        exe, version = _tool_version_with_exe(tool)
        ok = exe is not None and version is not None and version != "unknown"
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

    state, detail = _fastmetal_auth_status()
    # "missing" (env も保存済みも無い) のみ FAIL。確認不能 (unknown) は
    # degraded として ok=True にし、理由を detail で明示する (設計メモ 0009 §3.2)。
    checks.append(("fastmetal credential hint", state != "missing", detail))

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


def _read_plan_file(path: str) -> list[dict]:
    """計画 JSON ({"tasks": [...]} または bare list) を読み込む。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise NepoforgeError(f"cannot read plan file: {path} ({exc})") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise NepoforgeError(f"plan file is not JSON: {path} ({exc})") from exc
    if isinstance(data, dict):
        if "data_policy" in data:
            raise NepoforgeError("plan cannot set the project data_policy")
        data = data.get("tasks")
    if not isinstance(data, list):
        raise NepoforgeError(f"plan must be an array of tasks or {{\"tasks\": [...]}}: {path}")
    return data


def _plan_task_from_dict(raw: dict) -> tuple[planning.PlannedTask, dict]:
    """計画 1 件を PlannedTask と resolver/budget 用の拡張属性に分ける。"""
    if not isinstance(raw, dict):
        raise NepoforgeError("plan task must be an object")
    forbidden = raw.keys() & {"permissions", "destination", "reasoning", "reasoning_effort", "variant"}
    if forbidden:
        raise NepoforgeError(f"plan task cannot override: {', '.join(sorted(forbidden))}")
    for key in ("task_id", "role"):
        if not isinstance(raw.get(key), str):
            raise NepoforgeError(f"plan task requires string '{key}'")
    if "capabilities" in raw and (
        not isinstance(raw["capabilities"], list)
        or not all(isinstance(cap, str) for cap in raw["capabilities"])
    ):
        raise NepoforgeError("plan task 'capabilities' must be a string array")
    context_tokens = raw.get("context_tokens")
    if context_tokens is not None and (type(context_tokens) is not int or context_tokens < 1):
        raise NepoforgeError("plan task 'context_tokens' must be a positive integer")
    if raw.get("task_kind") is not None and not isinstance(raw["task_kind"], str):
        raise NepoforgeError("plan task 'task_kind' must be a string")
    if "data_policy" in raw:
        data_policy.validate(raw["data_policy"])
    if "domestic_only" in raw and not isinstance(raw["domestic_only"], bool):
        raise NepoforgeError("plan task 'domestic_only' must be boolean")
    extras = {
        k: raw[k]
        for k in (
            "model_request",
            "executor",
            "capabilities",
            "complexity",
            "risk",
            "concern",
            "context_tokens",
            "responsibility",
            "task_kind",
            "data_policy",
            "domestic_only",
            "max_input_tokens",
            "max_output_tokens",
            "retries",
        )
        if k in raw
    }
    task = planning.PlannedTask(
        task_id=raw["task_id"],
        role=raw["role"],
        depends_on=tuple(raw.get("depends_on", ()) or ()),
        write_scope=tuple(raw.get("write_scope", ()) or ()),
    )
    return task, extras


def cmd_plan(args: argparse.Namespace) -> int:
    """タスク計画の検証・ready 判定・予算ゲート (課金なし)。設計メモ 0005 §4.4・0008。"""
    strict = bool(getattr(args, "contract", False))
    if strict and getattr(args, "public_strict_options", False):
        raise NepoforgeError("strict contract options require --contract")
    if strict and getattr(args, "retries", None) is not None:
        raise NepoforgeError("--retries cannot override strict contract retries")
    if getattr(args, "approved_exception", ()) and not strict:
        raise NepoforgeError("--approved-exception requires --contract")
    if getattr(args, "plan_command", None) == "report" and not strict:
        raise NepoforgeError("plan report requires --contract")
    if strict:
        try:
            document = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NepoforgeError(f"cannot read contract JSON: {args.file} ({exc})") from exc
        digest = delegation_plan.validate_contract(
            document, approved_exceptions=getattr(args, "approved_exception", ()) or ()
        )
    else:
        document = None
        digest = None
    tasks_raw = _read_plan_file(args.file)
    if strict:
        tasks_raw = document["tasks"]
    planned, extras = [], {}
    for raw in tasks_raw:
        task, ext = _plan_task_from_dict(raw)
        planned.append(task)
        extras[task.task_id] = ext

    if args.plan_command == "validate":
        if not strict:
            planning.validate_plan(planned)
        if args.json:
            output = {"valid": True, "tasks": len(planned)}
            if strict:
                output["plan_sha256"] = digest
            print(json.dumps(output, ensure_ascii=False))
        else:
            print(f"plan is valid ({len(planned)} tasks)")
        return 0

    # ready: 計画検証 → モデル解決 → ready 判定 → 予算ゲート
    if strict and (not args.run_id or not args.gate):
        raise NepoforgeError("strict ready requires --run-id and --gate")
    if not strict:
        planning.validate_plan(planned)
    manifest = store.load_manifest(store.manifest_path())
    profile_name = args.profile or (manifest.get("profile") if manifest else None) or "balanced"
    config = profiles.load_profile_config(profile_name)
    project_policy = data_policy.load_project_policy()
    max_parallel_value = getattr(args, "max_parallel", None)
    if strict and max_parallel_value is not None and max_parallel_value > config.constraints.max_parallel:
        raise NepoforgeError("--max-parallel cannot exceed the profile limit in strict mode")
    max_parallel = max_parallel_value or config.constraints.max_parallel

    run_report = None
    unknown_run_cost = False
    resolutions: dict[str, resolver.ModelResolution] = {}
    if strict:
        if any(getattr(args, key, None) is not None for key in ("accepted", "running", "failed", "spent_yen", "reserved_yen")) or getattr(args, "budget_error", False):
            raise NepoforgeError("strict ready state must come only from --gate")
    else:
        if getattr(args, "approved_exception", ()) or getattr(args, "run_id", None) or getattr(args, "gate", None):
            raise NepoforgeError("--approved-exception/--run-id/--gate require --contract")
        accepted = set(filter(None, (args.accepted or "").split(","))) if args.accepted else set()
        running = set(filter(None, (args.running or "").split(","))) if args.running else set()
        failed = set(filter(None, (args.failed or "").split(","))) if args.failed else set()
    by_id = {t.task_id: t for t in planned}

    resolved: dict[str, str] = {}
    unresolved: list[dict] = []
    for tid, ext in extras.items():
        executor = ext.get("executor", "agent")
        if strict and executor == "tool":
            continue
        if strict and executor == "primary":
            primary = config.primary_for(project_policy)
            if primary is None:
                unresolved.append({"task_id": tid, "reason": "profile has no Primary recommendation"})
                continue
            # Primary direct execution is an exception to delegation, not to task
            # safety constraints. Refuse constraints that cannot be proven here.
            if (ext.get("data_policy") or ext.get("domestic_only")
                    or ext.get("capabilities") or ext.get("context_tokens")):
                unresolved.append({
                    "task_id": tid,
                    "reason": "primary exception cannot verify task-specific model constraints",
                })
                continue
            resolutions[tid] = resolver.ModelResolution(
                task_id=tid, executor="primary", role=by_id[tid].role,
                resolved_model=primary, binding="primary", reason="profile_primary",
                data_policy=project_policy, status="selected",
            )
            resolved[tid] = profiles.format_model(primary)
            continue
        request = ext.get("model_request")
        model_ref = profiles.parse_model(request) if request is not None else None
        res = resolver.resolve(
            resolver.TaskInput(
                task_id=tid,
                role=by_id[tid].role,
                capabilities=tuple(ext.get("capabilities", ()) or ()),
                complexity=ext.get("complexity", "medium"),
                risk=ext.get("risk", "medium"),
                concern=ext.get("concern"),
                context_tokens=ext.get("context_tokens"),
                model_request=model_ref,
                # 計画 JSON は人間設定の出所を証明できない。
                model_request_source="plan",
                executor=executor,
                responsibility=ext.get("responsibility", "leaf"),
                task_kind=ext.get("task_kind"),
                data_policy=ext.get("data_policy"),
                domestic_only=ext.get("domestic_only", False),
            ),
            config,
            project_policy=project_policy,
        )
        if res.status == "selected" and res.resolved_model is not None:
            resolved[tid] = profiles.format_model(res.resolved_model)
            resolutions[tid] = res
        elif res.status != "selected":
            # executor=tool の通常解決 (reason=tool, model=None) は未解決ではない
            unresolved.append({"task_id": tid, "reason": res.reason})

    if strict:
        try:
            run_doc = json.loads(Path(args.gate).read_text(encoding="utf-8"))
            run_state = delegation_run.evaluate_run(
                run_doc, run_id=args.run_id, plan_sha256=digest, tasks=planned,
                executors={tid: ext.get("executor", "agent") for tid, ext in extras.items()},
                resolutions=resolutions,
                retry_limits={tid: ext.get("retries", 0) for tid, ext in extras.items()},
                primary_model=profiles.format_model(config.primary_for(project_policy))
                if config.primary_for(project_policy) else None,
            )
        except (OSError, ValueError) as exc:
            raise NepoforgeError(f"invalid run gate: {exc}") from exc
        accepted, running, failed = run_state.accepted, run_state.running, run_state.failed
        retryable = run_state.retryable
        run_report = run_state.report
        args.budget_error = run_state.provider_budget_error
        unknown_run_cost = run_state.spent_yen is None or run_state.reserved_yen is None
        args.spent_yen = run_state.spent_yen or Decimal(0)
        args.reserved_yen = run_state.reserved_yen or Decimal(0)
    else:
        retryable = set()
        args.spent_yen = args.spent_yen if args.spent_yen is not None else 0.0
        args.reserved_yen = args.reserved_yen if args.reserved_yen is not None else 0.0
    if args.plan_command == "report":
        # 最終報告: 起動判定は行わず、確定 gate から時間・費用・予算差を要約する (#12)。
        run_report["budget"] = _run_budget_block(
            args.limit_yen, unknown_run_cost, args.spent_yen, args.reserved_yen
        )
        summary = delegation_run.summarize_report(run_report)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(_format_run_summary(summary))
        return 0
    ready_ids = planning.ready_tasks(
        planned, accepted=accepted, running=running,
        failed=failed - retryable if strict else failed, max_parallel=max_parallel
    )
    launch: list[str] = []
    launch_details: list[dict] = []

    def record_launch(tid: str) -> None:
        launch.append(tid)
        if strict:
            launch_details.append({
                "task_id": tid,
                "executor": extras[tid].get("executor", "agent"),
                "stage": next(t.get("stage") for t in document["tasks"] if t["task_id"] == tid),
                "attempt": 2 if tid in retryable else 1,
            })

    reservations: dict[str, float] = {}
    denied: list[dict] = []
    not_ready = [tid for tid in by_id if tid not in ready_ids]
    state = budget.BudgetState(
        limit_yen=args.limit_yen,
        spent_yen=args.spent_yen,
        reserved_yen=args.reserved_yen,
    )
    for tid in ready_ids:
        ext = extras[tid]
        model = resolved.get(tid)
        if ext.get("executor", "agent") == "tool":
            # 決定的ツール実行は課金なし。ただし信頼できない model_request が
            # 付いた場合は resolver が unresolved にするため、起動しない (#52)。
            if any(u["task_id"] == tid for u in unresolved):
                denied.append(
                    {"task_id": tid, "reason": unresolved_reason(unresolved, tid)}
                )
            else:
                record_launch(tid)
            continue
        if unknown_run_cost:
            denied.append({"task_id": tid, "reason": "run cost is unknown; paid launch denied"})
            continue
        if model is None:
            denied.append(
                {"task_id": tid, "reason": f"model unresolved ({unresolved_reason(unresolved, tid)})"}
            )
            continue
        resolution = resolutions[tid]
        output_limit = resolution.max_output_tokens
        output_estimate = ext.get("max_output_tokens", args.est_output)
        if output_estimate is None:
            output_estimate = output_limit or 50_000
        est = budget.LaunchEstimate(
            model=model,
            max_input_tokens=ext.get("max_input_tokens", args.est_input),
            max_output_tokens=output_estimate,
            retries=ext.get("retries", 0) if strict else ext.get(
                "retries", args.retries if args.retries is not None else 1
            ),
        )
        d = budget.decide_launch(state, est, provider_budget_error=args.budget_error)
        if not d.allowed:
            denied.append({"task_id": tid, "reason": d.reason})
            continue
        if output_limit is not None and est.max_output_tokens > output_limit:
            denied.append({"task_id": tid, "reason": f"output estimate exceeds task policy ({output_limit} tokens)"})
            continue
        binding_problem = None if strict and ext.get("executor") == "primary" else _plan_binding_problem(resolution, model)
        if binding_problem:
            denied.append({"task_id": tid, "reason": binding_problem})
            continue
        record_launch(tid)
        reservations[tid] = d.estimated_cost_yen
        reserved = Decimal(str(state.reserved_yen)) + Decimal(str(d.estimated_cost_yen))
        state = replace(state, reserved_yen=reserved)
    result = {
        "profile": profile_name,
        "data_policy": project_policy,
        "ready": ready_ids,
        "launch": launch,
        "reservations_yen": reservations,
        "denied": denied,
        "not_ready": sorted(not_ready),
        "resolved": resolved,
        "model_resolutions": {
            tid: {
                "binding": res.binding,
                "reason": res.reason,
                "reasoning": res.reasoning,
                "max_output_tokens": res.max_output_tokens,
                "data_policy": res.data_policy,
                "endpoint": res.endpoint,
            }
            for tid, res in resolutions.items()
        },
        "unresolved": unresolved,
    }
    if strict:
        run_report = dict(run_report or {})
        run_report["budget"] = _run_budget_block(
            args.limit_yen, unknown_run_cost, args.spent_yen, args.reserved_yen
        )
        result.update(
            run_id=args.run_id,
            plan_sha256=digest,
            stages=list(dict.fromkeys(task.get("stage") for task in document["tasks"])),
            run_report=run_report,
            launch_details=launch_details,
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"profile: {profile_name}")
        print(f"  launch:     {' '.join(launch) if launch else '(none)'}")
        if strict and launch_details:
            for detail in launch_details:
                print(
                    f"  launch detail: {detail['task_id']} "
                    f"(attempt {detail['attempt']}, stage {detail['stage']}, "
                    f"executor {detail['executor']})"
                )
        for d in denied:
            print(f"  denied:     {d['task_id']} — {d['reason']}")
        if not_ready:
            print(f"  not ready:  {' '.join(sorted(not_ready))}")
        for u in unresolved:
            print(f"  unresolved: {u['task_id']} — {u['reason']}")
    return 0


def _run_budget_block(limit_yen, unknown_run_cost, spent_yen, reserved_yen) -> dict:
    """run_report["budget"] の共通ブロック (ready と report で同じ値になる)。"""
    return {
        "limit_yen": str(limit_yen) if limit_yen is not None else None,
        "spent_yen": None if unknown_run_cost else str(spent_yen),
        "reserved_yen": None if unknown_run_cost else str(reserved_yen),
        "estimate_basis": "unknown" if unknown_run_cost else "profile price table and token limits",
    }


def _format_yen(value: object) -> str:
    """表示用の金額。不明は 0 円と偽らず「不明」とする (#12)。"""
    if value is None or value == "unknown":
        return "不明"
    return f"約 {Decimal(str(value)):.2f}円"


def _format_run_summary(summary: dict) -> str:
    """run_report 要約の人間可読な最終報告 (#12)。時間と費用は常に含める。"""
    cost = summary["cost"]
    if cost["state"] == "unknown":
        reason = cost.get("reason") or "理由不明"
        cost_line = f"API費用: 不明（理由: {reason}）"
    else:
        cost_line = f"API費用: {_format_yen(cost['total'])}（{cost['basis']}）"
    lines = [
        f"所要時間: {summary['walltime_text']}",
        f"実行状態: {summary['outcome']}",
        cost_line,
        f"内訳: Primary {_format_yen(cost['primary'])} / サブエージェント {_format_yen(cost['children'])}",
    ]
    budget = summary.get("budget")
    limit_text = _format_yen(budget.get("limit_yen")) if budget else None
    if budget and limit_text != "不明":
        remaining = budget.get("remaining_yen")
        if remaining in (None, "unknown"):
            lines.append(f"予算: 上限 {limit_text} / 残額 不明")
        elif budget.get("over"):
            lines.append(f"予算: 上限 {limit_text} / 超過 {-Decimal(str(remaining)):.2f}円")
        else:
            lines.append(f"予算: 上限 {limit_text} / 残り {Decimal(str(remaining)):.2f}円")
    else:
        lines.append("予算: 上限未設定")
    return "\n".join(lines)


def _plan_binding_problem(resolution: resolver.ModelResolution, model: str) -> str | None:
    role = resolution.role
    binding = resolution.binding
    path = store.opencode_dir() / "agents" / f"{binding}.md"
    try:
        if binding == role:
            expected = profiles.render(f"agents/{role}.md", {role: model})
        else:
            expected = derive.derive_text(role, model)
        actual = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, NepoforgeError):
        return f"agent binding unavailable: {binding}"
    if actual != expected:
        return f"agent binding differs from managed definition: {binding}"
    return None


def unresolved_reason(unresolved: list[dict], task_id: str) -> str:
    for u in unresolved:
        if u["task_id"] == task_id:
            return u["reason"]
    return "unknown"


def cmd_profile(args: argparse.Namespace) -> int:
    if args.profile_command in ("check-primary", "check-route"):
        manifest = store.load_manifest(store.manifest_path())
        name = args.profile or (manifest.get("profile") if manifest else None) or "balanced"
        config = profiles.load_profile_config(name)
        observed = profiles.parse_model(args.model)
        policy = data_policy.effective(data_policy.load_project_policy(), args.data_policy)
        if args.profile_command == "check-primary":
            recommended = config.primary_for(policy)
            if recommended is None:
                fail(f"profile '{name}' has no Primary recommendation")
            if observed != recommended:
                fail(f"Primary model mismatch: selected {observed}, recommended {recommended}")
        endpoint = resolver.approved_endpoint(config, observed)
        if not endpoint or endpoint.rstrip("/") != args.endpoint.rstrip("/") or args.upstream_model != observed.model:
            fail(f"unapproved execution route for model: {observed}")
        candidate = next((c for c in config.candidates if c.ref == observed), None)
        if candidate is None or not data_policy.permits(
            policy, no_training=candidate.no_training, retention=candidate.retention
        ):
            fail(f"model is not eligible for Data Policy '{policy}': {observed}")
        if budget.price_for(str(observed)) is None or budget.expired_price(str(observed)):
            fail(f"model has no current price in the budget table: {observed}")
        print(f"route verified for profile '{name}' and Data Policy '{policy}': {observed}")
        return 0
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
            print(f"  primary_model (recommended): {cfg.primary_model or '(unset)'}")
            for policy, model in cfg.primary_model_by_policy.items():
                print(f"  primary_model ({policy}): {model}")
        except NepoforgeError:
            pass
        return 0
    if args.profile_command == "derive":
        return cmd_profile_derive(args)
    # use
    manifest = store.load_manifest(store.manifest_path())
    if manifest is None:
        return not_installed("profile use")
    return sync_global(args.name, args.force, args.dry_run)


def _check_derive_conflicts(plan, config_dir, force):
    conflicts = [it for it in plan if it.state == "conflict"]
    if conflicts and not force:
        print("conflict: 派生定義が既存ファイルと異なるため停止しました:", file=sys.stderr)
        for it in conflicts:
            print(f"  [conflict] {config_dir / it.rel}", file=sys.stderr)
        raise SystemExit(2)


def _derive_apply(plan, config_dir, force, dry_run):
    """派生定義の計画を適用する共通処理 (profile derive / use / init で再利用)。"""
    if dry_run:
        for it in plan:
            print(f"  [derived {it.state:8}] {config_dir / it.rel}")
            if it.state in ("write", "update", "conflict"):
                target = config_dir / it.rel
                print(store.diff_text_vs(target if target.is_file() else None, it.expected_text, str(target)), end="")
        return 0
    _check_derive_conflicts(plan, config_dir, force)
    for it in plan:
        if it.state == "ok":
            continue
        target = config_dir / it.rel
        if it.state in ("update", "conflict"):
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
    action = "add"
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
            new_text = _refresh_legacy_fastmetal_block(text)
            if new_text is None:
                print(f"provider already configured: {config_file}")
                print("不足するモデル定義があれば配布テンプレートを参照して手動で追加してください。")
                return
            action = "update"
        elif isinstance(providers, dict):
            fail(
                f"{config_file} has a 'providers' key without fastmetal. "
                "auto-merge is not attempted to protect your settings. add the fastmetal block manually "
                "(template: nepoforge/data/templates/provider-fastmetal.jsonc)"
            )
        elif not isinstance(data, dict):
            fail(f"{config_file} is not a JSON object. refusing to edit")
        else:
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
        print(f"[dry-run] would {action} FastMetal provider in: {config_file}")
        print(store.diff_text_vs(config_file if config_file.is_file() else None, new_text, str(config_file)), end="")
        print("dry-run: no changes made")
        return
    backup = store.backup_file(config_dir, config_file.name)
    if backup:
        print(f"  [backup  ] {config_file} -> {backup}")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(new_text, encoding="utf-8")
    past_tense = "updated" if action == "update" else "added"
    print(f"provider {past_tense} (fastmetal): {config_file}")
    print("認証は既存の仕組みを使ってください (例: export FASTMETAL_API_KEY=... )。キーを設定ファイルに書かないこと。")


def _refresh_legacy_fastmetal_block(text: str) -> str | None:
    template = PROVIDER_TEMPLATE.read_text(encoding="utf-8").rstrip("\n")
    for replacement in (template, dedent_two(template)):
        lines = replacement.splitlines()
        first, last = lines[0], lines[-1]
        start = text.find("\n" + first + "\n") + 1
        if not start:
            continue
        end = text.find("\n" + last + "\n", start)
        if end < 0:
            continue
        end += len(last) + 1
        if sha256_text(text[start:end]) in LEGACY_FASTMETAL_BLOCK_HASHES:
            return text[:start] + replacement + text[end:]
    return None


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


def cmd_validate(args: argparse.Namespace) -> int:
    """同梱データ (エージェント・コマンド・プロファイル・テンプレート) を検証する (#11)。

    課金・ネットワークなし。問題が 1 件でもあれば終了コード 1 で失敗する (CI 用)。
    """
    problems = validate.collect_problems()
    if args.json:
        print(json.dumps({"valid": not problems, "problems": problems}, ensure_ascii=False, indent=2))
    elif problems:
        for item in problems:
            print(validate.format_problem(item), file=sys.stderr)
        print(f"validation failed ({len(problems)} problems)", file=sys.stderr)
    else:
        print("bundled data is consistent")
    return 0 if not problems else 1


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
    primary_check = pprofile.add_parser("check-primary", help="Primary の推奨・実行ルート・Data Policy を確認する")
    route_check = pprofile.add_parser("check-route", help="手動起動・委譲前の実行ルートと Data Policy を確認する")
    for check in (primary_check, route_check):
        check.add_argument("--profile", help="照合するプロファイル (既定: 現行)")
        check.add_argument("--model", required=True, help="選択済み provider/model[#variant]")
        check.add_argument("--endpoint", required=True, help="有効設定の送信先 baseURL")
        check.add_argument("--upstream-model", required=True, help="有効設定で提供元へ送る実モデル ID")
        check.add_argument("--data-policy", choices=data_policy.POLICIES, help="プロジェクトより厳格な場合のみ指定")
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

    p = sub.add_parser("plan", help="タスク計画の検証・ready 判定・予算ゲート (課金なし)")
    pplan = p.add_subparsers(dest="plan_command", required=True)
    pvalidate = pplan.add_parser("validate", help="計画 JSON を検証する")
    pvalidate.add_argument("file", help="計画 JSON ({\"tasks\": [...]} または配列)")
    pvalidate.add_argument("--json", action="store_true", help="JSON で出力する")
    pvalidate.set_defaults(fn=cmd_plan)
    pready = pplan.add_parser(
        "ready",
        help="検証・モデル解決・ready 判定・予算ゲートを通過した起動候補を返す",
    )
    pready.add_argument("file", help="計画 JSON ({\"tasks\": [...]} または配列)")
    pready.add_argument("--profile", help="モデル解決に使うプロファイル (既定: 現行)")
    pready.add_argument("--accepted", help="受理済み task_id のカンマ区切り")
    pready.add_argument("--running", help="実行中 task_id のカンマ区切り")
    pready.add_argument("--failed", help="失敗 task_id のカンマ区切り (後続は blocked)")
    for command_parser in (pvalidate, pready):
        command_parser.add_argument("--contract", action="store_true", help="厳格な delegation contract として検証する")
        command_parser.add_argument("--approved-exception", action="append", default=[], metavar="TASK_ID",
                                    help="human_approved 例外への人間の承認入力 (繰返し可)")
    pvalidate.set_defaults(public_strict_options=False)
    pready.add_argument("--max-parallel", type=int, help="並列上限 (既定: プロファイル値)")
    pready.add_argument("--est-input", type=int, default=200_000, help="見積りの既定入力トークン上限 (タスク単位で上書き可)")
    pready.add_argument("--est-output", type=int, help="推論込み出力の見積り (省略時はタスク別規則、規則なしは 50000)")
    pready.add_argument("--retries", type=int, help="見積りに積む自動再試行回数 (既定 1)")
    pready.add_argument("--limit-yen", type=float, help="予算上限 (円)。未指定は未設定として起動を拒否する")
    pready.add_argument("--spent-yen", type=float, default=None, help="実績費用 (円・推定含む)")
    pready.add_argument("--reserved-yen", type=float, default=None, help="実行中タスクの予約額 (円)")
    pready.add_argument("--budget-error", action="store_true", default=None, help="provider 予算超過後の停止モード (全起動を拒否)")
    pready.add_argument("--run-id", help="gate snapshot の run ID (厳格モード必須)")
    pready.add_argument("--gate", help="gate snapshot JSON (厳格モード必須)")
    pready.set_defaults(public_strict_options=False)
    pready.add_argument("--json", action="store_true", help="JSON で出力する")
    pready.set_defaults(fn=cmd_plan)
    preport = pplan.add_parser(
        "report",
        help="確定した gate snapshot から所要時間・費用・予算差の最終報告を生成する (課金なし)",
    )
    preport.add_argument("file", help="計画 JSON ({\"tasks\": [...]})")
    preport.add_argument("--profile", help="モデル解決に使うプロファイル (既定: 現行)")
    preport.add_argument("--limit-yen", type=float, help="承認済み予算上限 (円)。指定時に残額・超過を表示")
    preport.add_argument("--gate", help="gate snapshot JSON (厳格モード必須)")
    preport.add_argument("--run-id", help="gate snapshot の run ID (厳格モード必須)")
    preport.add_argument("--json", action="store_true", help="JSON で出力する")
    preport.add_argument("--contract", action="store_true", help="厳格な delegation contract として検証する")
    preport.add_argument("--approved-exception", action="append", default=[], metavar="TASK_ID",
                         help="human_approved 例外への人間の承認入力 (繰返し可)")
    pvalidate.set_defaults(public_strict_options=False)
    preport.set_defaults(public_strict_options=False)
    preport.set_defaults(fn=cmd_plan)

    p = sub.add_parser("validate", help="同梱データの整合性を検証する (課金なし・CI 用)")
    p.add_argument("--json", action="store_true", help="JSON で出力する")
    p.set_defaults(fn=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except NepoforgeError as exc:
        fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
