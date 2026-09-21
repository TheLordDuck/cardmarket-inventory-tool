"""CLI entry point.

Two workflows, both driven through your own already-logged-in Chrome (see
README) -- always the English (`/en/`) site and always the same login form,
so selectors stay stable run to run:

    python -m src.main reprice [--dry-run] [--limit N] [--price-source trend|30d|7d|1d] [--adjustment-pct N]
        Clicks into each stock article's own product page, reads whichever of
        Cardmarket's own price stats --price-source selects (default: 7d,
        the 7-day average), applies an optional --adjustment-pct (e.g. 5 or
        -5), then writes the result straight back via the "Edit Article"
        modal.

    python -m src.main export [--output path.csv]
        Scrapes your current stock into a CSV you can open and edit.

    python -m src.main import [--input path.csv] [--dry-run] [--limit N]
        Reads a CSV previously written by `export` (with the `price` column
        edited) and applies just those prices back in the browser.
"""
from __future__ import annotations

import argparse
import csv
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .cardmarket import csv_io
from .cardmarket.browser_client import (
    PRICE_SOURCE_LABELS,
    CardmarketBrowserClient,
    CardmarketBrowserError,
    LOGIN_URL,
)
from .config import load_config
from .pricing import compute_price


class _HumanFormatter(logging.Formatter):
    """Console/GUI-facing formatter: just a clock time and the message, with
    a level tag only when something's actually worth flagging (warning or
    worse) -- the full `timestamp LEVEL logger.name: message` format is kept
    for the file log instead, where it's useful for troubleshooting but
    otherwise just noise to read live.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        if record.levelno >= logging.WARNING:
            return f"{ts}  [{record.levelname}] {record.getMessage()}"
        return f"{ts}  {record.getMessage()}"


def _setup_logging(logs_dir):
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_HumanFormatter())

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler], force=True)
    return log_path


def _write_report(cfg, report_rows) -> Path:
    path = cfg.reports_dir / f"pricing_{datetime.now():%Y%m%d_%H%M%S}.csv"
    fieldnames = ["id_article", "id_product", "name", "expansion", "collector_number", "foil",
                  "condition", "language", "quantity", "current_price", "new_price", "matched", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in report_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return path


def _find_latest_own_export(reports_dir: Path) -> Path | None:
    if not reports_dir.exists():
        return None
    candidates = sorted(reports_dir.glob("stock_export_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _adjustment_pct(text: str) -> float:
    """argparse type for `reprice --adjustment-pct`: accepts a plain number
    (`5`, `-5`, `2.5`) as well as a leading `+` and/or trailing `%`
    (`+5%`, `-5%`), since that's the natural way to type an increase/
    decrease.
    """
    cleaned = text.strip().rstrip("%").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} isn't a valid percentage -- use e.g. 5, -5, +5%% or -5%%."
        )


def _cdp_alive(cdp_url: str) -> bool:
    try:
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _ensure_chrome_running(cfg, logger) -> subprocess.Popen | None:
    """If Chrome isn't already listening on the configured debugging port,
    launches it ourselves with the dedicated profile (see README -- Chrome
    refuses the debugging flag on your default profile), always starting at
    LOGIN_URL (the English-locale `/en/` Cardmarket homepage) as Chrome's own
    *startup* URL. This is Chrome's own, non-automation-driven navigation (an
    OS-level launch argument, resolved before Playwright ever attaches over
    CDP) -- not a scripted `.goto()` -- so it doesn't reintroduce the risk
    documented in browser_client's module docstring.

    Returns the launched process (so the caller can close it again once
    done -- see `_close_chrome`), or None if Chrome (with that debugging
    port) was already up and left alone as someone else's session.
    """
    if _cdp_alive(cfg.cardmarket_cdp_url):
        return None

    logger.info(
        "Chrome isn't running with the debugging port open yet -- launching it now "
        "(profile: %s).", cfg.cardmarket_browser_profile_dir,
    )
    cfg.cardmarket_browser_profile_dir.mkdir(parents=True, exist_ok=True)
    port = cfg.cardmarket_cdp_url.rsplit(":", 1)[-1]
    process = subprocess.Popen([
        cfg.chrome_executable_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={cfg.cardmarket_browser_profile_dir}",
        # Cardmarket's header login form is hidden by CSS (Bootstrap
        # `d-none d-lg-inline-flex`) below its "lg" breakpoint (~992px) --
        # confirmed to cause a stuck/failed auto-login when Chrome opens
        # narrower than that. 1280x900 stays comfortably above it.
        "--window-size=1280,900",
        LOGIN_URL,
    ])

    for _ in range(30):
        if _cdp_alive(cfg.cardmarket_cdp_url):
            logger.info("Chrome is up.")
            return process
        time.sleep(1)
    raise CardmarketBrowserError(
        f"Launched Chrome but its debugging port ({cfg.cardmarket_cdp_url}) never came up after 30s. "
        f"Check CHROME_EXECUTABLE_PATH ({cfg.chrome_executable_path!r}) is correct, and that no other "
        "Chrome process is blocking it (close every Chrome window and try again)."
    )


def _close_chrome(process: subprocess.Popen | None, logger) -> None:
    """Closes the Chrome instance we launched ourselves this run, if any --
    never touches a Chrome instance we merely attached to (see
    `_ensure_chrome_running`, which returns None in that case). Uses
    `taskkill /F` -- confirmed live (twice, back to back) to still leave the
    dedicated profile's cookies/session intact for the next run, unlike a
    raw `Stop-Process -Force` from PowerShell, which was confirmed to
    sometimes lose the logged-in session and require 2FA again.
    """
    if process is None or process.poll() is not None:
        return
    logger.info("Closing the browser...")
    try:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
        process.wait(timeout=8)
    except Exception:
        logger.debug("Closing Chrome didn't fully confirm (non-fatal).")


def _ensure_logged_in(client: CardmarketBrowserClient, cfg, logger) -> bool:
    """Always goes through the one login form Cardmarket serves at LOGIN_URL
    (`/en/Magic`) -- either it's already satisfied (session cookie present)
    or `attempt_auto_login` fills that same form. If it just submitted the
    form, polls for up to LOGIN_WAIT_TIMEOUT_SECONDS for you to finish
    2FA/verification in the browser, so the command can continue on its own
    instead of you having to rerun it. Returns True if the caller can
    proceed now, False if it gave up waiting (a real failure -- the caller
    should exit non-zero).
    """
    if client.looks_logged_in():
        return True
    if cfg.cardmarket_username and cfg.cardmarket_password:
        logger.info("Not logged in yet -- attempting to fill in your Cardmarket login form...")
        submitted = client.attempt_auto_login(cfg.cardmarket_username, cfg.cardmarket_password)
        if submitted:
            timeout = cfg.login_wait_timeout_seconds
            logger.info(
                "Submitted your Cardmarket username/password. If a 2FA/verification step appears in "
                "the browser window, complete it there now -- waiting up to %d minute(s) for you to "
                "finish, then continuing automatically.", max(timeout // 60, 1),
            )
            deadline = time.time() + timeout
            last_reminder = time.time()
            while time.time() < deadline:
                if client.looks_logged_in():
                    logger.info("Logged in -- continuing.")
                    return True
                if time.time() - last_reminder >= 20:
                    logger.info("Still waiting for you to finish logging in in the browser window...")
                    last_reminder = time.time()
                time.sleep(2)
            logger.error(
                "Gave up waiting after %d minute(s) -- still not logged in. Finish logging in "
                "yourself in the browser window, then run this command again (LOGIN_WAIT_TIMEOUT_SECONDS "
                "in .env controls how long this waits).", max(timeout // 60, 1),
            )
            return False
        logger.warning(
            "Couldn't find a login form to auto-fill (SELECTORS['login_*'] may not match Cardmarket's "
            "real markup -- check the dumped browser_login_form_not_found_*.html in reports/ if you're "
            "sure you're logged out). Falling back to manual login."
        )
    client.ensure_logged_in()
    return True


def _open_client(cfg, debug_dir) -> CardmarketBrowserClient:
    return CardmarketBrowserClient(
        cdp_url=cfg.cardmarket_cdp_url,
        debug_dir=debug_dir,
        delay_range=(cfg.browser_action_delay_min_seconds, cfg.browser_action_delay_max_seconds),
    )


def cmd_login(args) -> None:
    """Standalone login step: launches Chrome (if needed), attempts
    auto-login, and waits for you to finish 2FA -- without also kicking off
    a scrape/reprice run. Run this on its own first if you'd rather handle
    2FA calmly before starting an actual command.

    Deliberately does NOT close the browser afterwards (unlike
    reprice/export/import) -- the entire point of this command is to leave
    you with an open, logged-in Chrome ready for whatever you run next (or
    to look at yourself); auto-closing it here just meant it flashed open
    and shut again immediately whenever 2FA wasn't even needed.
    """
    cfg = load_config()
    log_path = _setup_logging(cfg.logs_dir)
    logger = logging.getLogger("login")

    debug_dir = cfg.reports_dir if args.debug_browser else None

    try:
        _ensure_chrome_running(cfg, logger)
        with _open_client(cfg, debug_dir) as client:
            if not _ensure_logged_in(client, cfg, logger):
                sys.exit(1)
            logger.info("Logged in and ready -- you can now run `reprice`, `export`, or `import`.")
    except CardmarketBrowserError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception:
        logger.exception(
            "Unexpected error -- this isn't one of the known/handled failure cases above, so it's "
            "logged here with its full traceback. Pass --debug-browser and share this log plus any new "
            "reports/browser_*_stuck_*.html dump if you're not sure what caused it."
        )
        sys.exit(1)

    logger.info("Done. Full log: %s", log_path)


def cmd_reprice(args) -> None:
    cfg = load_config()
    if args.limit is not None:
        cfg.max_changes_per_run = args.limit

    log_path = _setup_logging(cfg.logs_dir)
    logger = logging.getLogger("reprice")

    logger.warning(
        "Attaches to your own Chrome (CDP), launching it with a dedicated profile if it isn't already "
        "running -- it never navigates cardmarket.com itself via automation, only Chrome's own startup "
        "URL / your own clicks do. Even so this isn't risk-free -- start with --dry-run and a small "
        "--limit. Pass --debug-browser and share the dump in reports/ if something looks wrong."
    )

    debug_dir = cfg.reports_dir if args.debug_browser else None

    chrome_process = None
    try:
        chrome_process = _ensure_chrome_running(cfg, logger)

        with _open_client(cfg, debug_dir) as client:
            if not _ensure_logged_in(client, cfg, logger):
                sys.exit(1)
            client.ensure_on_stock_page()

            if args.inspect_modal:
                if not args.debug_browser:
                    logger.error("--inspect-modal requires --debug-browser (it has no other output).")
                    sys.exit(1)
                limit = args.limit or 1
                opened = client.inspect_edit_modal(limit=limit)
                logger.info(
                    "Opened and closed %d edit modal(s) for inspection; nothing was changed. Check the "
                    "dumped reports/browser_modal_inspect_*.html and correct SELECTORS['modal_price_input']/"
                    "['modal_submit'] in src/cardmarket/browser_client.py if they don't match.",
                    opened,
                )
                logger.info("Done. Full log: %s", log_path)
                return

            price_label = PRICE_SOURCE_LABELS[args.price_source]
            logger.info(
                "Scraping current stock and each article's own Cardmarket %r "
                "(one extra tab open/close per row -- this takes a while for a full stock)...", price_label,
            )
            rows = client.get_stock(fetch_trend_prices=True, price_source=args.price_source)
            logger.info("Scraped %d stock rows.", len(rows))

            report_rows = []
            new_prices: dict[str, float] = {}
            for row in rows:
                base_row = {
                    "id_article": row.id_article, "id_product": row.id_product or "", "name": row.name,
                    "expansion": row.expansion, "collector_number": row.collector_number,
                    "foil": row.foil, "condition": row.condition, "language": row.language,
                    "quantity": row.quantity, "current_price": row.current_price,
                }
                if row.trend_price is None:
                    report_rows.append({**base_row, "matched": False, "new_price": "",
                                         "note": f"no Cardmarket {price_label} available"})
                    continue

                new_price = compute_price(
                    trend_price=row.trend_price,
                    min_price=cfg.min_price,
                    max_price=cfg.max_price,
                    step=cfg.price_step,
                    adjustment_pct=args.adjustment_pct,
                )
                report_rows.append({**base_row, "matched": True, "new_price": new_price})

                if abs(new_price - row.current_price) >= cfg.price_step:
                    new_prices[row.match_key] = new_price

            if cfg.max_changes_per_run and len(new_prices) > cfg.max_changes_per_run:
                capped_keys = list(new_prices.keys())[: cfg.max_changes_per_run]
                new_prices = {k: new_prices[k] for k in capped_keys}
                logger.info("Capped to %d price changes this run (MAX_CHANGES_PER_RUN).", cfg.max_changes_per_run)

            report_path = _write_report(cfg, report_rows)
            matched = sum(1 for r in report_rows if r["matched"])
            logger.info("Wrote pricing report to %s (%d/%d rows had a Cardmarket price available, "
                        "%d price changes queued)",
                        report_path, matched, len(report_rows), len(new_prices))

            if args.dry_run:
                logger.info("--dry-run set: not touching anything in the browser.")
            elif not new_prices:
                logger.info("No price changes to apply.")
            else:
                logger.info("Applying %d price updates in the browser...", len(new_prices))
                result = client.apply_price_updates(new_prices)
                logger.info("Applied %d update(s), %d unmatched, %d failed.",
                            result["applied"], result["unmatched"], result["failed"])
    except CardmarketBrowserError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception:
        logger.exception(
            "Unexpected error -- this isn't one of the known/handled failure cases above, so it's "
            "logged here with its full traceback. Pass --debug-browser and share this log plus any new "
            "reports/browser_*_stuck_*.html dump if you're not sure what caused it."
        )
        sys.exit(1)
    finally:
        _close_chrome(chrome_process, logger)

    logger.info("Done. Full log: %s", log_path)


def cmd_inspect_login(args) -> None:
    """Safely dumps the login page's real HTML/screenshot to reports/ so
    SELECTORS['login_*'] in src/cardmarket/browser_client.py can be verified
    or corrected -- never fills or submits anything. Doesn't require being
    logged in or out. A raw fetch of cardmarket.com from outside a real
    browser gets Cloudflare's challenge page instead of the real markup, so
    this has to go through your own attached Chrome session, same as
    everything else this tool does.
    """
    cfg = load_config()
    log_path = _setup_logging(cfg.logs_dir)
    logger = logging.getLogger("inspect-login")

    if not args.debug_browser:
        logger.error("inspect-login requires --debug-browser (it has no other output).")
        sys.exit(1)

    chrome_process = None
    try:
        chrome_process = _ensure_chrome_running(cfg, logger)
        with _open_client(cfg, cfg.reports_dir) as client:
            found = client.inspect_login_page()
    except CardmarketBrowserError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception:
        logger.exception(
            "Unexpected error -- this isn't one of the known/handled failure cases above, so it's "
            "logged here with its full traceback. Pass --debug-browser and share this log plus any new "
            "reports/browser_*_stuck_*.html dump if you're not sure what caused it."
        )
        sys.exit(1)
    finally:
        _close_chrome(chrome_process, logger)

    if found:
        logger.info(
            "Found SELECTORS['login_username_input'] on the page. Check the dumped "
            "reports/browser_login_page_*.html against SELECTORS['login_*'] in "
            "src/cardmarket/browser_client.py to confirm they still match."
        )
    else:
        logger.warning(
            "Couldn't find SELECTORS['login_username_input'], even after trying "
            "SELECTORS['login_link']. Check the dumped reports/browser_login_page_*.html -- either "
            "you're already logged in (no login form to find, which is fine), or Cardmarket's real "
            "markup has changed and SELECTORS['login_*'] needs updating."
        )
    logger.info("Done. Full log: %s", log_path)


def cmd_export(args) -> None:
    cfg = load_config()
    log_path = _setup_logging(cfg.logs_dir)
    logger = logging.getLogger("export")

    debug_dir = cfg.reports_dir if args.debug_browser else None

    chrome_process = None
    try:
        chrome_process = _ensure_chrome_running(cfg, logger)

        with _open_client(cfg, debug_dir) as client:
            if not _ensure_logged_in(client, cfg, logger):
                sys.exit(1)
            client.ensure_on_stock_page()

            logger.info("Scraping current stock...")
            rows = client.get_stock()
            logger.info("Scraped %d stock rows.", len(rows))
    except CardmarketBrowserError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception:
        logger.exception(
            "Unexpected error -- this isn't one of the known/handled failure cases above, so it's "
            "logged here with its full traceback. Pass --debug-browser and share this log plus any new "
            "reports/browser_*_stuck_*.html dump if you're not sure what caused it."
        )
        sys.exit(1)
    finally:
        _close_chrome(chrome_process, logger)

    output_path = Path(args.output) if args.output else \
        cfg.reports_dir / f"stock_export_{datetime.now():%Y%m%d_%H%M%S}.csv"
    csv_io.write_stock_csv(output_path, rows)
    logger.info(
        "Wrote %d rows to %s. Edit the 'price' column yourself (only that column), then run "
        "`import --input %s` (or just `import` -- it picks the newest export automatically).",
        len(rows), output_path, output_path,
    )
    logger.info("Done. Full log: %s", log_path)


def cmd_import(args) -> None:
    cfg = load_config()
    if args.limit is not None:
        cfg.max_changes_per_run = args.limit

    log_path = _setup_logging(cfg.logs_dir)
    logger = logging.getLogger("import")

    if args.input:
        input_path = Path(args.input)
    else:
        input_path = _find_latest_own_export(cfg.reports_dir)
        if input_path is None:
            logger.error(
                "No --input given and no stock_export_*.csv found in %s. Run `export` first, "
                "or pass --input explicitly.", cfg.reports_dir,
            )
            sys.exit(1)
        logger.info("No --input given; using the newest export found: %s", input_path)

    try:
        new_prices = csv_io.read_new_prices(input_path)
    except csv_io.CsvFormatError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if cfg.max_changes_per_run and len(new_prices) > cfg.max_changes_per_run:
        capped_keys = list(new_prices.keys())[: cfg.max_changes_per_run]
        new_prices = {k: new_prices[k] for k in capped_keys}
        logger.info("Capped to %d price changes this run (MAX_CHANGES_PER_RUN).", cfg.max_changes_per_run)

    logger.info("Loaded %d price(s) from %s.", len(new_prices), input_path)

    debug_dir = cfg.reports_dir if args.debug_browser else None

    chrome_process = None
    try:
        chrome_process = _ensure_chrome_running(cfg, logger)

        with _open_client(cfg, debug_dir) as client:
            if not _ensure_logged_in(client, cfg, logger):
                sys.exit(1)
            client.ensure_on_stock_page()

            if args.inspect_modal:
                if not args.debug_browser:
                    logger.error("--inspect-modal requires --debug-browser (it has no other output).")
                    sys.exit(1)
                limit = args.limit or 1
                opened = client.inspect_edit_modal(limit=limit)
                logger.info(
                    "Opened and closed %d edit modal(s) for inspection; nothing was changed. Check the "
                    "dumped reports/browser_modal_inspect_*.html and correct SELECTORS['modal_price_input']/"
                    "['modal_submit'] in src/cardmarket/browser_client.py if they don't match.",
                    opened,
                )
                logger.info("Done. Full log: %s", log_path)
                return

            if args.dry_run:
                logger.info("--dry-run set: found %d price(s) to apply, not touching the browser.",
                            len(new_prices))
            elif not new_prices:
                logger.info("No price changes to apply.")
            else:
                logger.info("Applying %d price updates in the browser...", len(new_prices))
                result = client.apply_price_updates(new_prices)
                logger.info("Applied %d update(s), %d unmatched, %d failed.",
                            result["applied"], result["unmatched"], result["failed"])
    except CardmarketBrowserError as exc:
        logger.error(str(exc))
        sys.exit(1)
    except Exception:
        logger.exception(
            "Unexpected error -- this isn't one of the known/handled failure cases above, so it's "
            "logged here with its full traceback. Pass --debug-browser and share this log plus any new "
            "reports/browser_*_stuck_*.html dump if you're not sure what caused it."
        )
        sys.exit(1)
    finally:
        _close_chrome(chrome_process, logger)

    logger.info("Done. Full log: %s", log_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cardmarket stock repricer (direct-browser only)")
    sub = parser.add_subparsers(dest="command", required=True)

    login_p = sub.add_parser(
        "login",
        help="Launch Chrome (if needed) and log in -- waits for you to finish 2FA, then reports ready. "
             "Run this on its own before reprice/export/import if you'd rather handle 2FA separately.",
    )
    login_p.add_argument("--debug-browser", action="store_true",
                          help="Dump page HTML/screenshots to reports/ when something looks wrong.")
    login_p.set_defaults(func=cmd_login)

    reprice_p = sub.add_parser(
        "reprice",
        help="Auto-price every article from Cardmarket's own average price and apply it in the browser.",
    )
    reprice_p.add_argument("--dry-run", action="store_true",
                            help="Only scrape stock and write the pricing report; don't change any prices.")
    reprice_p.add_argument("--debug-browser", action="store_true",
                            help="Dump page HTML/screenshots to reports/ when something looks wrong.")
    reprice_p.add_argument("--limit", type=int, default=None,
                            help="Cap how many articles get updated this run.")
    reprice_p.add_argument("--inspect-modal", action="store_true",
                            help="Safely opens the Edit Article modal for the first N rows (--limit, default "
                                 "1), dumps its HTML/screenshot to reports/ for selector verification, then "
                                 "closes it without changing anything. Requires --debug-browser. Run this "
                                 "before ever dropping --dry-run.")
    reprice_p.add_argument("--price-source", choices=list(PRICE_SOURCE_LABELS), default="7d",
                            help="Which Cardmarket average to use as the base price: trend, 30d, 7d "
                                 "(default) or 1d.")
    reprice_p.add_argument("--adjustment-pct", type=_adjustment_pct, default=0.0,
                            help="Extra +/- percentage applied on top of the source price, e.g. 5 "
                                 "or -5 (also accepts a trailing %% and a leading +).")
    reprice_p.set_defaults(func=cmd_reprice)

    export_p = sub.add_parser(
        "export",
        help="Scrape your current stock into a CSV you can edit the price column of.",
    )
    export_p.add_argument("--output", default=None,
                           help="Where to write the CSV (default: reports/stock_export_<timestamp>.csv).")
    export_p.add_argument("--debug-browser", action="store_true",
                           help="Dump page HTML/screenshots to reports/ when something looks wrong.")
    export_p.set_defaults(func=cmd_export)

    import_p = sub.add_parser(
        "import",
        help="Apply the price column from a CSV previously written by `export` (and edited by you).",
    )
    import_p.add_argument("--input", default=None,
                           help="Path to the edited CSV. If omitted, uses the newest "
                                "reports/stock_export_*.csv.")
    import_p.add_argument("--dry-run", action="store_true",
                           help="Only read the CSV and report how many prices would change; don't touch the browser.")
    import_p.add_argument("--debug-browser", action="store_true",
                           help="Dump page HTML/screenshots to reports/ when something looks wrong.")
    import_p.add_argument("--limit", type=int, default=None,
                           help="Cap how many articles get updated this run.")
    import_p.add_argument("--inspect-modal", action="store_true",
                           help="Safely opens the Edit Article modal for the first N rows (--limit, default "
                                "1), dumps its HTML/screenshot to reports/ for selector verification, then "
                                "closes it without changing anything. Requires --debug-browser. Run this "
                                "before ever dropping --dry-run.")
    import_p.set_defaults(func=cmd_import)

    inspect_login_p = sub.add_parser(
        "inspect-login",
        help="Safely dumps the login page's HTML/screenshot to reports/ for verifying SELECTORS['login_*'] "
             "in src/cardmarket/browser_client.py. Never fills or submits anything.",
    )
    inspect_login_p.add_argument("--debug-browser", action="store_true",
                                  help="Required -- dumps page HTML/screenshots to reports/.")
    inspect_login_p.set_defaults(func=cmd_inspect_login)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
