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


# ---------------------------------------------------------------------------
# 設計メモ 0007 §3 / §4.1: モデル参照・候補・プロファイル設定

import json as _json  # noqa: E402

from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

import re as _re  # noqa: E402


@dataclass(frozen=True)
class ModelRef:
    """モデル参照 (provider/model[#variant])。"""

    provider: str
    model: str
    variant: str | None = None

    def __str__(self) -> str:  # 便宜
        return format_model(self)


PARSE_MODEL_RE = _re.compile(r"^([^/#]+)/([^#]+?)(?:#([^#]+))?$")


def parse_model(text: str) -> ModelRef:
    """厳密版のモデル表記検証 (OpenCode スキーマ準拠)。"""
    if not isinstance(text, str) or not text.strip():
        raise NepoforgeError("model notation must be a non-empty string")
    text = text.strip()
    m = PARSE_MODEL_RE.match(text)
    if not m:
        raise NepoforgeError(f"invalid model notation: {text!r}")
    provider, model, variant = m.group(1), m.group(2), m.group(3)
    if not provider or not model:
        raise NepoforgeError(f"invalid model notation: {text!r}")
    if "/" in model or "#" in model:
        raise NepoforgeError(f"invalid model notation: {text!r}")
    # 前後の空白だけを除去し、内部の空白・制御文字は拒否する (#52 監査 W01)。
    # 派生定義の agent_id は小文字の slug を使うため、これらは生成時に
    # はじめて失敗する。設定の読み込み時点で止める。
    for part in (provider, model, variant):
        if part is None:
            continue
        if part != part.strip() or any(ch.isspace() for ch in part) or not part.isprintable():
            raise NepoforgeError(f"model notation contains whitespace or control chars: {text!r}")
    return ModelRef(provider=provider, model=model, variant=variant)


def format_model(ref: ModelRef) -> str:
    base = f"{ref.provider}/{ref.model}"
    if ref.variant:
        return f"{base}#{ref.variant}"
    return base


@dataclass(frozen=True)
class Candidate:
    ref: ModelRef
    capabilities: tuple[str, ...] | None = None
    context_tokens: int | None = None
    destination: str | None = None
    retention: str | None = None
    approval_ref: str | None = None


@dataclass(frozen=True)
class TaskRule:
    when: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    note: str | None = None


@dataclass(frozen=True)
class ProviderConstraint:
    id: str
    destination: str | None = None
    retention: str | None = None
    approved: bool = False
    approval_ref: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default


@dataclass(frozen=True)
class Budget:
    per_task: int | None = None
    note: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default


@dataclass(frozen=True)
class Constraints:
    providers: tuple[ProviderConstraint, ...] = field(default_factory=tuple)
    max_parallel: int = 10
    budget: Budget | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    roles: dict[str, str] = field(default_factory=dict)
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    task_rules: tuple[TaskRule, ...] = field(default_factory=tuple)
    constraints: Constraints = field(default_factory=Constraints)


