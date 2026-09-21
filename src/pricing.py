"""Pure pricing calculation: trend price -> condition-adjusted, bounded,
rounded listing price. No I/O, fully unit-testable.
"""
from __future__ import annotations


def round_to_step(price: float, step: float) -> float:
    if step <= 0:
        return round(price, 2)
    steps = round(price / step)
    return round(steps * step, 2)


def compute_price(
    trend_price: float,
    condition: str,
    multiplier: float,
    condition_multipliers: dict,
    min_price: float,
    max_price: float | None,
    step: float,
    adjustment_pct: float = 0.0,
) -> float:
    if trend_price is None or trend_price <= 0:
        raise ValueError("trend_price must be a positive number")

    condition_multiplier = condition_multipliers.get(condition.upper(), 1.0)
    raw_price = trend_price * multiplier * condition_multiplier * (1 + adjustment_pct / 100)

    bounded = max(raw_price, min_price)
    if max_price is not None:
        bounded = min(bounded, max_price)

    return round_to_step(bounded, step)
