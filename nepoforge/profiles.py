"""モデルプロファイルの読み込み・検証・適用 (#47)。

#6 の scripts/switch_profile.py と同じ分離方針をユーザーレベルに適用したもの:

    - 役割 (role) の定義・プロンプト・権限はエージェント md 側
    - プロファイルは役割→モデルの割当のみを持ち、frontmatter の
      `model:` 行を差し替えるだけで適用される
    - ユーザーの個人上書き (local) は ~/.config/nepoforge/profile.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import store
from .store import NepoforgeError

REQUIRED_ROLES = store.USER_AGENT_IDS
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._#-]*$")
MODEL_LINE_RE = re.compile(r"^model:\s*.*$", re.MULTILINE)


def profile_dir() -> Path:
    return store.DATA_DIR / "profiles"


def available_profiles() -> list[str]:
    names = sorted(p.stem for p in profile_dir().glob("*.json"))
    if store.local_profile_path().is_file():
        names.append("local")
    return names


def load_profile(name: str) -> dict[str, str]:
    """プロファイルを読み込み、役割の過不足・モデル表記を検証する。"""
    if name == "local":
        path = store.local_profile_path()
        if not path.is_file():
            raise NepoforgeError(
                f"local profile not found: {path}. create one to override packaged profiles"
            )
    else:
        path = profile_dir() / f"{name}.json"
        if not path.is_file():
            raise NepoforgeError(
                f"unknown profile '{name}' ({path} not found). "
                f"available: {', '.join(available_profiles())}"
            )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NepoforgeError(f"profile is broken: {path} ({exc})") from exc
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise NepoforgeError(f"profile has no 'roles' mapping: {path}")
    missing = [r for r in REQUIRED_ROLES if r not in roles]
    extra = [r for r in roles if r not in REQUIRED_ROLES]
    if missing:
        raise NepoforgeError(f"profile is missing roles: {', '.join(missing)} ({path})")
    if extra:
        raise NepoforgeError(f"profile has unknown roles: {', '.join(extra)} ({path})")
    for role, model in roles.items():
        if not isinstance(model, str) or not MODEL_RE.match(model):
            raise NepoforgeError(f"invalid model notation for '{role}': {model!r} ({path})")
    return {role: str(model) for role, model in roles.items()}


def apply_to_agent(agent_id: str, text: str, model: str) -> str:
    """エージェント md の frontmatter 内 `model:` 行を差し替える。

    frontmatter がない・model 行がない場合は停止する (黙って追加しない。
    配布物の形式変更を見逃さないため)。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise NepoforgeError(f"agent file has no frontmatter: agents/{agent_id}.md")
    replaced = False
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if MODEL_LINE_RE.match(line):
            lines[i] = f"model: {model}\n"
            replaced = True
    if not replaced:
        raise NepoforgeError(f"agent file has no 'model:' line in frontmatter: agents/{agent_id}.md")
    return "".join(lines)


def render(rel: str, profile: dict[str, str] | None) -> str:
    """配布物 1 ファイルについて、プロファイル適用後の内容を返す。

    profile が None の場合は同梱の内容そのまま。
    """
    text = (store.DATA_DIR / rel).read_text(encoding="utf-8")
    if profile is None or not rel.startswith("agents/"):
        return text
    agent_id = Path(rel).stem
    if agent_id not in profile:
        return text
    return apply_to_agent(agent_id, text, profile[agent_id])
