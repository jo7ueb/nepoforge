"""プロジェクトの承認済み Data Policy と実行ルートの適格性。"""

from __future__ import annotations

import json
from pathlib import Path

from . import store
from .store import NepoforgeError

POLICIES = ("open", "private", "confidential")
DEFAULT = "confidential"


def validate(value: object) -> str:
    if not isinstance(value, str) or value not in POLICIES:
        raise NepoforgeError("invalid data_policy: expected open, private or confidential")
    return value


def effective(project_policy: str, task_policy: str | None) -> str:
    project = validate(project_policy)
    if task_policy is None:
        return project
    task = validate(task_policy)
    if POLICIES.index(task) < POLICIES.index(project):
        raise NepoforgeError("task data_policy cannot relax the approved project policy")
    return task


def permits(policy: str, *, no_training: bool | None, retention: str | None) -> bool:
    policy = validate(policy)
    return policy == "open" or (
        no_training is True and (policy == "private" or retention == "zero")
    )


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError) as exc:
        raise NepoforgeError(f"cannot read Data Policy file: {path}") from exc
    if not isinstance(data, dict):
        raise NepoforgeError(f"Data Policy file must contain a JSON object: {path}")
    return data


def load_project_policy() -> str:
    root = store.find_project_root()
    declared = validate(_read_object(root / ".nepoforge.json").get("data_policy", DEFAULT))
    if declared == DEFAULT:
        return DEFAULT

    # リポジトリに含まれる設定やシンボリックリンクを承認の根拠にしない。
    approvals = store.local_profile_path().with_name("project-policies.json")
    if approvals.is_symlink() or approvals.resolve().is_relative_to(root):
        raise NepoforgeError("Data Policy approvals must be a user file outside the project")
    approved = _read_object(approvals).get(str(root))
    if approved is None or validate(approved) != declared:
        raise NepoforgeError(f"project data_policy '{declared}' is not approved in {approvals}")
    return declared
