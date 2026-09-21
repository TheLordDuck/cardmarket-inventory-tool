"""Reads/writes this tool's own stock CSV format, used by the `export` /
`import` commands: scrape your stock via the browser once, edit the `price`
column yourself (e.g. in a spreadsheet), then import it back to apply only
the prices you changed.

Comma-delimited, UTF-8 with a BOM (so Excel opens it correctly without
mangling accents), one row per stock article:

    id_article,name,expansion,collector_number,foil,condition,language,quantity,price

Only `price` is ever read back on import. `name`/`expansion`/`condition`/
`foil`/`language` are there so you can see what you're pricing, and are also
what `browser_client.make_match_key` uses to find each row again in the
browser -- don't edit those columns, only `price`.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .browser_client import BrowserRow, make_match_key

FIELDNAMES = [
    "id_article", "name", "expansion", "collector_number", "foil",
    "condition", "language", "quantity", "price",
]


class CsvFormatError(ValueError):
    pass


def write_stock_csv(path: Path, rows: list[BrowserRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "id_article": row.id_article,
                "name": row.name,
                "expansion": row.expansion,
                "collector_number": row.collector_number,
                "foil": "Y" if row.foil else "N",
                "condition": row.condition,
                "language": row.language,
                "quantity": row.quantity,
                "price": f"{row.current_price:.2f}",
            })


def _safe_float(text: str) -> float:
    cleaned = re.sub(r"[^\d,.\-]", "", text or "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def read_new_prices(path: Path) -> dict[str, float]:
    """Reads a previously exported (and price-edited) CSV and returns
    {match_key: new_price}, ready to pass to
    `CardmarketBrowserClient.apply_price_updates`. The match key is
    recomputed from name/expansion/condition/language/foil the same way the
    browser client computes it for a live stock row -- edit only `price`.
    """
    if not path.exists():
        raise CsvFormatError(f"CSV not found: {path}")

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing = [c for c in FIELDNAMES if c not in fieldnames]
        if missing:
            raise CsvFormatError(
                f"Missing expected column(s) {missing} in {path} (found {fieldnames}). "
                "Only edit the 'price' column of a CSV written by `export` -- don't rename or remove headers."
            )
        rows = list(reader)

    new_prices: dict[str, float] = {}
    for row in rows:
        foil = (row.get("foil", "") or "").strip().upper() == "Y"
        match_key = make_match_key(
            row.get("name", ""), row.get("expansion", ""), row.get("condition", ""),
            row.get("language", ""), foil,
        )
        new_prices[match_key] = _safe_float(row.get("price", ""))
    return new_prices