def _default_candidate_models(name: str) -> set[str]:
    models: set[str] = set()
    for p in profile_dir().glob("*.json"):
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        roles = data.get("roles")
        if isinstance(roles, dict):
            for m in roles.values():
                if isinstance(m, str):
                    models.add(m)
    lp = store.local_profile_path()
    if lp.is_file():
        try:
            data = _json.loads(lp.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                roles = data.get("roles")
                if isinstance(roles, dict):
                    for m in roles.values():
                        if isinstance(m, str):
                            models.add(m)
        except (OSError, ValueError):
            pass
    return models


def load_profile_config(name: str) -> ProfileConfig:
    """プロファイルを読み込み、optional キー (candidates/task_rules/constraints) を検証する。"""
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
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NepoforgeError(f"profile is broken: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise NepoforgeError(f"profile is not an object: {path}")

    # roles (既存 load_profile と同じ検証)
    roles_raw = data.get("roles")
    if not isinstance(roles_raw, dict):
        raise NepoforgeError(f"profile has no 'roles' mapping: {path}")
    missing = [r for r in REQUIRED_ROLES if r not in roles_raw]
    extra = [r for r in roles_raw if r not in REQUIRED_ROLES]
    if missing:
        raise NepoforgeError(f"profile is missing roles: {', '.join(missing)} ({path})")
    if extra:
        raise NepoforgeError(f"profile has unknown roles: {', '.join(extra)} ({path})")
    roles: dict[str, str] = {}
    for role, model in roles_raw.items():
        if not isinstance(model, str) or not MODEL_RE.match(model):
            raise NepoforgeError(f"invalid model notation for '{role}': {model!r} ({path})")
        try:
            parse_model(model)
        except NepoforgeError:
            raise NepoforgeError(f"invalid model notation for '{role}': {model!r} ({path})")
        roles[role] = str(model)

    # constraints (candidates の provider 検証に先だって解析)
    raw_constraints = data.get("constraints", None)
    if raw_constraints is None:
        raw_constraints = {}
    if not isinstance(raw_constraints, dict):
        raise NepoforgeError(f"'constraints' must be an object: {path}")
    raw_providers = raw_constraints.get("providers", None)
    if raw_providers is None:
        raw_providers = [
            {
                "id": "fastmetal",
                "destination": "FastMetal 国内ホスト",
                "retention": "学習利用なし",
                "approved": True,
                "approval_ref": "0001 §3 / #52 PR",
            }
        ]
    if not isinstance(raw_providers, list):
        raise NepoforgeError(f"'constraints.providers' must be an array: {path}")
    providers: list[ProviderConstraint] = []
    for pr in raw_providers:
        if not isinstance(pr, dict):
            raise NepoforgeError(f"provider entry must be an object: {path}")
        pid = pr.get("id")
        if not isinstance(pid, str) or not pid:
            raise NepoforgeError(f"provider 'id' must be a non-empty string: {path}")
        approved = pr.get("approved")
        if not isinstance(approved, bool):
            raise NepoforgeError(f"provider 'approved' must be boolean: {path}")
        destination = pr.get("destination")
        if not isinstance(destination, str):
            raise NepoforgeError(f"provider 'destination' must be a string: {path}")
        retention = pr.get("retention")
        if not isinstance(retention, str):
            raise NepoforgeError(f"provider 'retention' must be a string: {path}")
        approval_ref = pr.get("approval_ref")
        if approval_ref is not None and not isinstance(approval_ref, str):
            raise NepoforgeError(f"provider 'approval_ref' must be a string: {path}")
        providers.append(
            ProviderConstraint(
                id=pid,
                destination=destination,
                retention=retention,
                approved=approved,
                approval_ref=approval_ref,
            )
        )
    approved_ids = {p.id for p in providers if p.approved}

    max_parallel = raw_constraints.get("max_parallel", 10)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int):
        raise NepoforgeError(f"'constraints.max_parallel' must be an integer: {path}")
    if max_parallel < 1:
        raise NepoforgeError(f"'constraints.max_parallel' must be >= 1: {path}")

    raw_budget = raw_constraints.get("budget", None)
    if raw_budget is None:
        raw_budget = {"per_task": None, "note": "#52 では強制しない (#15 で実装)"}
    if not isinstance(raw_budget, dict):
        raise NepoforgeError(f"'constraints.budget' must be an object: {path}")
    per_task = raw_budget.get("per_task", None)
    if per_task is not None and (isinstance(per_task, bool) or not isinstance(per_task, int)):
        raise NepoforgeError(f"'constraints.budget.per_task' must be an integer or null: {path}")
    note = raw_budget.get("note", None)
    if note is not None and not isinstance(note, str):
        raise NepoforgeError(f"'constraints.budget.note' must be a string: {path}")
    budget = Budget(per_task=per_task, note=note)
    constraints = Constraints(providers=tuple(providers), max_parallel=max_parallel, budget=budget)

    # candidates
    raw_candidates = data.get("candidates", None)
    if raw_candidates is not None and not isinstance(raw_candidates, list):
        raise NepoforgeError(f"'candidates' must be an array: {path}")

    candidates: list[Candidate] = []
    candidate_model_strs: set[str] = set()

    if raw_candidates is None:
        for ms in _default_candidate_models(name):
            try:
                ref = parse_model(ms)
            except NepoforgeError:
                continue
            fm = format_model(ref)
            if fm in candidate_model_strs:
                continue
            candidate_model_strs.add(fm)
            candidates.append(
                Candidate(ref=ref, capabilities=None, context_tokens=None, destination=None, retention=None, approval_ref=None)
            )
    else:
        for c in raw_candidates:
            if not isinstance(c, dict):
                raise NepoforgeError(f"candidate entry must be an object: {path}")
            if "model" not in c or not isinstance(c.get("model"), str):
                raise NepoforgeError(f"candidate 'model' must be a string: {path}")
            ref = parse_model(c["model"])
            fm = format_model(ref)
            if fm in candidate_model_strs:
                raise NepoforgeError(f"duplicate candidate model: {fm} ({path})")
            candidate_model_strs.add(fm)
            caps_raw = c.get("capabilities", None)
            caps: tuple[str, ...] | None = None
            if caps_raw is not None:
                if not isinstance(caps_raw, list) or not all(isinstance(x, str) for x in caps_raw):
                    raise NepoforgeError(f"candidate 'capabilities' must be a string array: {path}")
                caps = tuple(caps_raw)
            ct = c.get("context_tokens", None)
            if ct is not None and (isinstance(ct, bool) or not isinstance(ct, int)):
                raise NepoforgeError(f"candidate 'context_tokens' must be an integer or null: {path}")
            dest = c.get("destination", None)
            if dest is not None and not isinstance(dest, str):
                raise NepoforgeError(f"candidate 'destination' must be a string: {path}")
            ret = c.get("retention", None)
            if ret is not None and not isinstance(ret, str):
                raise NepoforgeError(f"candidate 'retention' must be a string: {path}")
            ar = c.get("approval_ref", None)
            if ar is not None and not isinstance(ar, str):
                raise NepoforgeError(f"candidate 'approval_ref' must be a string: {path}")
            candidates.append(
                Candidate(ref=ref, capabilities=caps, context_tokens=ct, destination=dest, retention=ret, approval_ref=ar)
            )

    # roles の値を候補に union
    for role, model in roles.items():
        ref = parse_model(model)
        fm = format_model(ref)
        if fm not in candidate_model_strs:
            candidate_model_strs.add(fm)
            candidates.append(
                Candidate(ref=ref, capabilities=None, context_tokens=None, destination=None, retention=None, approval_ref=None)
            )

    # provider 承認チェック
    for c in candidates:
        if c.ref.provider not in approved_ids:
            raise NepoforgeError(f"candidate provider not approved: {c.ref.provider} ({path})")

    # task_rules
    raw_rules = data.get("task_rules", None)
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise NepoforgeError(f"'task_rules' must be an array: {path}")
    rules: list[TaskRule] = []
    allowed_when = {"role", "capabilities", "complexity", "risk", "concern"}
    for r in raw_rules:
        if not isinstance(r, dict):
            raise NepoforgeError(f"task_rule entry must be an object: {path}")
        when = r.get("when")
        if not isinstance(when, dict) or not when:
            raise NepoforgeError(f"task_rule 'when' must be a non-empty object: {path}")
        if set(when.keys()) - allowed_when:
            raise NepoforgeError(f"task_rule 'when' has unknown keys: {path}")
        if "role" in when and when["role"] not in REQUIRED_ROLES:
            raise NepoforgeError(f"task_rule 'role' must be one of 5 roles: {path}")
        if "complexity" in when and when["complexity"] not in ("low", "medium", "high"):
            raise NepoforgeError(f"task_rule 'complexity' must be low/medium/high: {path}")
        if "risk" in when and when["risk"] not in ("low", "medium", "high"):
            raise NepoforgeError(f"task_rule 'risk' must be low/medium/high: {path}")
        if "capabilities" in when:
            caps = when["capabilities"]
            if not isinstance(caps, list) or not all(isinstance(x, str) for x in caps):
                raise NepoforgeError(f"task_rule 'capabilities' must be a string array: {path}")
        if "concern" in when and not isinstance(when["concern"], str):
            raise NepoforgeError(f"task_rule 'concern' must be a string: {path}")
        model = r.get("model")
        if not isinstance(model, str):
            raise NepoforgeError(f"task_rule 'model' must be a string: {path}")
        try:
            mref = parse_model(model)
        except NepoforgeError:
            raise NepoforgeError(f"task_rule 'model' invalid notation: {model!r} ({path})")
        if format_model(mref) not in candidate_model_strs:
            raise NepoforgeError(f"task_rule 'model' not in candidates: {model} ({path})")
        note = r.get("note", None)
        if note is not None and not isinstance(note, str):
            raise NepoforgeError(f"task_rule 'note' must be a string: {path}")
        rules.append(TaskRule(when=dict(when), model=model, note=note))

    return ProfileConfig(
        name=data.get("name", name) if isinstance(data.get("name"), str) else name,
        roles=roles,
        candidates=tuple(candidates),
        task_rules=tuple(rules),
        constraints=constraints,
    )
