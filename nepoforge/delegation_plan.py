"""Strict, pure validation for delegation plan contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping

from .planning import (
    PlanError,
    PlannedTask,
    TASK_ROLES,
    READ_ONLY_ROLES,
    normalize_scope,
    validate_plan,
)

STAGES = frozenset(
    {"requirements", "research", "design", "implementation", "test", "review", "integration"}
)
TOOL_KINDS = frozenset({"test", "ci-status", "git", "diagnostics"})
EXECUTORS = frozenset({"agent", "tool", "primary"})
EXCEPTION_REASONS = frozenset({"inseparable", "delegation_cost", "human_approved"})
TASK_FIELDS = frozenset({
    "task_id", "role", "executor", "stage", "issue_ref", "objective", "inputs",
    "expected_output", "acceptance_criteria", "read_scope", "write_scope", "depends_on",
    "deliverables", "task_kind", "exception", "capabilities", "complexity", "risk",
    "concern", "context_tokens", "responsibility", "data_policy", "domestic_only",
    "max_input_tokens", "max_output_tokens", "retries",
})
OPTIONAL_FIELDS = frozenset({
    "task_kind", "exception", "capabilities", "complexity", "risk", "concern",
    "context_tokens", "responsibility", "data_policy", "domestic_only",
    "max_input_tokens", "max_output_tokens", "retries",
})


def _fail(message: str) -> None:
    raise PlanError(message)


def _strings(value: object, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{name} must be a {'non-empty ' if nonempty else ''}list of strings")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _fail(f"{name} must contain non-empty strings")
    if len(set(value)) != len(value):
        _fail(f"{name} must not contain duplicates")
    return value


def validate_contract(
    document: object, *, approved_exceptions: Collection[str] = ()
) -> str:
    """Validate a strict plan contract and return its canonical JSON SHA-256."""
    if not isinstance(document, Mapping):
        _fail("contract must be an object")
    if set(document) != {"schema_version", "issue_ref", "deliverables", "tasks"}:
        _fail("contract has missing or unknown fields")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _fail("schema_version must be 1")
    issue_ref = document["issue_ref"]
    if not isinstance(issue_ref, str) or not issue_ref.strip():
        _fail("issue_ref must be a non-empty string")
    deliverables = _strings(document["deliverables"], "deliverables", nonempty=True)
    tasks = document["tasks"]
    if not isinstance(tasks, list) or not tasks:
        _fail("tasks must be a non-empty list")

    planned: list[PlannedTask] = []
    if not isinstance(approved_exceptions, Collection) or isinstance(approved_exceptions, (str, bytes)):
        _fail("approved_exceptions must be a collection of task IDs")
    if any(not isinstance(item, str) or not item for item in approved_exceptions):
        _fail("approved_exceptions must contain non-empty task IDs")
    claimed: dict[str, int] = {item: 0 for item in deliverables}
    ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, Mapping) or not TASK_FIELDS.issuperset(task):
            _fail("task must be an object with known fields")
        required = TASK_FIELDS - OPTIONAL_FIELDS
        if not required.issubset(task):
            _fail("task is missing required fields")
        task_id, role = task["task_id"], task["role"]
        executor, stage = task["executor"], task["stage"]
        if not isinstance(task_id, str) or not task_id.strip() or task_id in ids:
            _fail("task_id must be unique and non-empty")
        ids.add(task_id)
        if (
            not isinstance(role, str) or role not in TASK_ROLES
            or not isinstance(executor, str) or executor not in EXECUTORS
            or not isinstance(stage, str) or stage not in STAGES
        ):
            _fail(f"{task_id}: invalid role, executor, or stage")
        if task["issue_ref"] != issue_ref:
            _fail(f"{task_id}: issue_ref differs from contract")
        for key in ("objective", "expected_output"):
            if not isinstance(task[key], str) or not task[key].strip():
                _fail(f"{task_id}: {key} must be non-empty")
        for key in ("inputs", "acceptance_criteria", "read_scope", "write_scope", "depends_on"):
            _strings(task[key], f"{task_id}.{key}", nonempty=key == "acceptance_criteria")
        if "model_request" in task:
            _fail(f"{task_id}: model_request is not allowed")
        for key in ("read_scope", "write_scope"):
            for scope in task[key]:
                try:
                    normalize_scope(scope)
                except PlanError as exc:
                    raise PlanError(f"{task_id}: {key}: {exc}") from exc
        capabilities = task.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(x, str) or not x for x in capabilities):
            _fail(f"{task_id}: capabilities must be a list of strings")
        for key in ("complexity", "risk"):
            if key in task and task[key] not in ("low", "medium", "high"):
                _fail(f"{task_id}: invalid {key}")
        if "responsibility" in task and task["responsibility"] not in ("leaf", "integration"):
            _fail(f"{task_id}: invalid responsibility")
        for key in ("concern", "data_policy"):
            if key in task and task[key] is not None and not isinstance(task[key], str):
                _fail(f"{task_id}: {key} must be a string")
        if "domestic_only" in task and type(task["domestic_only"]) is not bool:
            _fail(f"{task_id}: domestic_only must be boolean")
        for key in ("context_tokens", "max_input_tokens", "max_output_tokens"):
            if key in task and (type(task[key]) is not int or task[key] < 1):
                _fail(f"{task_id}: {key} must be a positive integer")
        if "retries" in task and (type(task["retries"]) is not int or task["retries"] < 0 or task["retries"] > 1):
            _fail(f"{task_id}: retries must be 0 or 1")
        owned = _strings(task["deliverables"], f"{task_id}.deliverables", nonempty=True)
        for item in owned:
            if item not in claimed:
                _fail(f"{task_id}: unknown deliverable {item}")
            claimed[item] += 1

        writes = task["write_scope"]
        if role in READ_ONLY_ROLES and writes:
            _fail(f"{task_id}: read-only role cannot write")
        exception = task.get("exception")
        if executor == "primary":
            if not isinstance(exception, Mapping) or set(exception) != {"reason", "detail", "scope"}:
                _fail(f"{task_id}: primary requires a complete exception")
            if not isinstance(exception["reason"], str) or exception["reason"] not in EXCEPTION_REASONS:
                _fail(f"{task_id}: invalid exception reason")
            if not isinstance(exception["detail"], str) or not exception["detail"].strip():
                _fail(f"{task_id}: exception detail is required")
            if exception["scope"] != writes:
                _fail(f"{task_id}: exception scope must equal write_scope")
            if exception["reason"] == "human_approved" and task_id not in approved_exceptions:
                _fail(f"{task_id}: human approval required")
        elif "exception" in task:
            _fail(f"{task_id}: exceptions are only valid for primary")

        if executor == "tool":
            kind = task.get("task_kind")
            if not isinstance(kind, str) or kind not in TOOL_KINDS:
                _fail(f"{task_id}: tool requires an allowed task_kind")
            if writes:
                _fail(f"{task_id}: tool write_scope must be empty")
        elif "task_kind" in task and task["task_kind"] is not None:
            # A tool's allowlist is not a free-form agent routing mechanism.
            if not isinstance(task["task_kind"], str):
                _fail(f"{task_id}: task_kind must be a string")

        planned.append(PlannedTask(task_id, role, tuple(task["depends_on"]), tuple(writes)))

    if any(count != 1 for count in claimed.values()):
        _fail("each declared deliverable must be assigned to exactly one task")
    if set(approved_exceptions) - ids:
        _fail("approved_exceptions references an unknown task")
    if any(
        task.get("task_id") in approved_exceptions
        and (task.get("executor") != "primary"
             or not isinstance(task.get("exception"), Mapping)
             or task["exception"].get("reason") != "human_approved")
        for task in tasks
    ):
        _fail("approved_exceptions references a task without human_approved exception")
    if any(dep not in ids for task in planned for dep in task.depends_on):
        _fail("depends_on references an unknown task")
    validate_plan(planned)
    try:
        canonical = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PlanError("contract is not JSON serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
