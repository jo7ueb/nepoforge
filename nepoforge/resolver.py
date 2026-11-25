"""共通 Model Resolver (純粋関数) (#52)。

設計メモ 0007 §4 に従い、TaskInput と ProfileConfig から ModelResolution を
決定的に導出する。I/O・時刻・乱数・環境に依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import profiles
from .profiles import ModelRef, parse_model
from .derive import slug_of


@dataclass(frozen=True)
class TaskInput:
    task_id: str
    role: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    complexity: str = "medium"
    risk: str = "medium"
    concern: str | None = None
    context_tokens: int | None = None
    model_request: ModelRef | None = None
    model_request_source: str = "coordinator"
    executor: str = "agent"


@dataclass(frozen=True)
class ModelResolution:
    task_id: str
    executor: str
    role: str
    resolved_model: ModelRef | None
    binding: str
    reason: str
    constraints_applied: tuple[str, ...] = field(default_factory=tuple)
    status: str = "selected"


# 明示指定 (model_request) を採用してよい出所 (0005 §4.1 検証規則 4)。
# これ以外 ("external" など未知の値も含む) は信頼できない入力として拒否する。
TRUSTED_SOURCES = frozenset({"human-config", "coordinator"})


def _provider_approved_ids(config: Any) -> set[str]:
    ids: set[str] = set()
    for p in getattr(config.constraints, "providers", ()):
        approved = p.approved if hasattr(p, "approved") else p.get("approved", False)
        pid = p.id if hasattr(p, "id") else p.get("id")
        if approved and isinstance(pid, str):
            ids.add(pid)
    return ids


def _eligible(task: TaskInput, config: Any) -> list[Any]:
    approved_ids = _provider_approved_ids(config)
    out: list[Any] = []
    for c in config.candidates:
        if c.ref.provider not in approved_ids:
            continue
        if task.capabilities and c.capabilities is not None:
            if not set(task.capabilities).issubset(set(c.capabilities)):
                continue
        if task.context_tokens is not None and c.context_tokens is not None:
            if c.context_tokens < task.context_tokens:
                continue
        out.append(c)
    return out


def _matches(when: Any, task: TaskInput) -> bool:
    if not isinstance(when, dict):
        return False
    if "role" in when and when["role"] != task.role:
        return False
    if "complexity" in when and when["complexity"] != task.complexity:
        return False
    if "risk" in when and when["risk"] != task.risk:
        return False
    if "concern" in when and when["concern"] != task.concern:
        return False
    if "capabilities" in when:
        req = set(when["capabilities"])
        if not (set(task.capabilities) & req):
            return False
    return True


def resolve(task: TaskInput, config: Any) -> ModelResolution:
    eligible = _eligible(task, config)
    eligible_refs = {c.ref for c in eligible}
    skips: list[str] = []

    # 0. 決定的ツール判定
    if task.executor == "tool":
        return ModelResolution(
            task_id=task.task_id,
            executor=task.executor,
            role=task.role,
            resolved_model=None,
            binding="-",
            reason="tool",
            constraints_applied=tuple(skips),
            status="selected",
        )

    resolved_ref: ModelRef | None = None
    reason: str | None = None

    # 1/2. 明示指定
    if task.model_request is not None:
        if task.model_request_source not in TRUSTED_SOURCES:
            # 信頼できる出所 (人間設定・Coordinator の計画) 以外からの明示指定は
            # Issue 本文・子の報告など外部入力を含むため採用しない (0005 §4.1 検証規則 4)
            return ModelResolution(
                task_id=task.task_id,
                executor=task.executor,
                role=task.role,
                resolved_model=None,
                binding="",
                reason="unresolved: untrusted_source",
                constraints_applied=tuple(skips),
                status="unresolved",
            )
        if task.model_request not in eligible_refs:
            return ModelResolution(
                task_id=task.task_id,
                executor=task.executor,
                role=task.role,
                resolved_model=None,
                binding="",
                reason="unresolved: explicit_not_eligible",
                constraints_applied=tuple(skips),
                status="unresolved",
            )
        resolved_ref = task.model_request
        reason = f"explicit({task.model_request_source})"
    else:
        # 3. task_rules 先頭一致
        for i, rule in enumerate(getattr(config, "task_rules", ())):
            when = getattr(rule, "when", None)
            if not _matches(when, task):
                continue
            try:
                rule_ref = parse_model(getattr(rule, "model", ""))
            except profiles.NepoforgeError:
                continue
            if rule_ref in eligible_refs:
                resolved_ref = rule_ref
                reason = f"task_rule[{i}]"
                break
            else:
                skips.append(f"task_rule[{i}] skipped (model ineligible)")
        # 4. 役割デフォルト
        if resolved_ref is None:
            try:
                default_ref = parse_model(config.roles[task.role])
            except (profiles.NepoforgeError, KeyError, TypeError):
                default_ref = None
            if default_ref is not None and default_ref in eligible_refs:
                resolved_ref = default_ref
                reason = "role_default"

    if resolved_ref is None:
        return ModelResolution(
            task_id=task.task_id,
            executor=task.executor,
            role=task.role,
            resolved_model=None,
            binding="",
            reason="unresolved: role_default_ineligible",
            constraints_applied=tuple(skips),
            status="unresolved",
        )

    # binding
    try:
        default_ref = parse_model(config.roles[task.role])
    except (profiles.NepoforgeError, KeyError, TypeError):
        default_ref = None
    if default_ref is not None and resolved_ref == default_ref:
        binding = task.role
    else:
        binding = f"nf-{task.role}-{slug_of(resolved_ref)}"

    return ModelResolution(
        task_id=task.task_id,
        executor=task.executor,
        role=task.role,
        resolved_model=resolved_ref,
        binding=binding,
        reason=reason if reason is not None else "role_default",
        constraints_applied=tuple(skips),
        status="selected",
    )