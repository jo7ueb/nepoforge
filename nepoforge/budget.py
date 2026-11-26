"""予算ゲートと provider 予算超過の扱い (#15・#61)。

有料タスクを起動する直前に、推定費用が予算内に収まるかを純粋関数で判定する。
実行・API 呼び出しは行わず、判定と理由の生成だけを行う。

規則の正本は設計メモ 0008。要点:

- 予算上限が未設定・不明なら有料起動を拒否する (未設定を無制限と解釈しない)。
- 価格が不明なモデルの有料起動は拒否する。
- 実績 (spent) と実行中タスクの予約額 (reserved) を含めて判定する。
- provider の予算超過を検知したら、新規有料起動と自動再試行を即時停止する。
  安全に停止できる進行中有料タスクは停止し、状態と理由を報告する。
  再開は人間の明示承認後のみ。課金なしのローカル作業は継続してよい。

provider・OpenCode 側では支出の強制ができない (強制可能な制約と努力目標の
区別は設計メモ 0008 に記録)。本モジュールの判定は起動前の関所として使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from math import isfinite
from typing import Mapping

from .store import NepoforgeError


class BudgetError(NepoforgeError):
    """予算規則に違反している (起動前に拒否する)。"""


# 単価表: 円 / 100万 token (入力, 出力・推論を含む・税別)。
# 価格基準日は PRICE_BASIS で明記し、請求実額と区別する。
# cacheRead は入力単価で概算する (実条件は未確認)。
# 記載のないモデルは価格不明として扱う。
PRICES: dict[str, tuple[float, float]] = {
    "fastmetal/japan-deepseek-v4.1-flash-1017": (33.6, 134.4),
    "fastmetal/japan-glm-5.3-flash": (17.64, 58.8),
    "fastmetal/beam-1.0": (67.2, 201.6),
    "fastmetal/gemma-4-26b-a4b-it": (11.76, 57.12),
    "fastmetal/gpt-oss-20b": (5.04, 23.52),
    "fastmetal/japan-qwen3-swallow-8b": (5.5, 27.0),
    "fastmetal/japan-deepseek-v4.1-flash-1017-validation": (33.6, 134.4),
    "fastmetal/llama-3.1-8b-instruct": (3.36, 6.72),
    "fastmetal/mistral-voxtral-mini-3b-2507": (8.4, 8.4),
}

PRICE_BASIS = "2026-11-26"


@dataclass(frozen=True)
class LaunchEstimate:
    """起動しようとする 1 タスクの費用見積りの入力。

    上限値 (最悪値) で見積もる。provider 側で出力を強制できないため、
    max_output_tokens は見積りの前提であり、強制ではない。
    """

    model: str
    max_input_tokens: int
    max_output_tokens: int
    retries: int = 0  # この起動で許す自動再試行回数 (費用に積む)

    def __post_init__(self) -> None:
        for name in ("max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise BudgetError(f"{name} must be a non-negative integer")
        if type(self.retries) is not int or self.retries < 0:
            raise BudgetError("retries must be a non-negative integer")


@dataclass(frozen=True)
class BudgetState:
    """現在の予算状態。金額は円・税別の推定値。"""

    limit_yen: float | None
    spent_yen: float = 0.0
    reserved_yen: float | Decimal = 0.0

    def __post_init__(self) -> None:
        for name in ("limit_yen", "spent_yen", "reserved_yen"):
            value = getattr(self, name)
            if name == "limit_yen" and value is None:
                continue
            if not isfinite(value) or value < 0:
                raise BudgetError(f"{name} must be finite and >= 0")


@dataclass(frozen=True)
class LaunchDecision:
    """起動可否と理由。allowed=False のとき reason が必ず入る。"""

    allowed: bool
    reason: str = ""
    estimated_cost_yen: float = 0.0
    projected_cost_yen: float = 0.0
    price_basis: str = ""


@dataclass(frozen=True)
class BudgetStop:
    """予算超過を検知したときの停止指示。"""

    stop_new_paid_launches: bool = True
    stop_auto_retry: bool = True
    stop_running_if_safe: bool = True
    charge_free_work_may_continue: bool = True
    resume_requires: str = "explicit human approval"
    reason: str = ""


def price_for(model: str, prices: Mapping[str, tuple[float, float]] = PRICES) -> tuple[float, float] | None:
    """モデルの単価 (入力, 出力)。未登録は None (価格不明)。"""
    return prices.get(model)


def estimate_cost_yen(
    est: LaunchEstimate, prices: Mapping[str, tuple[float, float]] = PRICES
) -> float:
    """見積りの上限額 (円)。未知の価格は BudgetError (起動拒否の理由になる)。"""
    price = price_for(est.model, prices)
    if price is None:
        raise BudgetError(f"unknown price for model: {est.model}")
    attempts = 1 + est.retries
    input_rate, output_rate = (Decimal(str(rate)) for rate in price)
    cost = (est.max_input_tokens * input_rate + est.max_output_tokens * output_rate)
    cost = cost * attempts / 1_000_000
    return float(cost.quantize(Decimal("0.0001"), rounding=ROUND_CEILING))


def decide_launch(
    state: BudgetState,
    est: LaunchEstimate,
    *,
    provider_budget_error: bool = False,
    prices: Mapping[str, tuple[float, float]] = PRICES,
) -> LaunchDecision:
    """有料起動を許すかを判定する。

    規則 (設計メモ 0008):
    1. provider の予算超過が直前に発生していたら、予算残高にかかわらず拒否
       (停止モード)。
    2. 予算上限が未設定なら拒否 (未設定を無制限と解釈しない)。
    3. 価格不明のモデルなら拒否。
    4. spent + reserved + 見積り が上限を超えるなら拒否。
    """
    if provider_budget_error:
        return LaunchDecision(
            allowed=False,
            reason=(
                "provider budget already exceeded: new paid launches and"
                " auto-retries are stopped until explicit human approval"
            ),
            price_basis=PRICE_BASIS,
        )
    if est.model not in prices:
        return LaunchDecision(
            allowed=False,
            reason=f"unknown price for model: {est.model} (deny paid launch)",
            price_basis=PRICE_BASIS,
        )
    if state.limit_yen is None:
        return LaunchDecision(
            allowed=False,
            reason="budget limit is not set (unset is not unlimited)",
            price_basis=PRICE_BASIS,
        )
    if est.max_input_tokens == est.max_output_tokens == 0:
        return LaunchDecision(
            allowed=False,
            reason="paid launch requires a non-zero token estimate",
            price_basis=PRICE_BASIS,
        )
    cost = estimate_cost_yen(est, prices)
    projected = Decimal(str(state.spent_yen)) + Decimal(str(state.reserved_yen)) + Decimal(str(cost))
    if projected > Decimal(str(state.limit_yen)):
        return LaunchDecision(
            allowed=False,
            reason=(
                f"insufficient budget: projected {projected:.4f} yen exceeds"
                f" limit {state.limit_yen:.4f} yen"
            ),
            estimated_cost_yen=cost,
            projected_cost_yen=float(projected),
            price_basis=PRICE_BASIS,
        )
    return LaunchDecision(
        allowed=True,
        estimated_cost_yen=cost,
        projected_cost_yen=float(projected),
        price_basis=PRICE_BASIS,
    )


def on_budget_error(reason: str = "provider reported budget exceeded") -> BudgetStop:
    """provider の予算超過を検知したときの停止指示を返す。

    新規有料起動・自動再試行を即時停止する。進行中有料タスクは安全に停止
   できる場合のみ停止する。課金なしのローカル作業は継続してよい。
    再開は人間の明示承認後のみ。
    """
    return BudgetStop(reason=reason)
