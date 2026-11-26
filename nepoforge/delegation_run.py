"""単一 delegation run の snapshot 検証 (#76/#12)。

message は呼び出し側が正規化したローカル観測であり、その入力の真正性を前提
とする。本文を保持せず、改ざん耐性のある監査ログではない。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from . import resolver
from .budget import PRICE_BASIS
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


def _runtime_report(guard: Any) -> dict[str, Any]:
    """本文を含めず、RuntimeGuardの観測状態をJSON互換に複写する。"""
    if guard is None:
        return {}
    sessions = []
    snapshots = getattr(guard, "snapshots", {})
    if isinstance(snapshots, dict):
        for session_id, snapshot in sorted(snapshots.items()):
            messages = getattr(snapshot, "messages", {})
            sessions.append({
                "session_id": str(session_id),
                "message_count": len(messages) if isinstance(messages, dict) else None,
                "input_tokens": getattr(snapshot, "input_tokens", None),
                "output_tokens": getattr(snapshot, "output_tokens", None),
                "reasoning_tokens": getattr(snapshot, "reasoning_tokens", None),
                "cache_read_tokens": getattr(snapshot, "cache_read_tokens", None),
                "cache_write_tokens": getattr(snapshot, "cache_write_tokens", None),
                "last_observed_ms": getattr(snapshot, "last_observed_ms", None),
                "spent_yen": _json_value(getattr(snapshot, "spent_yen", None)),
                "reserved_yen": _json_value(getattr(snapshot, "reserved_yen", None)),
            })
    active = sorted(getattr(guard, "_active_sessions", set()))
    requests = [{"session_id": sid, "reason": getattr(guard, "latch_reason", None)}
                for sid in active] if getattr(guard, "is_latched", False) else []
    observed_sessions = sum(
        getattr(snapshot, "last_observed_ms", None) is not None
        for snapshot in snapshots.values()
    ) if isinstance(snapshots, dict) else 0
    observation_status = (
        "stopped" if getattr(guard, "is_latched", False) else
        "unavailable" if not sessions else
        "missing" if not observed_sessions else
        "observed" if observed_sessions == len(sessions) else "partial"
    )
    return {
        "guard_state": {
            "latched": bool(getattr(guard, "is_latched", False)),
            "stop_reason": getattr(guard, "latch_reason", None),
            "limit_yen": _json_value(getattr(guard, "limit_yen", None)),
            "spent_yen": _json_value(getattr(guard, "spent_yen", None)),
            "reserved_yen": _json_value(getattr(guard, "reserved_yen", None)),
        },
        "sessions": sessions,
        "stop_requests": requests,
        "observation_status": observation_status,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


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
    runtime_guard: Any = None,
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
    report = {
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
    }
    runtime = _runtime_report(runtime_guard)
    if runtime:
        report["runtime"] = runtime
    return RunState(accepted, running, failed, retryable, spent, reserved_total, budget_error, report)


def _walltime_text(ms: int) -> str:
    """壁時計時間の表示 (例: 2分34秒)。"""
    seconds = ms // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}時間")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def summarize_report(report: dict) -> dict:
    """run_report を最終報告用の要約に変換する (#12)。

    正常・停止・失敗のすべての終了状態で使える。費用不明を 0 円と偽らず
    "unknown" で示す。金額は gate 申告と単価表に基づく推定であり、
    請求実額ではない (価格基準日は budget.PRICE_BASIS)。
    """
    if not isinstance(report, dict):
        raise ValueError("run report must be an object")
    walltime = report.get("walltime_ms")
    if type(walltime) is not int or walltime < 0:
        raise ValueError("invalid walltime_ms")
    costs = report.get("costs") or {}
    if not isinstance(costs, dict):
        raise ValueError("invalid costs block")

    def label(value: Any) -> str:
        money = _money(value)
        return "unknown" if money is None else str(money)

    total_unknown = costs.get("total_spent_yen") is None
    cost = {
        "state": "unknown" if total_unknown else "estimated",
        "total": label(costs.get("total_spent_yen")),
        "primary": label(costs.get("primary_spent_yen")),
        "children": label(costs.get("children_spent_yen")),
        "reserved": label(costs.get("reserved_yen")),
        "reason": report.get("unknown_reason") if total_unknown else None,
        "basis": f"推定・税別・価格基準日 {PRICE_BASIS}・請求実額ではない",
    }

    budget_block = report.get("budget")
    budget_summary = None
    if isinstance(budget_block, dict) and budget_block.get("limit_yen") is not None:
        limit = _money(budget_block.get("limit_yen"))
        spent = _money(costs.get("total_spent_yen"))
        reserved = _money(costs.get("reserved_yen"))
        if limit is None or spent is None or reserved is None:
            remaining = "unknown"
            over = None
        else:
            rest = limit - spent - reserved
            remaining = str(rest)
            over = rest < 0
        budget_summary = {
            "limit_yen": str(limit),
            "remaining_yen": remaining,
            "over": over,
        }

    primary = report.get("primary") or {}
    summary = {
        "walltime_ms": walltime,
        "walltime_text": _walltime_text(walltime),
        "outcome": primary.get("outcome"),
        "cost": cost,
        "budget": budget_summary,
    }
    runtime = report.get("runtime")
    if isinstance(runtime, dict):
        summary["runtime"] = runtime
    return summary
