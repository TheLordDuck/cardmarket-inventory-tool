# Cardmarket Inventory Tool

Repricing tool for a Cardmarket (Magic: The Gathering) seller account.

There's no official Cardmarket API access on this account, and Cardmarket
has no native stock export/import feature — so this tool drives your own,
already-open, already-logged-in Chrome browser and clicks through the site
the same way you would by hand. Two workflows are available: a fully
automatic repricer, and a manual export → edit → import CSV round-trip.

Everything always starts from the same English-locale URL
(`https://www.cardmarket.com/en/Magic`, `LOGIN_URL` in
`src/cardmarket/browser_client.py`) and the same login form, so the page
markup/selectors this tool relies on stay consistent run to run, regardless
of what language or page your account might otherwise default to.

## Contents

- [Why it works this way](#why-it-works-this-way)
- [Setup](#setup)
- [Configuration](#configuration)
- [Logging in](#logging-in)
- [Running the project](#running-the-project)
- [Option 1 — auto-reprice](#option-1--reprice-auto-price-from-cardmarkets-own-averages)
- [Option 2 — manual CSV editing](#option-2--export--import-manual-csv-editing)
- [Shared behavior](#shared-behavior)
- [Tuning click speed](#tuning-click-speed)
- [Before trusting this on your real inventory](#-before-trusting-this-on-your-real-inventory)
- [Tests](#tests)
- [Project layout](#project-layout)

## Why it works this way

An earlier version of this tool launched a *fresh* Playwright-controlled
Chrome and had the script `page.goto()` straight to the login page. On the
very first real run, that `goto()` alone — before any human interaction —
got an immediate Cloudflare WAF block ("Sorry, you have been blocked...").

This version instead:

- **Attaches to your own, already-running Chrome** over the Chrome
  DevTools Protocol, rather than launching a fresh automated one.
- **Never calls `.goto()` against cardmarket.com.** The one exception is
  passing the URL as Chrome's own *startup* argument when this tool
  launches Chrome for you — that's Chrome's own navigation, not scripted
  browser automation.
- **Only reads the DOM or dispatches real `.click()`/`.fill()` calls** on
  elements already on screen — the same kind of interaction a real click
  produces.

This isn't risk-free — Cloudflare's bot management watches behavior for the
whole session — but it's the smallest risk surface available without
official API access.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chrome

copy .env.example .env          # then edit .env, see Configuration below
```

## Configuration

All settings live in `.env` (copied from `.env.example`, never committed —
already gitignored).

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARDMARKET_CDP_URL` | `http://localhost:9222` | CDP URL of the Chrome instance to attach to. |
| `BROWSER_ACTION_DELAY_MIN_SECONDS` | `1.5` | Minimum random delay (seconds) between simulated actions. |
| `BROWSER_ACTION_DELAY_MAX_SECONDS` | `4.0` | Maximum random delay (seconds) between simulated actions. |
| `CARDMARKET_USERNAME` | *(empty)* | Optional — lets the CLI auto-fill the login form. 2FA/CAPTCHA still needs you. |
| `CARDMARKET_PASSWORD` | *(empty)* | Paired with `CARDMARKET_USERNAME` — treat it like a real password. |
| `CHROME_EXECUTABLE_PATH` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Only needed if Chrome isn't at the default install path. |
| `CARDMARKET_BROWSER_PROFILE_DIR` | `<project>\.cardmarket-browser-profile` | Dedicated Chrome profile directory (gitignored) if left blank. |
| `LOGIN_WAIT_TIMEOUT_SECONDS` | `300` | How long to wait for you to finish 2FA before giving up. |
| `MIN_PRICE` | `0.05` | Price floor. |
| `MAX_PRICE` | *(none)* | Optional price ceiling. Leave blank for no cap. |
| `PRICE_STEP` | `0.01` | Rounds the computed price to the nearest multiple of this. |
| `MAX_CHANGES_PER_RUN` | `0` *(no limit)* | Caps how many rows get a changed price per run; overridable with `--limit`. |

## Logging in

Both `reprice`/`export`/`import` can launch Chrome themselves — no manual
PowerShell command needed.

- Chrome refuses to enable remote debugging on your **default** profile (a
  real security restriction, not a bug here), so a **separate, dedicated
  profile** is used instead (`.cardmarket-browser-profile/`, gitignored) —
  only if nothing's already listening on `CARDMARKET_CDP_URL`. If Chrome is
  already running with that debugging port open (e.g. from a previous run),
  it's left alone and reused as-is.
- **`reprice`/`export`/`import` close that Chrome again once they finish**
  (success or failure), but only if the command launched it itself this
  run — a Chrome you already had open, or attached to via
  `CARDMARKET_CDP_URL`, is never touched. Your login stays intact across
  closes/relaunches (cookies persist in the dedicated profile), so the next
  command just reuses it.
- **`login` is the one exception — it never closes the browser itself.**
  Its whole job is to leave you with an open, logged-in Chrome ready for
  whatever you run next; auto-closing it there just meant it flashed open
  and shut again immediately whenever 2FA wasn't even needed.

Set `CARDMARKET_USERNAME` / `CARDMARKET_PASSWORD` in `.env` and each command
will fill in your login form for you. **It never touches 2FA/CAPTCHA** —
that has to stay a human step: it fills in your username/password, then
**waits** (up to `LOGIN_WAIT_TIMEOUT_SECONDS`, default 5 minutes) for you to
complete 2FA/verification in the browser window — once you do, it continues
automatically, no need to rerun anything.

Run `login` on its own first if you'd rather get 2FA out of the way before
starting an actual `reprice`/`export`/`import` (also available as its own
tab in the GUI):

```powershell
.venv\Scripts\python.exe -m src.main login
```

**Prefer to log in by hand?** Close every open Chrome window completely
(Windows silently ignores the debugging flag if any Chrome process is still
running — check with `Get-Process chrome`), then:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$PWD\.cardmarket-browser-profile"
```

and log into Cardmarket in that window yourself before running a command.
Either way, this debugging port has no authentication — anything running
locally can fully control that browser while it's open, so close it when
you're done.

> [!TIP]
> Before ever dropping `--dry-run`, verify the Edit Article modal's real
> field selectors with the safe inspect mode — it opens the modal for one
> article, dumps its HTML/screenshot to `reports/`, and closes it again
> **without filling or submitting anything**:
> ```powershell
> .venv\Scripts\python.exe -m src.main reprice --inspect-modal --debug-browser --limit 1
> ```

## Running the project

Every command (`login`, `reprice`, `export`, `import`) can be run either
through the GUI or directly on the command line — the GUI doesn't
reimplement anything, it just shells out to the same `python -m src.main
...` commands and streams their output into a log panel. Pick whichever you
prefer; both drive the same browser client, and only one command can run at
a time (matching the single attached Chrome session).

### Using the GUI

A small Tkinter GUI (stdlib, no extra dependency) wraps all four commands
with their flags as checkboxes/fields, and shows live output:

```powershell
.venv\Scripts\python.exe -m src.gui
```

**Launching it without a terminal:**

| Platform | How |
| --- | --- |
| Windows | Double-click `run_gui.bat` — uses `pythonw.exe` from `.venv` (no console window). |
| Linux/macOS | Run `./run_gui.sh` (first time: `chmod +x run_gui.sh`). If double-clicking opens a text editor instead, right-click → "Run" / "Run in terminal", or run it from a terminal once. |

Both scripts `cd` to the project folder themselves and use the project's
own `.venv` if present, so they work regardless of where you launch them
from.

### Using the command line

Run commands directly: `.venv\Scripts\python.exe -m src.main <command>` —
see [Logging in](#logging-in) above, and the two options below. This is
exactly what the GUI does under the hood, just without the point-and-click
wrapper — useful for scripting, scheduled runs, or if you just prefer a
terminal.

## Option 1 — `reprice`: auto-price from Cardmarket's own averages

Opens each stock article's own product page (a real click on the card's
name link) and reads one of Cardmarket's own price stats, then applies your
pricing rules and writes the result back through Cardmarket's own "Edit
Article" modal.

| Flag | Values | Default | Purpose |
| --- | --- | --- | --- |
| `--price-source` | `trend` / `30d` / `7d` / `1d` | `7d` | Which Cardmarket average to read as the base price. Also a dropdown in the GUI's Reprice tab. |
| `--adjustment-pct` | e.g. `5`, `-5`, `+5%`, `-5%` | `0` | Extra +/- adjustment on top of everything else. Also a field in the GUI. |
| `--dry-run` | flag | off | Only scrapes and writes the report — never touches the browser's prices. |
| `--limit N` | integer | no limit | Caps how many articles get a changed price this run. |
| `--debug-browser` | flag | off | Dumps page HTML/screenshots to `reports/` if something looks wrong. |

```powershell
# Never touches the browser -- just scrapes, computes, writes a report
.venv\Scripts\python.exe -m src.main reprice --dry-run --debug-browser --limit 5

# Small live test once the dry-run report looks right
.venv\Scripts\python.exe -m src.main reprice --limit 5

# Use the 30-day average instead of the 7-day default, list 5% above it
.venv\Scripts\python.exe -m src.main reprice --price-source 30d --adjustment-pct +5%

# Full run
.venv\Scripts\python.exe -m src.main reprice
```

Writes `reports/pricing_<timestamp>.csv` (every row, old vs. new price) so
you can review before trusting a full run. In the GUI, the Reprice tab's
"Generate price changes CSV only (dry run)" button is a one-click shortcut
for exactly `reprice --dry-run` — it never touches the browser's prices,
just writes that report.

> [!NOTE]
> **Foil pricing:** Cardmarket's product page has an "Only Foils?" toggle
> that switches its price stats between the foil and non-foil variant — and
> that toggle turned out to be a *session-level* filter, not scoped to a
> single tab. `reprice` checks the toggle's actual current state before
> every row and only clicks it when that row's own foil-ness doesn't
> already match, in either direction — so a foil row earlier in a run can't
> leave the filter stuck on and silently foil-price a non-foil row read
> later in the same run. If a particular card's page doesn't have the
> toggle at all, it falls back to the page's current default and logs a
> warning — check the pricing report for rows that look off.

If a single article's "Edit Article" modal doesn't open or close within 10s
(e.g. a validation error keeps it open), that one article is dumped to
`reports/browser_apply_..._stuck_*.html` and skipped rather than crashing
the whole run — check the final "Applied N, unmatched N, failed N" line and
the dumped file(s) for any that need a manual look.

## Option 2 — `export` / `import`: manual CSV editing

For when you'd rather review/adjust prices yourself (e.g. in a spreadsheet)
instead of trusting the automatic pricing formula.

```powershell
# 1. Scrape your current stock into a CSV
.venv\Scripts\python.exe -m src.main export
# -> reports/stock_export_<timestamp>.csv

# 2. Open it (Excel/Sheets/a text editor) and edit ONLY the "price" column.
#    Don't touch id_article/name/expansion/condition/foil/language -- those
#    are what's used to find the same row again on import.

# 3. Apply your edited prices back
.venv\Scripts\python.exe -m src.main import --dry-run   # review first
.venv\Scripts\python.exe -m src.main import
```

`import` with no `--input` picks the newest `reports/stock_export_*.csv`
automatically. The CSV format is this tool's own (see
`src/cardmarket/csv_io.py`): comma-delimited, UTF-8 with BOM —

```
id_article,name,expansion,collector_number,foil,condition,language,quantity,price
```

## Shared behavior

**Pricing formula** (`reprice` only, `src/pricing.py`):

```
new_price = source_price × (1 + adjustment_pct / 100)
```

...then clamped to `[MIN_PRICE, MAX_PRICE]` and rounded to the nearest
`PRICE_STEP`.

- `source_price` — whichever Cardmarket stat `--price-source` selected
  (`PRICE_SOURCE_LABELS` in `src/cardmarket/browser_client.py`).
- `adjustment_pct` — `--adjustment-pct` (default `0`).

**Other shared behavior:**

- Both commands that touch prices (`reprice`, `import`) respect
  `MAX_CHANGES_PER_RUN` / `--limit`, so you can roll out changes gradually.
- Both write a `logs/run_<timestamp>.log`. What you see on screen (and in
  the GUI's log panel) is deliberately trimmed down to a clock time and the
  message, with a `[WARNING]`/`[ERROR]` tag only when something needs your
  attention; the log **file** keeps the full `timestamp LEVEL logger.name:
  message` detail, which is what to share if something needs
  troubleshooting.
- **Live progress:** every browser action logs when it starts and
  finishes, so you can follow along in the terminal or the GUI's log panel
  instead of staring at a blank screen. Reading prices logs each card as
  it's read (`Reading trend price for 'Card Name' (card 12)...` /
  `Read trend price for 'Card Name': 3.45 EUR`); applying updates logs each
  one as it's written (`Updating card 3/40: 'Card Name' (NM) 2.50 -> 2.60
  EUR...` / `Updated 'Card Name' -- 3/40 card(s) updated so far.`), so the
  running "how many cards updated" count is always visible, not just the
  final summary line.

## Tuning click speed

Every simulated action (each click/fill) waits a random delay between
`BROWSER_ACTION_DELAY_MIN_SECONDS` and `BROWSER_ACTION_DELAY_MAX_SECONDS`
(default `1.5`–`4.0`s) before firing. This is deliberate, not
network/rendering wait time: Cardmarket sits behind Cloudflare's bot
management, and a burst of clicks with no human-scale pauses between them
is exactly the pattern that gets flagged (see [Why it works this
way](#why-it-works-this-way)).

You *can* lower these to speed a run up:

```
BROWSER_ACTION_DELAY_MIN_SECONDS=0.5
BROWSER_ACTION_DELAY_MAX_SECONDS=1.5
```

but treat it as a real trade-off, not a free win — the lower you go, the
more this starts to look like a bot rather than a person clicking through
the site. If you do lower it, test with a small `--limit` first and watch
for a `CardmarketBrowserBlockedError` (an immediate hard stop the moment a
Cloudflare block/challenge page is detected).

## ⚠️ Before trusting this on your real inventory

Selectors in `src/cardmarket/browser_client.py` (`SELECTORS`) were verified
end-to-end against a real account for reading stock, pagination, and login
detection. **The submit-and-confirm step of applying a price
(`apply_price_updates`, used by both `reprice` and `import` without
`--dry-run`) is still unverified** against a live account — start with
`--dry-run --debug-browser --limit 1`, run `--inspect-modal` first, and
share the `reports/browser_apply_*` dump if something doesn't work as
expected.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

Covers pricing (`src/pricing.py`), the CSV export/import round-trip
(`src/cardmarket/csv_io.py`), pure helpers in the browser client
(`is_blocked`, `make_match_key`, tab picking), the CLI's argument parsing
(`src/main.py`), and the GUI's CLI-arg building (`src/gui.py`) — all
network-free logic. The actual browser interaction was verified manually
against a live account (see above), not by these tests.

## Project layout

```
update-inventory/
├── src/
│   ├── main.py                # CLI entry point (login, reprice, export, import)
│   ├── gui.py                 # Tkinter GUI wrapping the CLI
│   ├── pricing.py             # Pure price-calculation logic
│   ├── config.py              # .env-driven configuration
│   └── cardmarket/
│       ├── browser_client.py  # Playwright/CDP browser automation
│       └── csv_io.py          # Stock CSV read/write
├── tests/                     # pytest suite (network-free)
├── reports/                   # Generated CSVs + debug dumps
├── logs/                      # Per-run logs
├── run_gui.bat / run_gui.sh   # Double-click GUI launchers
└── .env.example                # Copy to .env and fill in
```
