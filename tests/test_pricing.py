import pytest

from src.pricing import compute_price, round_to_step


def test_round_to_step_basic():
    assert round_to_step(1.234, 0.01) == 1.23
    assert round_to_step(1.005, 0.01) == 1.0 or round_to_step(1.005, 0.01) == 1.01
    assert round_to_step(1.27, 0.1) == 1.3


def test_compute_price_matches_trend_with_no_adjustment():
    price = compute_price(
        trend_price=2.0,
        min_price=0.05,
        max_price=None,
        step=0.01,
    )
    assert price == 2.0


def test_compute_price_respects_min_price_floor():
    price = compute_price(
        trend_price=0.02,
        min_price=0.25,
        max_price=None,
        step=0.01,
    )
    assert price == 0.25


def test_compute_price_respects_max_price_cap():
    price = compute_price(
        trend_price=500.0,
        min_price=0.05,
        max_price=100.0,
        step=0.01,
    )
    assert price == 100.0


def test_compute_price_applies_positive_adjustment_pct():
    price = compute_price(
        trend_price=10.0,
        min_price=0.05,
        max_price=None,
        step=0.01,
        adjustment_pct=5,
    )
    assert price == 10.5


def test_compute_price_applies_negative_adjustment_pct():
    price = compute_price(
        trend_price=10.0,
        min_price=0.05,
        max_price=None,
        step=0.01,
        adjustment_pct=-5,
    )
    assert price == 9.5


def test_compute_price_rejects_non_positive_trend():
    with pytest.raises(ValueError):
        compute_price(
            trend_price=0.0,
            min_price=0.05,
            max_price=None,
            step=0.01,
        )
