"""実行中のbest-effort費用・反復失敗ガード (#12/#15/#61)。

OpenCode側のイベント/APIアダプターは本文を除いた MessageUsage を供給し、
停止時は InterruptDispatcher で session.abort 相当を要求する。
金額は予算ゲートの価格表による推定であり、請求上限ではない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
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
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.limit_yen = limit_yen
        self.failure_threshold = failure_threshold
        self.dispatcher = dispatcher
        self.snapshots: dict[str, UsageSnapshot] = {}
        self._active_sessions: set[str] = set()
        self._fingerprints: dict[str, int] = {}
        self.is_latched = False
        self.latch_reason: str | None = None

    @property
    def spent_yen(self) -> Decimal:
        return sum((s.spent_yen for s in self.snapshots.values()), Decimal(0))

    @property
    def reserved_yen(self) -> Decimal:
        return sum((s.reserved_yen for s in self.snapshots.values()), Decimal(0))

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
        if estimate_yen <= 0:
            self._latch("missing or non-positive in-flight estimate")
            return False, self.latch_reason or "invalid estimate"
        allowed, reason = self.can_launch(estimate_yen)
        if not allowed:
            return False, reason
        self.snapshots[session_id].reserved_yen += estimate_yen
        return True, ""

    def settle_request(self, session_id: str, reservation_yen: Decimal) -> None:
        """完了後に当該要求の予約分だけ精算する。"""
        snapshot = self.snapshots.get(session_id)
        if snapshot is None or reservation_yen < 0 or snapshot.reserved_yen < reservation_yen:
            self._latch("reservation settlement mismatch")
            return
        snapshot.reserved_yen -= reservation_yen

    def update_usage(self, session_id: str, messages: list[MessageUsage]) -> None:
        """messageの新規分だけ累積し、重複/欠損/価格不明を fail-closed 扱いする。"""
        self.register_session(session_id)
        snapshot = self.snapshots[session_id]
        for raw in messages:
            if raw.session_id != session_id:
                self._latch("session id mismatch")
                return
            if not raw.id or raw.created_ms < 0:
                self._latch("invalid message identity or timestamp")
                return
            if min(
                raw.input_tokens, raw.output_tokens, raw.reasoning_tokens,
                raw.cache_read_tokens, raw.cache_write_tokens,
            ) < 0:
                self._latch("negative token count")
                return
            previous = snapshot.messages.get(raw.id)
            if previous is not None:
                # The guard may have filled estimated_cost_yen after the first
                # observation; compare the provider/source record, not that derived field.
                comparable = MessageUsage(
                    **{**raw.__dict__, "estimated_cost_yen": previous.estimated_cost_yen}
                )
                if previous != comparable:
                    self._latch("conflicting duplicate message id")
                    return
                continue

            # cost=0/None is not proof of free usage. Re-price known token usage
            # using the established (dated) table; unknown model/cost fails closed.
            rates = budget.price_for(raw.model)
            if rates is None:
                self._latch(f"unknown price for model: {raw.model}")
                return
            if budget.expired_price(raw.model):
                self._latch(f"expired price for model: {raw.model}")
                return
            if raw.cache_write_tokens:
                self._latch("cache-write price unknown")
                return
            estimated = raw.estimated_cost_yen
            if estimated is None:
                input_rate, output_rate = (Decimal(str(rate)) for rate in rates)
                estimated = (
                    Decimal(raw.input_tokens + raw.cache_read_tokens) * input_rate
                    + Decimal(raw.output_tokens + raw.reasoning_tokens) * output_rate
                ) / Decimal(1_000_000)
            if estimated < 0:
                self._latch("negative estimated cost")
                return
            if (raw.input_tokens + raw.output_tokens + raw.reasoning_tokens
                    + raw.cache_read_tokens + raw.cache_write_tokens > 0
                    and estimated == 0):
                self._latch("non-zero usage has zero estimated cost")
                return
            snapshot.messages[raw.id] = MessageUsage(
                **{**raw.__dict__, "estimated_cost_yen": estimated}
            )
            snapshot.last_observed_ms = max(snapshot.last_observed_ms or 0, raw.created_ms)
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

    def observe_api_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """OpenCode message API response (`data[]`) を本文なしで取り込み、欠損時は停止。"""
        try:
            normalized = [message_usage_from_api(session_id, item) for item in messages]
        except (KeyError, TypeError, ValueError):
            self._latch("incomplete or invalid OpenCode usage observation")
            return
        self.update_usage(session_id, normalized)

    def can_launch(self, estimate_yen: Decimal | None = None) -> tuple[bool, str]:
        """新規送信/retry直前に上限・現時点の予約・次要求見積りを検査。"""
        if self.is_latched:
            return False, self.latch_reason or "run is latched"
        if self.limit_yen is None:
            self._latch("budget limit is not set (fail-closed)")
            return False, self.latch_reason or "budget limit is not set"
        if estimate_yen is None or estimate_yen < 0:
            self._latch("next request estimate is unknown (fail-closed)")
            return False, self.latch_reason or "next request estimate is unknown"
        projected = self.spent_yen + self.reserved_yen + estimate_yen
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
        if self.dispatcher is not None:
            for session_id in sorted(self._active_sessions):
                self.dispatcher.interrupt_session(session_id)


def message_usage_from_api(session_id: str, message: dict[str, Any]) -> MessageUsage:
    """OpenCode v2 message recordをusageに正規化。本文やpartsは保持しない。"""
    info = message.get("info", message)
    model = info["model"]
    tokens = info["tokens"]
    cache = tokens.get("cache") or {}
    when = info.get("time") or {}
    created_ms = when.get("created", info.get("created_ms"))
    if type(created_ms) is not int:
        raise ValueError("missing message timestamp")
    model_id = f"{model['providerID']}/{model['id']}"
    if model.get("variant"):
        model_id += f"#{model['variant']}"
    return MessageUsage(
        id=info["id"],
        session_id=session_id,
        model=model_id,
        created_ms=created_ms,
        input_tokens=tokens["input"],
        output_tokens=tokens["output"],
        reasoning_tokens=tokens.get("reasoning", 0),
        cache_read_tokens=cache.get("read", 0),
        cache_write_tokens=cache.get("write", 0),
        reported_cost=(
            Decimal(str(info["cost"])) if info.get("cost") is not None else None
        ),
    )
