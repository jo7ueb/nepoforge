"""実行中のbest-effort費用・反復失敗ガード (#12/#15/#61)。

OpenCode側のイベント/APIアダプターは本文を除いた MessageUsage を供給し、
停止時は InterruptDispatcher で session.abort 相当を要求する。
金額は予算ゲートの価格表による推定であり、請求上限ではない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from . import budget


class InterruptDispatcher(Protocol):
    """OpenCode session.abort を呼び出すアダプター。"""

    def interrupt_session(self, session_id: str) -> None: ...


@dataclass(frozen=True)
class MessageUsage:
    """OpenCode message/session API から本文を除いて正規化した使用量。"""

    id: str
    session_id: str
    model: str
    created_ms: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Provider側の記録値は通貨/条件が異なるため、円換算と混同しない。
    reported_cost: Decimal | None = None
    estimated_cost_yen: Decimal | None = None


@dataclass
class UsageSnapshot:
    """sessionごとの重複排除済み累積。"""

    messages: dict[str, MessageUsage] = field(default_factory=dict)
    reserved_yen: Decimal = Decimal(0)
    last_observed_ms: int | None = None

    @property
    def input_tokens(self) -> int:
        return sum(m.input_tokens for m in self.messages.values())

    @property
    def output_tokens(self) -> int:
        return sum(m.output_tokens for m in self.messages.values())

    @property
    def reasoning_tokens(self) -> int:
        return sum(m.reasoning_tokens for m in self.messages.values())

    @property
    def cache_read_tokens(self) -> int:
        return sum(m.cache_read_tokens for m in self.messages.values())

    @property
    def cache_write_tokens(self) -> int:
        return sum(m.cache_write_tokens for m in self.messages.values())

    @property
    def spent_yen(self) -> Decimal:
        return sum(
            (m.estimated_cost_yen for m in self.messages.values()
             if m.estimated_cost_yen is not None),
            Decimal(0),
        )


class RuntimeGuard:
    """単一runの停止ラッチ。観測異常・予算超過・反復失敗後は解除しない。"""

    def __init__(
        self,
        limit_yen: Decimal | None,
        *,
        failure_threshold: int = 3,
        dispatcher: InterruptDispatcher | None = None,
        enforce_limit: bool = True,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if limit_yen is not None:
            try:
                limit_yen = Decimal(str(limit_yen))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError("budget limit must be a finite non-negative amount") from None
            if not limit_yen.is_finite() or limit_yen < 0:
                raise ValueError("budget limit must be a finite non-negative amount")
        self.limit_yen = limit_yen
        self.failure_threshold = failure_threshold
        self.dispatcher = dispatcher
        self.enforce_limit = enforce_limit
        self.accounting_complete = True
        self.snapshots: dict[str, UsageSnapshot] = {}
        self._launch_reserved_yen = Decimal(0)
        self._active_sessions: set[str] = set()
        self._fingerprints: dict[str, int] = {}
        self.interrupt_results: dict[str, str] = {}
        self.is_latched = False
        self.latch_reason: str | None = None

    @property
    def spent_yen(self) -> Decimal:
        return sum((s.spent_yen for s in self.snapshots.values()), Decimal(0))

    @property
    def reserved_yen(self) -> Decimal:
        return (
            sum((s.reserved_yen for s in self.snapshots.values()), Decimal(0))
            + self._launch_reserved_yen
        )

    def reserve_launch(self, estimate_yen: Decimal) -> tuple[bool, str]:
        """strict ready が許可した新規起動額を同一バッチ内で予約する。"""
        try:
            amount = Decimal(str(estimate_yen))
        except (InvalidOperation, TypeError, ValueError):
            self._latch("invalid launch estimate")
            return False, self.latch_reason or "invalid launch estimate"
        if not amount.is_finite() or amount <= 0:
            self._latch("missing or non-positive launch estimate")
            return False, self.latch_reason or "invalid launch estimate"
        allowed, reason = self.can_launch(amount)
        if not allowed:
            return False, reason
        self._launch_reserved_yen += amount
        return True, ""

    def register_session(self, session_id: str) -> None:
        """usage初回到着前に親・子sessionを監視対象に登録する。"""
        if not session_id:
            self._latch("missing session id")
            return
        self._active_sessions.add(session_id)
        self.snapshots.setdefault(session_id, UsageSnapshot())

    def reserve_request(self, session_id: str, estimate_yen: Decimal) -> tuple[bool, str]:
        """送信前に要求の保守的見積りを予約し、既存予約は置換せず累積する。"""
        self.register_session(session_id)
        try:
            estimate = Decimal(str(estimate_yen))
        except (InvalidOperation, TypeError, ValueError):
            self._latch("missing or invalid in-flight estimate")
            return False, self.latch_reason or "invalid estimate"
        if not estimate.is_finite() or estimate <= 0:
            self._latch("missing or non-positive in-flight estimate")
            return False, self.latch_reason or "invalid estimate"
        allowed, reason = self.can_launch(estimate)
        if not allowed:
            return False, reason
        self.snapshots[session_id].reserved_yen += estimate
        return True, ""

    def settle_request(self, session_id: str, reservation_yen: Decimal) -> None:
        """完了後に当該要求の予約分だけ精算する。"""
        snapshot = self.snapshots.get(session_id)
        try:
            amount = Decimal(str(reservation_yen))
        except (InvalidOperation, TypeError, ValueError):
            self._latch("reservation settlement mismatch")
            return
        if (snapshot is None or not amount.is_finite() or amount < 0
                or snapshot.reserved_yen < amount):
            self._latch("reservation settlement mismatch")
            return
        snapshot.reserved_yen -= amount

    def update_usage(self, session_id: str, messages: list[MessageUsage]) -> None:
        """messageの新規分だけ累積し、重複/欠損/価格不明を fail-closed 扱いする。"""
        self.register_session(session_id)
        snapshot = self.snapshots[session_id]
        for raw in messages:
            if raw.session_id != session_id:
                self.accounting_complete = False
                self._latch("session id mismatch")
                return
            if (
                not isinstance(raw.id, str) or not raw.id
                or not isinstance(raw.model, str) or not raw.model
                or type(raw.created_ms) is not int or raw.created_ms < 0
            ):
                self.accounting_complete = False
                self._latch("invalid message identity or timestamp")
                return
            token_counts = (
                raw.input_tokens, raw.output_tokens, raw.reasoning_tokens,
                raw.cache_read_tokens, raw.cache_write_tokens,
            )
            if any(type(value) is not int or value < 0 for value in token_counts):
                self.accounting_complete = False
                self._latch("invalid token count")
                return
            previous = snapshot.messages.get(raw.id)
            if previous is not None:
                # The guard may have filled estimated_cost_yen after the first
                # observation; compare the provider/source record, not that derived field.
                comparable = MessageUsage(
                    **{**raw.__dict__, "estimated_cost_yen": previous.estimated_cost_yen}
                )
                if previous != comparable:
                    self.accounting_complete = False
                    self._latch("conflicting duplicate message id")
                    return
                continue

            # cost=0/None is not proof of free usage. Re-price known token usage
            # using the established (dated) table; unknown model/cost fails closed.
            rates = budget.price_for(raw.model)
            if rates is None:
                self.accounting_complete = False
                self._latch(f"unknown price for model: {raw.model}")
                return
            if budget.expired_price(raw.model):
                self.accounting_complete = False
                self._latch(f"expired price for model: {raw.model}")
                return
            if raw.cache_write_tokens:
                self.accounting_complete = False
                self._latch("cache-write price unknown")
                return
            estimated = raw.estimated_cost_yen
            if estimated is None:
                input_rate, output_rate = (Decimal(str(rate)) for rate in rates)
                estimated = (
                    Decimal(raw.input_tokens + raw.cache_read_tokens) * input_rate
                    + Decimal(raw.output_tokens + raw.reasoning_tokens) * output_rate
                ) / Decimal(1_000_000)
            else:
                try:
                    estimated = Decimal(str(estimated))
                except (InvalidOperation, TypeError, ValueError):
                    self.accounting_complete = False
                    self._latch("invalid estimated cost")
                    return
            if estimated < 0:
                self.accounting_complete = False
                self._latch("negative estimated cost")
                return
            if (raw.input_tokens + raw.output_tokens + raw.reasoning_tokens
                    + raw.cache_read_tokens + raw.cache_write_tokens > 0
                    and estimated == 0):
                self.accounting_complete = False
                self._latch("non-zero usage has zero estimated cost")
                return
            snapshot.messages[raw.id] = MessageUsage(
                **{**raw.__dict__, "estimated_cost_yen": estimated}
            )
            snapshot.last_observed_ms = max(snapshot.last_observed_ms or 0, raw.created_ms)
            if self.enforce_limit:
                allowed, reason = self.can_launch(Decimal(0))
                if not allowed:
                    self._latch(reason)
                    return

    def record_failure(self, fingerprint: str) -> bool:
        """匿名fingerprintの失敗を記録。read/tool変更ではカウンタを解除しない。"""
        if not fingerprint:
            self._latch("missing failure fingerprint")
            return True
        count = self._fingerprints.get(fingerprint, 0) + 1
        self._fingerprints[fingerprint] = count
        if count >= self.failure_threshold:
            self._latch(f"repeated failure threshold reached: {fingerprint}")
            return True
        return False

    def record_tool_result(
        self, session_id: str, tool_name: str, *, status: str, error: str | None = None,
    ) -> bool:
        """tool失敗を匿名fingerprint化。成功read等は失敗カウンタを解除しない。"""
        self.register_session(session_id)
        if status not in {"error", "failed"}:
            return self.is_latched
        text = (error or "").lower()
        if "budget has been exceeded" in text:
            self._latch("provider budget error")
            return True
        if "oldstring" in text or "old string" in text:
            kind = "oldstring-mismatch"
        elif "permission" in text or "denied" in text:
            kind = "permission-denied"
        elif "invalid argument" in text:
            kind = "invalid-arguments"
        elif "timeout" in text or "connection" in text:
            kind = "transport-error"
        else:
            kind = "tool-error"
        # tool/read/rename changes do not erase or bypass the same failure class.
        return self.record_failure(kind)

    def observation_lost(self, reason: str = "usage monitor lost") -> None:
        """イベント/API収集が切断した場合の停止通知。"""
        self._latch(reason)

    def observe_api_messages(self, session_id: str, messages: list[dict[str, Any]]) -> bool:
        """OpenCode message API response (`data[]`) を本文なしで取り込み、欠損時は停止。"""
        try:
            normalized = [message_usage_from_api(session_id, item) for item in messages]
        except (KeyError, TypeError, ValueError):
            self.accounting_complete = False
            self._latch("incomplete or invalid OpenCode usage observation")
            return False
        self.update_usage(session_id, normalized)
        snapshot = self.snapshots[session_id]
        return all(item.id in snapshot.messages for item in normalized)

    def register_reservation(self, session_id: str, amount_yen: Decimal) -> None:
        """最新run snapshotに記録された起動済み予約を復元する。"""
        self.register_session(session_id)
        try:
            amount = Decimal(str(amount_yen))
        except (InvalidOperation, TypeError, ValueError):
            self._latch("invalid existing reservation")
            return
        if not amount.is_finite() or amount < 0:
            self._latch("invalid existing reservation")
            return
        self.snapshots[session_id].reserved_yen += amount

    def stop(self, reason: str) -> None:
        """観測済みの外部停止条件をrun guardへ反映する。"""
        self._latch(reason or "run stopped")

    def record_observation_failure(self, reason: str) -> None:
        """API観測・ページング等が完了しなかった場合に停止し会計をunknownにする。"""
        self.accounting_complete = False
        self.observation_lost(reason)

    def can_launch(self, estimate_yen: Decimal | None = None) -> tuple[bool, str]:
        """新規送信/retry直前に上限・現時点の予約・次要求見積りを検査。"""
        if self.is_latched:
            return False, self.latch_reason or "run is latched"
        if self.limit_yen is None:
            self._latch("budget limit is not set (fail-closed)")
            return False, self.latch_reason or "budget limit is not set"
        try:
            estimate = Decimal(str(estimate_yen)) if estimate_yen is not None else None
        except (InvalidOperation, TypeError, ValueError):
            self._latch("next request estimate is invalid (fail-closed)")
            return False, self.latch_reason or "next request estimate is invalid"
        if estimate is None or not estimate.is_finite() or estimate < 0:
            self._latch("next request estimate is unknown (fail-closed)")
            return False, self.latch_reason or "next request estimate is unknown"
        projected = self.spent_yen + self.reserved_yen + estimate
        if projected > self.limit_yen:
            reason = f"budget exceeded: projected {projected} > {self.limit_yen}"
            self._latch(reason)
            return False, reason
        return True, ""

    def _latch(self, reason: str) -> None:
        if self.is_latched:
            return
        self.is_latched = True
        self.latch_reason = reason
        for session_id in sorted(self._active_sessions):
            if self.dispatcher is None:
                self.interrupt_results[session_id] = "not_configured"
                continue
            try:
                self.dispatcher.interrupt_session(session_id)
            except Exception:
                # One failed request must not prevent dispatch to other sessions.
                self.interrupt_results[session_id] = "error"
            else:
                # Dispatcher acceptance is not proof that OpenCode stopped the run.
                self.interrupt_results[session_id] = "requested"


def message_usage_from_api(session_id: str, message: dict[str, Any]) -> MessageUsage:
    """OpenCode v2 message recordをusageに正規化。本文やpartsは保持しない。"""
    info = message.get("info", message)
    if not isinstance(info, dict):
        raise ValueError("invalid message info")
    model = info["model"]
    tokens = info["tokens"]
    if not isinstance(model, dict) or not isinstance(tokens, dict):
        raise ValueError("invalid model or token usage")
    cache = tokens.get("cache") or {}
    if not isinstance(cache, dict):
        raise ValueError("invalid cache usage")
    when = info.get("time") or {}
    if not isinstance(when, dict):
        raise ValueError("invalid message time")
    created_ms = when.get("created", info.get("created_ms"))
    if type(created_ms) is not int:
        raise ValueError("missing message timestamp")
    if (
        not isinstance(info.get("id"), str) or not info["id"]
        or not isinstance(session_id, str) or not session_id
        or not isinstance(model.get("providerID"), str) or not model["providerID"]
        or not isinstance(model.get("id"), str) or not model["id"]
    ):
        raise ValueError("invalid message or model identity")
    token_values = {
        "input": tokens.get("input"),
        "output": tokens.get("output"),
        "reasoning": tokens.get("reasoning", 0),
        "cache_read": cache.get("read", 0),
        "cache_write": cache.get("write", 0),
    }
    if any(type(value) is not int or value < 0 for value in token_values.values()):
        raise ValueError("invalid token counts")
    variant = model.get("variant")
    if variant is not None and not isinstance(variant, str):
        raise ValueError("invalid model variant")
    reported_cost = None
    if info.get("cost") is not None:
        try:
            reported_cost = Decimal(str(info["cost"]))
        except (InvalidOperation, ValueError):
            raise ValueError("invalid provider-reported cost") from None
        if not reported_cost.is_finite() or reported_cost < 0:
            raise ValueError("invalid provider-reported cost")
    model_id = f"{model['providerID']}/{model['id']}"
    if model.get("variant"):
        model_id += f"#{model['variant']}"
    return MessageUsage(
        id=info["id"],
        session_id=session_id,
        model=model_id,
        created_ms=created_ms,
        input_tokens=token_values["input"],
        output_tokens=token_values["output"],
        reasoning_tokens=token_values["reasoning"],
        cache_read_tokens=token_values["cache_read"],
        cache_write_tokens=token_values["cache_write"],
        reported_cost=reported_cost,
    )
