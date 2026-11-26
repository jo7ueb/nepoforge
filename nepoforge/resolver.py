"""共通 Model Resolver (純粋関数) (#52)。

設計メモ 0007 §4 に従い、TaskInput と ProfileConfig から ModelResolution を
決定的に導出する。I/O・時刻・乱数・環境に依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from . import data_policy, profiles
from .profiles import ModelRef, parse_model
from .derive import agent_id_for


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
    responsibility: str = "leaf"
    task_kind: str | None = None
    data_policy: str | None = None
    domestic_only: bool = False


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
    reasoning: str | None = None
    max_output_tokens: int | None = None
    data_policy: str | None = None
    endpoint: str | None = None


def approved_endpoint(config: profiles.ProfileConfig, ref: ModelRef) -> str | None:
    # auto 等の可変ルートでは、カタログの属性を実行先に結び付けられない。
    if ref.model == "auto":
        return None
    return next(
        (p.endpoint for p in config.constraints.providers if p.id == ref.provider and p.approved),
        None,
    )


def _eligible(task: TaskInput, config: Any) -> list[Any]:
    out: list[Any] = []
    for c in config.candidates:
        if not data_policy.permits(task.data_policy, no_training=c.no_training, retention=c.retention):
            continue
        if not approved_endpoint(config, c.ref):
            continue
        if not set(task.capabilities).issubset(c.capabilities or ()):
            continue
        if task.context_tokens is not None and (c.context_tokens or 0) < task.context_tokens:
            continue
        if task.domestic_only and c.destination != "japan":
            continue
        out.append(c)
    return out


def _matches(when: Any, task: TaskInput) -> bool:
    if not isinstance(when, dict):
        return False
    for key, expected in when.items():
        if key == "capabilities":
            if not set(task.capabilities).intersection(expected):
                return False
        elif getattr(task, key, None) != expected:
            return False
    return True


def resolve(task: TaskInput, config: Any, *, project_policy: str = data_policy.DEFAULT) -> ModelResolution:
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
    try:
        policy = data_policy.effective(project_policy, task.data_policy)
    except profiles.NepoforgeError as exc:
        return unresolved(str(exc))
    task = replace(task, data_policy=policy)
    if task.executor == "tool":
        return ModelResolution(
            task_id=task.task_id,
            executor=task.executor,
            role=task.role,
            resolved_model=None,
            binding="-",
            reason="tool",
            data_policy=policy,
        )
    if task.complexity not in ("low", "medium", "high") or task.risk not in ("low", "medium", "high"):
        return unresolved("invalid_complexity_or_risk")
    if task.responsibility not in ("leaf", "integration"):
        return unresolved("invalid_responsibility")
    eligible = {c.ref: c for c in _eligible(task, config)}
    skips: list[str] = []
    resolved_ref: ModelRef | None = None
    reason = "role_default"
    max_output_tokens = None
    for i, rule in enumerate(config.task_rules):
        if not _matches(rule.when, task):
            continue
        rule_ref = parse_model(rule.model)
        if rule_ref in eligible:
            resolved_ref = rule_ref
            reason = f"task_rule[{i}]"
            max_output_tokens = rule.max_output_tokens
            break
        skips.append(f"task_rule[{i}] skipped (model ineligible)")
    if resolved_ref is None and skips:
        return unresolved("task_rule_ineligible", tuple(skips))

    if task.model_request is not None:
        if task.model_request not in eligible:
            return unresolved("explicit_not_eligible")
        if resolved_ref is not None and task.model_request != resolved_ref:
            return unresolved("explicit_conflicts_with_task_rule")
        resolved_ref = task.model_request
        reason = f"explicit({task.model_request_source})"

    default_model = config.roles.get(task.role)
    default_ref = parse_model(default_model) if default_model else None
    if resolved_ref is None:
        if default_ref not in eligible:
            return unresolved("role_default_ineligible")
        resolved_ref = default_ref

    if resolved_ref == default_ref:
        binding = task.role
    else:
        binding = agent_id_for(task.role, resolved_ref)

    return ModelResolution(
        task_id=task.task_id,
        executor=task.executor,
        role=task.role,
        resolved_model=resolved_ref,
        binding=binding,
        reason=reason,
        constraints_applied=tuple(skips),
        status="selected",
        reasoning=eligible[resolved_ref].reasoning,
        max_output_tokens=max_output_tokens,
        data_policy=policy,
        endpoint=approved_endpoint(config, resolved_ref),
    )
