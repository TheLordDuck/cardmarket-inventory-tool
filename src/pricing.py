"""Pure pricing calculation: trend price -> adjusted, bounded, rounded
listing price. No I/O, fully unit-testable.
"""
from __future__ import annotations


def round_to_step(price: float, step: float) -> float:
    if step <= 0:
        return round(price, 2)
    steps = round(price / step)
    return round(steps * step, 2)


def compute_price(
    trend_price: float,
    min_price: float,
    max_price: float | None,
    step: float,
    adjustment_pct: float = 0.0,
) -> float:
    if trend_price is None or trend_price <= 0:
        raise ValueError("trend_price must be a positive number")

    raw_price = trend_price * (1 + adjustment_pct / 100)

    bounded = max(raw_price, min_price)
    if max_price is not None:
        bounded = min(bounded, max_price)

    return round_to_step(bounded, step)
