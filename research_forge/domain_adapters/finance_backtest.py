"""Point-in-time validity checks for finance and trading experiments."""

from __future__ import annotations

from typing import Any


FINANCE_TERMS = (
    "stock",
    "trading",
    "portfolio",
    "return",
    "equity",
    "finance",
    "股票",
    "交易",
    "投资组合",
    "收益",
    "选股",
)

REQUIRED_POLICIES = {
    "point_in_time_universe_policy": (
        "freeze point-in-time universe membership"
    ),
    "survivorship_bias_policy": (
        "declare delisting and suspension handling"
    ),
    "feature_availability_policy": (
        "bind every feature to an as-of timestamp"
    ),
    "corporate_action_policy": (
        "declare adjustment and restatement timing"
    ),
    "transaction_cost_policy": (
        "freeze fees, slippage, and capacity assumptions"
    ),
    "cross_sectional_leakage_policy": (
        "forbid future cross-sectional information"
    ),
    "trading_calendar_policy": (
        "freeze timezone and trading-day boundaries"
    ),
}


def is_finance_backtest_contract(payload: dict[str, Any]) -> bool:
    text = " ".join(str(value) for value in payload.values()).casefold()
    return any(term in text for term in FINANCE_TERMS)


def finance_backtest_defaults() -> dict[str, str]:
    return {
        "point_in_time_universe_policy": (
            "Universe membership is evaluated as known at each decision "
            "timestamp; later index constituents cannot enter earlier cases."
        ),
        "survivorship_bias_policy": (
            "Delisted and suspended instruments remain in the eligible "
            "history with explicit non-tradable outcomes."
        ),
        "feature_availability_policy": (
            "A feature is eligible only when its source timestamp is no later "
            "than the decision cutoff."
        ),
        "corporate_action_policy": (
            "Corporate actions and restatements use the version available at "
            "the decision timestamp; later revisions are excluded."
        ),
        "transaction_cost_policy": (
            "Primary net returns use the owner-approved frozen fee and "
            "slippage schedule; gross returns are secondary."
        ),
        "cross_sectional_leakage_policy": (
            "Cross-sectional transforms use only securities and observations "
            "available inside the same point-in-time universe."
        ),
        "trading_calendar_policy": (
            "Exchange timezone, close timestamp, non-trading days, and next "
            "executable session are frozen before formal execution."
        ),
    }


def finance_backtest_violations(
    data_requirements: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    for field, requirement in REQUIRED_POLICIES.items():
        if not str(data_requirements.get(field) or "").strip():
            violations.append(
                f"FINANCE_{field.upper()}_MISSING: {requirement}"
            )
    return violations


__all__ = [
    "finance_backtest_defaults",
    "finance_backtest_violations",
    "is_finance_backtest_contract",
]
