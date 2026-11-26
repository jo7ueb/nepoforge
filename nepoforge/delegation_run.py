"""単一 delegation run の snapshot 検証 (#76/#12)。

message は呼び出し側が正規化したローカル観測であり、その入力の真正性を前提
とする。本文を保持せず、改ざん耐性のある監査ログではない。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import resolver
from .planning import PlannedTask, conflicts, ready_tasks
from .profiles import ModelRef, format_model, parse_model


@dataclass(frozen=True)
class RunState:
    accepted: set[str]
    running: set[str]
    failed: set[str]
    retryable: set[str]
    spent_yen: Decimal | None
    reserved_yen: Decimal | None
    provider_budget_error: bool
    report: dict[str, Any]


def _money(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid money")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid money") from None
    if not result.is_finite() or result < 0:
        raise ValueError("invalid money")
    return result


def _model_observation(message: dict, expected: ModelRef) -> tuple[bool, dict]:
    model = message.get("model")
    observed = {}
    if isinstance(model, dict):
        observed = {k: model.get(k) for k in ("providerID", "id", "variant")}
    variant = observed.get("variant")
    wanted_variant = expected.variant or "default"
    actual_variant = variant or "default"
    return (
        observed.get("providerID") == expected.provider
        and observed.get("id") == expected.model
        and actual_variant == wanted_variant,
        observed,
    )


def _row_evidence_valid(tid, row, executors, resolutions, primary_verified=False):
    executor = executors.get(tid)
    if executor == "tool":
        return type(row.get("exit_code")) is int and row["exit_code"] == 0
    if executor == "primary":
        return primary_verified and row.get("success") is True
    resolution = resolutions.get(tid)
    if executor != "agent" or resolution is None or resolution.resolved_model is None:
        return False
    messages = row.get("_messages", [])
    return bool(messages) and row.get("model") == format_model(resolution.resolved_model) and all(
        m.get("agent") == resolution.binding
        and _model_observation(m, resolution.resolved_model)[0] for m in messages
    )


def evaluate_run(
    document: object, *, run_id: str, plan_sha256: str,
    tasks: list[PlannedTask], executors: dict[str, str],
    resolutions: dict[str, resolver.ModelResolution],
    primary_model: str | None,
    retry_limits: dict[str, int] | None = None,
) -> RunState:
    if not isinstance(document, dict):
        raise ValueError("run document must be an object")
    if document.get("run_id") != run_id or document.get("plan_sha256") != plan_sha256:
        raise ValueError("run identity or plan digest mismatch")
    start, as_of = document.get("started_ms"), document.get("as_of_ms")
    if type(start) is not int or type(as_of) is not int or as_of < start:
        raise ValueError("invalid run timestamps")
    primary, attempts = document.get("primary"), document.get("attempts")
    if not isinstance(primary, dict) or not isinstance(attempts, list):
        raise ValueError("invalid run snapshot")
    task_by_id = {task.task_id: task for task in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("duplicate planned task")
    parent_session = primary.get("session_id")
    if not isinstance(parent_session, str) or not parent_session:
        raise ValueError("invalid primary session")
    seen_sessions = {parent_session}
    seen_messages: set[str] = set()

    def messages_for(items: Any, session: str, parent: str | None = None,
                     began: int = start, end: int = as_of) -> list[dict]:
        if not isinstance(items, list):
            raise ValueError("messages must be an array")
        out = []
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not item["id"]):
                raise ValueError("invalid normalized message")
            if item.get("session_id") != session or item["id"] in seen_messages:
                raise ValueError("message session mismatch or duplicate id")
            if parent is not None and item.get("parent_session_id") != parent:
                raise ValueError("message parent session mismatch")
            created = item.get("created_ms")
            if type(created) is not int or created < began or created > end:
                raise ValueError("message timestamp outside evidence window")
            if item["id"] in seen_messages:
                raise ValueError("duplicate message id")
            seen_messages.add(item["id"])
            out.append(item)
        return out

    if primary.get("session_id") != parent_session:
        raise ValueError("invalid primary session")
    pmsgs = messages_for(primary.get("messages"), parent_session, began=start,
                         end=primary.get("ended_ms") if type(primary.get("ended_ms")) is int else as_of)
    primary_cost = _money(primary.get("spent_yen"))
    parent_verified = False
    parent_obs = []
    if primary_model:
        expected_parent = parse_model(primary_model)
        for message in pmsgs:
            if message.get("session_id") == parent_session and isinstance(message.get("agent"), str) and message["agent"]:
                match, observed = _model_observation(message, expected_parent)
                parent_obs.append((match, observed))
        parent_verified = bool(parent_obs) and all(match for match, _ in parent_obs)
    parent_state = primary.get("outcome")
    if parent_state not in ("accepted", "running", "failed"):
        raise ValueError("primary outcome must be explicit")
    if parent_state == "accepted" and (not parent_verified or primary.get("success") is not True):
        parent_state = "failed"
    if parent_state == "running" and primary.get("ended_ms") is not None:
        raise ValueError("running primary must not have ended_ms")
    if parent_state != "running" and type(primary.get("ended_ms")) is not int:
        raise ValueError("ended primary requires ended_ms")
    parent_end = primary.get("ended_ms")
    if parent_end is not None and (parent_end < start or parent_end > as_of):
        raise ValueError("primary end outside run")
    parent_reserved = _money(primary.get("reserved_yen"))
    budget_error = document.get("budget_error", False)
    if not isinstance(budget_error, bool):
        raise ValueError("budget_error must be boolean")

    grouped: dict[str, list[dict]] = {}
    for row in attempts:
        if not isinstance(row, dict):
            raise ValueError("attempt must be an object")
        tid, number, session = row.get("task_id"), row.get("attempt"), row.get("session_id")
        if not isinstance(tid, str) or tid not in task_by_id or type(number) is not int or number not in (1, 2):
            raise ValueError("invalid task attempt")
        shared_primary = executors.get(tid) == "primary" and session == parent_session
        if not isinstance(session, str) or not session or (session in seen_sessions and not shared_primary):
            raise ValueError("duplicate or invalid session")
        if not shared_primary:
            seen_sessions.add(session)
        outcome = row.get("outcome")
        if outcome not in ("running", "failed", "accepted"):
            raise ValueError("invalid outcome")
        began, ended = row.get("started_ms"), row.get("ended_ms")
        if type(began) is not int or began < start or began > as_of:
            raise ValueError("attempt start outside run")
        if (outcome == "running") != (ended is None):
            raise ValueError("running iff ended_ms is null")
        if ended is not None and (type(ended) is not int or ended < began or ended > as_of):
            raise ValueError("attempt end outside run")
        row_messages = (pmsgs if shared_primary else messages_for(
            row.get("messages"), session, parent_session, began=began,
            end=ended if ended is not None else as_of))
        row = dict(row)
        row["_messages"] = row_messages
        grouped.setdefault(tid, []).append(row)
    for tid, rows in grouped.items():
        rows.sort(key=lambda r: r["attempt"])
        if [r["attempt"] for r in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("attempts must be consecutive")
        if len(rows) == 2:
            previous, retry = rows
            if (previous["outcome"] != "failed" or previous["ended_ms"] is None
                    or previous.get("model") != retry.get("model")
                    or previous.get("failure_reason") != "execution_failure"
                    or retry["started_ms"] < previous["ended_ms"]
                    or executors.get(tid) != "agent"
                    or (retry_limits or {}).get(tid, 0) != 1
                    or not _row_evidence_valid(tid, previous, executors, resolutions)
                     ):
                raise ValueError("retry is not an eligible execution-failure retry")

    # Enforce dependency acceptance at launch and reject overlapping write scopes.
    completed_acceptance: dict[str, int] = {}
    for tid, rows in grouped.items():
        # Only verified final acceptance is a dependency completion.
        last = rows[-1]
        if last["outcome"] == "accepted" and _row_evidence_valid(
            tid, last, executors, resolutions, primary_verified=parent_verified
        ):
            completed_acceptance[tid] = last["ended_ms"]
    for tid, rows in grouped.items():
        for row in rows:
            task = task_by_id[tid]
            for dep in task.depends_on:
                accepted_at = completed_acceptance.get(dep)
                if accepted_at is None or accepted_at > row["started_ms"]:
                    raise ValueError("dependency was not accepted before launch")
    intervals = [(r["started_ms"], r["ended_ms"] if r["ended_ms"] is not None else as_of, tid)
                 for tid, rows in grouped.items() for r in rows]
    for i, (a, b, tid) in enumerate(intervals):
        for c, d, other in intervals[i + 1:]:
            if tid != other and a < d and c < b and conflicts(task_by_id[tid], task_by_id[other]):
                raise ValueError("overlapping write scopes")

    accepted: set[str] = set()
    running: set[str] = set()
    failed: set[str] = set()
    retryable: set[str] = set()
    report_rows = []
    child_spent = Decimal(0)
    unknown_child_cost = False
    child_reserved = Decimal(0)
    unknown_reserved = False
    for tid, rows in grouped.items():
        row = rows[-1]
        executor = executors.get(tid)
        status = row["outcome"]
        reason = row.get("failure_reason")
        evidence = "not_required"
        actual = []
        valid = status == "accepted"
        if executor == "tool":
            valid = valid and type(row.get("exit_code")) is int and row["exit_code"] == 0
            if row.get("spent_yen") is not None:
                raise ValueError("tool must not declare paid cost")
        elif executor == "agent":
            resolution = resolutions.get(tid)
            expected = resolution.resolved_model if resolution else None
            if expected is None:
                evidence = "unknown"
                valid = False
            else:
                observed = []
                for message in row["_messages"]:
                    if message.get("agent") != resolution.binding:
                        observed.append((False, {}))
                    else:
                        observed.append(_model_observation(message, expected))
                actual = [item[1] for item in observed]
                evidence = ("unknown" if not observed else
                            "matched" if all(item[0] for item in observed) else "mismatch")
                valid = valid and row.get("model") == format_model(expected) and evidence == "matched"
            cost = _money(row.get("spent_yen"))
            # All paid attempts, including failed/retried attempts.
            for attempt in rows:
                if attempt["outcome"] != "running":
                    amount = _money(attempt.get("spent_yen"))
                    if amount is None:
                        unknown_child_cost = True
                    else:
                        child_spent += amount
        elif executor == "primary":
            # A primary task is covered by the parent accounting; never count it twice.
            valid = valid and parent_verified and row.get("success") is True
        else:
            raise ValueError(f"unknown executor for {tid}")

        if status == "running":
            running.add(tid)
            if executor == "agent":
                reserve = _money(row.get("reserved_yen"))
                if reserve is None:
                    unknown_reserved = True
                else:
                    child_reserved += reserve
        elif valid:
            accepted.add(tid)
        else:
            failed.add(tid)
        if (executor == "agent" and status == "failed" and len(rows) == 1
                and (retry_limits or {}).get(tid, 0) == 1
                and row.get("failure_reason") == "execution_failure"
                and evidence == "matched" and row.get("model") == format_model(resolutions[tid].resolved_model)
                and _money(row.get("spent_yen")) is not None
                and not budget_error):
            retryable.add(tid)
        for attempt in rows:
            report_rows.append({
            "task_id": tid, "attempt": attempt["attempt"], "session_id": attempt["session_id"],
            "model": attempt.get("model"), "decision": "running" if attempt["outcome"] == "running" else
            ("accepted" if attempt is row and valid else "failed"),
            "failure_reason": attempt.get("failure_reason"),
            "started_ms": attempt["started_ms"], "ended_ms": attempt["ended_ms"],
            "cost_yen": (str(_money(attempt.get("spent_yen"))) if attempt.get("spent_yen") is not None else None),
            "evidence": evidence if attempt is row else "recorded",
            "actual_models": actual if attempt is row else [],
        })

    # Include primary only when its execution model and cost evidence are verified.
    unknown_spend = not parent_verified or primary_cost is None or unknown_child_cost
    spent = None if unknown_spend else primary_cost + child_spent
    if parent_state == "running":
        if parent_reserved is None:
            unknown_reserved = True
        else:
            child_reserved += parent_reserved
    reserved_total = None if unknown_reserved else child_reserved
    ready_tasks(tasks, accepted=accepted, running=running, failed=failed)
    overlaps = []
    for i, (a, b, tid) in enumerate(intervals):
        for c, d, other in intervals[i + 1:]:
            if a < d and c < b:
                overlaps.append({"task_ids": [tid, other], "from_ms": max(a, c),
                                 "to_ms": min(b, d), "walltime_ms": max(0, min(b, d)-max(a, c))})
    return RunState(accepted, running, failed, retryable, spent, reserved_total, budget_error, {
        "run_id": run_id, "plan_sha256": plan_sha256,
        "primary": {
            "session_id": parent_session, "model": primary_model,
            "outcome": parent_state, "started_ms": start, "ended_ms": parent_end,
            "evidence": "matched" if parent_verified else "unknown_or_mismatch",
            "actual_models": [observed for _, observed in parent_obs],
        },
        "started_ms": start, "as_of_ms": as_of, "tasks": report_rows,
        "parallel_overlaps": overlaps,
        "walltime_ms": as_of - start,
        "costs": {"total_spent_yen": None if spent is None else str(spent),
                  "primary_spent_yen": str(primary_cost) if parent_verified and primary_cost is not None else None,
                  "children_spent_yen": None if unknown_child_cost else str(child_spent),
                  "reserved_yen": None if reserved_total is None else str(reserved_total)},
        "reservation_note": "reserved_yen includes primary reservation and separate running child attempt reservations",
        "unknown_cost": unknown_spend,
        "unknown_reason": ("primary model/cost evidence unavailable" if not parent_verified or primary_cost is None
                           else "one or more completed agent costs unavailable" if unknown_child_cost else None),
        "provider_budget_error": budget_error,
        "paid_launch_allowed": not unknown_spend and not budget_error,
        "evidence_trust": "normalized local observations are trusted; not tamper-proof",
    })
