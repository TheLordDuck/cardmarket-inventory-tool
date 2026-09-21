import time
from pathlib import Path

import argparse

import pytest

from src.main import _adjustment_pct, _find_latest_own_export


def test_find_latest_own_export_returns_none_when_missing_dir(tmp_path):
    assert _find_latest_own_export(tmp_path / "nope") is None


def test_find_latest_own_export_returns_none_when_no_matches(tmp_path):
    (tmp_path / "unrelated.csv").write_text("x")
    assert _find_latest_own_export(tmp_path) is None


def test_find_latest_own_export_picks_newest_match(tmp_path):
    older = tmp_path / "stock_export_20260901_120000.csv"
    newer = tmp_path / "stock_export_20260914_120000.csv"
    older.write_text("a")
    time.sleep(0.01)
    newer.write_text("b")

    assert _find_latest_own_export(tmp_path) == newer


def test_adjustment_pct_accepts_plain_numbers():
    assert _adjustment_pct("5") == 5.0
    assert _adjustment_pct("-5") == -5.0
    assert _adjustment_pct("2.5") == 2.5


def test_adjustment_pct_accepts_leading_plus_and_trailing_percent():
    assert _adjustment_pct("+5%") == 5.0
    assert _adjustment_pct("-5%") == -5.0
    assert _adjustment_pct(" +7.5% ") == 7.5


def test_adjustment_pct_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        _adjustment_pct("abc")
