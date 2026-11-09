from pathlib import Path

import pytest

from src.cardmarket import csv_io
from src.cardmarket.browser_client import BrowserRow, make_match_key


def _sample_rows() -> list[BrowserRow]:
    return [
        BrowserRow(
            match_key=make_match_key("Lightning Bolt", "Limited Edition Alpha", "NM", "", False),
            name="Lightning Bolt", expansion="Limited Edition Alpha", collector_number="161",
            foil=False, condition="NM", language="", quantity=2, current_price=15.0,
            id_article="1001",
        ),
        BrowserRow(
            match_key=make_match_key("Ragavan, Nimble Pilferer", "Modern Horizons 2", "NM", "", True),
            name="Ragavan, Nimble Pilferer", expansion="Modern Horizons 2", collector_number="138",
            foil=True, condition="NM", language="", quantity=3, current_price=60.0,
            id_article="1003",
        ),
    ]


def test_write_stock_csv_round_trips_expected_columns(tmp_path):
    path = tmp_path / "export.csv"
    csv_io.write_stock_csv(path, _sample_rows())

    text = path.read_text(encoding="utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == ";".join(csv_io.FIELDNAMES)
    assert "Lightning Bolt" in lines[1]
    assert ";N;" in lines[1]
    assert lines[1].endswith(";15,00")
    # The comma in the card name stays inside its own cell, no quoting needed.
    assert ";Ragavan, Nimble Pilferer;" in lines[2]
    assert ";Y;" in lines[2]


def test_read_new_prices_recomputes_match_key_from_edited_csv(tmp_path):
    path = tmp_path / "export.csv"
    csv_io.write_stock_csv(path, _sample_rows())

    # Simulate a human editing only the price column.
    text = path.read_text(encoding="utf-8-sig")
    edited = text.replace("15,00", "12,50")
    path.write_text(edited, encoding="utf-8-sig")

    new_prices = csv_io.read_new_prices(path)
    bolt_key = make_match_key("Lightning Bolt", "Limited Edition Alpha", "NM", "", False)
    ragavan_key = make_match_key("Ragavan, Nimble Pilferer", "Modern Horizons 2", "NM", "", True)

    assert new_prices[bolt_key] == 12.5
    assert new_prices[ragavan_key] == 60.0


def test_read_new_prices_accepts_dot_decimal_separator(tmp_path):
    path = tmp_path / "export.csv"
    csv_io.write_stock_csv(path, _sample_rows())
    text = path.read_text(encoding="utf-8-sig")
    path.write_text(text.replace("15,00", "12.50"), encoding="utf-8-sig")

    new_prices = csv_io.read_new_prices(path)
    bolt_key = make_match_key("Lightning Bolt", "Limited Edition Alpha", "NM", "", False)
    assert new_prices[bolt_key] == 12.5


def test_read_new_prices_accepts_legacy_comma_delimited_file(tmp_path):
    path = tmp_path / "old_export.csv"
    path.write_text(
        ",".join(csv_io.FIELDNAMES) + "\n"
        '1001,Lightning Bolt,Limited Edition Alpha,161,N,NM,,2,"12,50"\n',
        encoding="utf-8-sig",
    )
    new_prices = csv_io.read_new_prices(path)
    bolt_key = make_match_key("Lightning Bolt", "Limited Edition Alpha", "NM", "", False)
    assert new_prices[bolt_key] == 12.5


def test_safe_float_handles_thousands_separators():
    assert csv_io._safe_float("1.234,50") == 1234.5
    assert csv_io._safe_float("1,234.50") == 1234.5
    assert csv_io._safe_float("12,50 €") == 12.5


def test_read_new_prices_raises_on_missing_file(tmp_path):
    with pytest.raises(csv_io.CsvFormatError):
        csv_io.read_new_prices(tmp_path / "missing.csv")


def test_read_new_prices_raises_on_wrong_headers(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8-sig")
    with pytest.raises(csv_io.CsvFormatError):
        csv_io.read_new_prices(path)
