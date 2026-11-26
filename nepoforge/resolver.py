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
    def unresolved(reason: str, constraints: tuple[str, ...] = ()) -> ModelResolution:
        return ModelResolution(
            task_id=task.task_id,
            executor=task.executor,
            role=task.role,
            resolved_model=None,
            binding="",
            reason=f"unresolved: {reason}",
            constraints_applied=constraints,
            status="unresolved",
        )

    # human-config は呼び出し側が出所を保証する。計画 JSON の自己申告は使わない。
    if task.model_request is not None and task.model_request_source != "human-config":
        return unresolved("untrusted_source")
    if task.executor not in ("agent", "tool"):
        return unresolved("invalid_executor")
    if task.executor == "tool":
        return ModelResolution(
            task_id=task.task_id,
            executor=task.executor,
            role=task.role,
            resolved_model=None,
            binding="-",
            reason="tool",
        )
    if task.complexity not in ("low", "medium", "high") or task.risk not in ("low", "medium", "high"):
        return unresolved("invalid_complexity_or_risk")

    eligible_refs = {c.ref for c in _eligible(task, config)}
    skips: list[str] = []
    resolved_ref: ModelRef | None = None
    reason = "role_default"
    for i, rule in enumerate(config.task_rules):
        if not _matches(rule.when, task):
            continue
        rule_ref = parse_model(rule.model)
        if rule_ref in eligible_refs:
            resolved_ref = rule_ref
            reason = f"task_rule[{i}]"
            break
        skips.append(f"task_rule[{i}] skipped (model ineligible)")
    if resolved_ref is None and skips:
        return unresolved("task_rule_ineligible", tuple(skips))

    if task.model_request is not None:
        if task.model_request not in eligible_refs:
            return unresolved("explicit_not_eligible")
        if resolved_ref is not None and task.model_request != resolved_ref:
            return unresolved("explicit_conflicts_with_task_rule")
        resolved_ref = task.model_request
        reason = f"explicit({task.model_request_source})"

    default_model = config.roles.get(task.role)
    default_ref = parse_model(default_model) if default_model else None
    if resolved_ref is None:
        if default_ref not in eligible_refs:
            return unresolved("role_default_ineligible")
        resolved_ref = default_ref

    if resolved_ref == default_ref:
        binding = task.role
    else:
        binding = f"nf-{task.role}-{slug_of(resolved_ref)}"

    return ModelResolution(
        task_id=task.task_id,
        executor=task.executor,
        role=task.role,
        resolved_model=resolved_ref,
        binding=binding,
        reason=reason,
        constraints_applied=tuple(skips),
        status="selected",
    )
