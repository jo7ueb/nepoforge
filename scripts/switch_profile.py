#!/usr/bin/env python3
"""モデルプロファイルを .opencode/agents/*.md に適用する (#6)。

役割 (role) の定義とプロンプトは .opencode/agents/<role>.md に置き、
モデル割当だけを profiles/<name>.json から差し替える。役割定義・プロンプト・
権限には一切触れないので、プロンプトとモデル選択は独立に保たれる。

使い方:
    scripts/switch_profile.py [PROFILE] [--check] [--models]

    PROFILE   profiles/ 配下のプロファイル名 (既定: balanced)。
              profiles/local.json を置けば個人の上書きプロファイルとして使える
              (gitignore 済み。グローバル設定 ~/.config/opencode は変更しない)。
    --check   適用せず検証のみ。現在の .opencode がプロファイルと一致しなければ
              非 0 で終了する (CI や事前確認用)。
    --models  利用可能なモデル一覧を表示して終了する。

完了条件 (#6):
    - 3 プロファイル (economy/balanced/performance) の切替が可能
    - 不明モデル・不足役割は明確なエラーになる
    - ユーザーの個人設定 (~/.config/opencode) は保持される
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"
PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "balanced"

REQUIRED_ROLES = ("architect", "expert", "explore", "implementer", "reviewer")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._#-]*$")
MODEL_LINE_RE = re.compile(r"^model:\s*")


class ProfileError(Exception):
    """明確なメッセージを持つ検証エラー。"""


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_profile(name: str) -> dict[str, str]:
    """プロファイルを読み込み、役割の過不足・モデル表記を検証する。"""
    path = PROFILES_DIR / f"{name}.json"
    if not path.is_file():
        available = sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
        fail(
            f"unknown profile '{name}' (profiles/{name}.json not found). "
            f"available: {', '.join(available)}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path}: invalid JSON: {exc}")
    if not isinstance(data, dict):
        fail(f"{path}: top level must be an object")
    roles = data.get("roles")
    if not isinstance(roles, dict):
        fail(f"{path}: 'roles' object is missing")

    unknown = sorted(set(roles) - set(REQUIRED_ROLES))
    if unknown:
        fail(f"{path}: unknown roles: {', '.join(unknown)} (known: {', '.join(REQUIRED_ROLES)})")
    missing = [r for r in REQUIRED_ROLES if r not in roles]
    if missing:
        fail(f"{path}: missing roles: {', '.join(missing)}")

    models: dict[str, str] = {}
    for role in REQUIRED_ROLES:
        model = roles[role]
        if not isinstance(model, str) or not MODEL_RE.match(model):
            fail(f"{path}: {role}: invalid model '{model}' (expected provider/model)")
        models[role] = model
    return models


def available_models() -> set[str] | None:
    """opencode CLI から利用可能なモデル一覧を取得する (無ければ None)。"""
    cli = shutil.which("opencode")
    if not cli:
        return None
    import subprocess

    result = subprocess.run([cli, "models"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def verify_models_exist(models: dict[str, str]) -> None:
    known = available_models()
    if known is None:
        print("warning: opencode CLI not available, skipped model existence check", file=sys.stderr)
        return
    unknown = sorted({m for m in models.values() if m not in known})
    if unknown:
        fail(f"unknown model(s): {', '.join(unknown)} (run 'opencode models' for the list)")


def set_model(agent_path: Path, model: str) -> str | None:
    """エージェント定義の model: 行を差し替える。旧値を返す (無ければ None)。"""
    lines = agent_path.read_text(encoding="utf-8").splitlines(keepends=True)
    previous = None
    replaced = False
    for i, line in enumerate(lines):
        if MODEL_LINE_RE.match(line):
            previous = MODEL_LINE_RE.sub("", line).strip()
            lines[i] = f"model: {model}\n"
            replaced = True
            break
    if not replaced:
        fail(f"{agent_path}: 'model:' frontmatter line not found")
    agent_path.write_text("".join(lines), encoding="utf-8")
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a model profile to .opencode/agents/*.md")
    parser.add_argument("profile", nargs="?", default=DEFAULT_PROFILE, help="profile name (default: balanced)")
    parser.add_argument("--check", action="store_true", help="validate only; exit 1 on drift")
    parser.add_argument("--models", action="store_true", help="list available models and exit")
    args = parser.parse_args()

    if args.models:
        known = available_models()
        if known is None:
            fail("opencode CLI not available")
        for m in sorted(known):
            print(m)
        return

    if (REPO_ROOT / "nepoforge" / "data").is_dir():
        fail("source checkout uses the user-level CLI: nepoforge profile use <profile>")

    models = load_profile(args.profile)
    verify_models_exist(models)

    # 適用前に全エージェント定義の存在を確認する (部分的な適用を起こさない)
    agent_paths = {}
    for role, model in models.items():
        path = AGENTS_DIR / f"{role}.md"
        if not path.is_file():
            fail(f"{path}: agent definition not found")
        agent_paths[role] = path

    changes = []
    for role in REQUIRED_ROLES:
        current = next(
            (MODEL_LINE_RE.sub("", line).strip() for line in agent_paths[role].read_text(encoding="utf-8").splitlines() if MODEL_LINE_RE.match(line)),
            None,
        )
        if current is None:
            fail(f"{agent_paths[role]}: 'model:' frontmatter line not found")
        target = models[role]
        if current == target:
            changes.append((role, None, target))
        else:
            if args.check:
                changes.append((role, current, target))
            else:
                old = set_model(agent_paths[role], target)
                changes.append((role, old, target))

    drift = [(r, o, t) for r, o, t in changes if o is not None]
    for role, old, target in changes:
        if old is None:
            print(f"  {role}: {target} (unchanged)")
        else:
            print(f"  {role}: {old} -> {target}")

    if args.check and drift:
        fail(f"profile '{args.profile}' is not applied ({len(drift)} role(s) differ); run without --check to apply")
    print(f"profile '{args.profile}' {'matches' if args.check else 'applied'}")


if __name__ == "__main__":
    main()
