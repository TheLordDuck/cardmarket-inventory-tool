# update-inventory

Repricing tool for a Cardmarket (Magic: The Gathering) seller account. Two
workflows, both driven through your own browser session — no official API
access is available on this account, and Cardmarket has no native stock
export/import feature, so both work by attaching to your own, already-open,
already-logged-in Chrome and clicking through the site the same way you
would by hand.

Everything always starts from the same English-locale URL
(`https://www.cardmarket.com/en/Magic`, `LOGIN_URL` in
`src/cardmarket/browser_client.py`) and the same login form — so the page
markup/selectors this tool relies on stay consistent run to run, regardless
of what language or page your account might otherwise default to.

## Why not full automation

An earlier version of this tool launched a *fresh* Playwright-controlled
Chrome and had the script `page.goto()` straight to the login page. On the
very first real run that `goto()` alone — before any human interaction —
got an immediate Cloudflare WAF block ("Sorry, you have been blocked...").
This version instead **attaches to your own, already-running Chrome** over
the Chrome DevTools Protocol and never calls `.goto()` against
cardmarket.com at all (except as Chrome's own startup URL when launching it
for you, which isn't scripted browser navigation) — it only reads the DOM or
dispatches `.click()`/`.fill()` on elements already on screen, the same kind
of interaction a real click produces. It's not risk-free — Cloudflare's bot
management watches behavior for the whole session — but it's the smallest
risk surface available without official API access.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chrome

copy .env.example .env          # then edit .env (see "Configuration" below)
```

### One-command Chrome startup

Both commands can launch Chrome themselves — no manual PowerShell command
needed. Chrome refuses to enable remote debugging on your **default**
profile (a real security restriction, not a bug here), so a **separate,
dedicated profile** is used instead (`.cardmarket-browser-profile/`,
gitignored), only if nothing's already listening on `CARDMARKET_CDP_URL` —
if Chrome is already running with that debugging port open (e.g. from a
previous run), it's left alone and reused as-is.

**`reprice`/`export`/`import` close that Chrome again once they finish**
(success or failure) -- but only if the command launched it itself this run;
a Chrome you already had open (or attached to via `CARDMARKET_CDP_URL`,
including one left open by `login`, see below) is never touched. Your login
stays intact across closes/relaunches (cookies persist in the dedicated
profile), so the next command just reuses it.

**`login` is the one exception -- it never closes the browser itself.** Its
whole job is to leave you with an open, logged-in Chrome ready for whatever
you run next (or to look at yourself); auto-closing it there just meant it
flashed open and shut again immediately whenever 2FA wasn't even needed.

Optionally set `CARDMARKET_USERNAME` / `CARDMARKET_PASSWORD` in `.env`
(never commit this file — already gitignored) and each command will also
fill in your login form for you. **It never touches 2FA/CAPTCHA** — that
has to stay a human step:

- If logged out, it fills in your username/password on Cardmarket's login
  form, then **waits** (up to `LOGIN_WAIT_TIMEOUT_SECONDS`, default 5
  minutes) for you to complete 2FA/verification in the browser window —
  once you do, it continues automatically, no need to rerun anything.
- Run `login` on its own first if you'd rather get 2FA out of the way
  before starting an actual `reprice`/`export`/`import`:
  ```powershell
  .venv\Scripts\python.exe -m src.main login
  ```
  (also available as its own tab in the GUI)

If you'd rather log in by hand, close every open Chrome window completely
(Windows silently ignores the debugging flag if any Chrome process is still
running — check with `Get-Process chrome`), then:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$PWD\.cardmarket-browser-profile"
```

and log into Cardmarket in that window yourself before running either
command. Either way, this debugging port has no authentication — anything
running locally can fully control that browser while it's open, so close it
when you're done.

Before ever dropping `--dry-run`, verify the Edit Article modal's real field
selectors with the safe inspect mode below (works with both commands below)
— it opens the modal for one article, dumps its HTML/screenshot to
`reports/`, and closes it again **without filling or submitting anything**:

```powershell
.venv\Scripts\python.exe -m src.main reprice --inspect-modal --debug-browser --limit 1
```

## Running the project

Every command below (`login`, `reprice`, `export`, `import`) can be run
either through the GUI or directly on the command line — the GUI doesn't
reimplement anything, it just shells out to the same `python -m src.main ...`
commands and streams their output into a log panel. Pick whichever you
prefer; both drive the same browser client.

### With the GUI

A small Tkinter GUI (stdlib, no extra dependency) wraps all four commands
with their flags as checkboxes/fields, and shows live output:

```powershell
.venv\Scripts\python.exe -m src.gui
```

Only one command can run at a time (matches the browser attaching to a
single Chrome session).

**Launching it without a terminal:**

- **Windows:** double-click `run_gui.bat`. It uses `pythonw.exe` from `.venv`
  (no console window) and closes its own window immediately.
- **Linux/macOS:** run `./run_gui.sh` (first time: `chmod +x run_gui.sh`).
  Whether double-clicking it works depends on your file manager's settings
  for executable `.sh` files — if it just opens a text editor, right-click →
  "Run" / "Run in terminal", or run it from a terminal once.

Both scripts `cd` to the project folder themselves and use the project's own
`.venv` if present, so they work regardless of where you double-click them
from.

### Without the GUI (command line)

Run the same commands directly with `.venv\Scripts\python.exe -m src.main
<command>` — see `login` above, and `reprice`/`export`/`import` below. This
is exactly what the GUI does under the hood, just without the point-and-click
wrapper; useful for scripting, scheduled runs, or if you just prefer a
terminal.

## Option 1 — `reprice`: auto-price from Cardmarket's own trend price

Opens each stock article's own product page (a real click on the card's
name link), reads one of Cardmarket's own price stats -- **Price Trend**,
**30-day**, **7-day** (the default) or **1-day average price**, picked with
`--price-source trend|30d|7d|1d` (or the "Price source" dropdown in the
GUI's Reprice tab) -- applies `PRICE_MULTIPLIER` from `.env` (default `1.0`
— list at exactly that price; e.g. `0.95` would list 5% below it), a
per-condition multiplier, and an optional `--adjustment-pct` (e.g. `5` or
`-5`, also accepts a leading `+` and/or trailing `%` such as `+5%`/`-5%` --
the "Adjustment %" field in the GUI) on top of all that, then writes the
result back through Cardmarket's own "Edit Article" modal.

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

**Foil rows**: Cardmarket's product page shows the *non-foil* price panel by
default even when opened from a foil listing — confirmed live to otherwise
badly undervalue foils (a real 2.88€ foil dropped to 0.25€, the non-foil
price). For a foil row, `reprice` now also clicks that page's own "Only
Foils?" toggle (a real click, same as everything else here) before reading
the price, which switches the panel to the foil-specific trend. If a
particular card's page doesn't have that toggle, it falls back to the
default number and logs a warning — check the pricing report for foil rows
that look off.

If a single article's "Edit Article" modal doesn't open or close within 10s
(e.g. a validation error keeps it open), that one article is dumped to
`reports/browser_apply_..._stuck_*.html` and skipped rather than crashing
the whole run — check the final "Applied N, unmatched N, failed N" line and
the dumped file(s) for any that need a manual look.

## Option 2 — `export` / `import`: manual CSV editing

For when you'd rather review/adjust prices yourself (e.g. in a spreadsheet)
instead of trusting the automatic 7-day-average formula.

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
`id_article,name,expansion,collector_number,foil,condition,language,quantity,price`.

## Shared pieces

- **Pricing** (`reprice` only) — `new_price = source_price × PRICE_MULTIPLIER × condition_multiplier
  × (1 + adjustment_pct / 100)`, clamped to `[MIN_PRICE, MAX_PRICE]` and rounded to `PRICE_STEP`
  (`src/pricing.py`), where `source_price` is whichever of Cardmarket's own
  stats `--price-source` selected (`PRICE_SOURCE_LABELS` in
  `src/cardmarket/browser_client.py`) and `adjustment_pct` is
  `--adjustment-pct` (default `0`). Per-condition multipliers live in
  `DEFAULT_CONDITION_MULTIPLIERS` in `src/config.py`.
- Both commands that touch prices (`reprice`, `import`) respect
  `MAX_CHANGES_PER_RUN` / `--limit` so you can roll out changes gradually.
- Both write a `logs/run_<timestamp>.log`. What you see on screen (and in
  the GUI's log panel) is deliberately trimmed down to a clock time and the
  message, with a `[WARNING]`/`[ERROR]` tag only when something needs your
  attention; the log **file** keeps the full `timestamp LEVEL logger.name:
  message` detail, which is what to share if something needs troubleshooting.
- **Live progress**: every browser action logs when it starts and finishes,
  so you can follow along in the terminal or the GUI's log panel instead of
  staring at a blank screen. Reading trend prices logs each card as it's
  read (`Reading trend price for 'Card Name' (card 12)...` /
  `Read trend price for 'Card Name': 3.45 EUR`); applying updates logs each
  one as it's written (`Updating card 3/40: 'Card Name' (NM) 2.50 -> 2.60
  EUR...` / `Updated 'Card Name' -- 3/40 card(s) updated so far.`), so the
  running "how many cards updated" count is always visible, not just the
  final summary line.

### Making the clicker faster

Every simulated action (each click/fill) waits a random delay between
`BROWSER_ACTION_DELAY_MIN_SECONDS` and `BROWSER_ACTION_DELAY_MAX_SECONDS`
(`.env`, default `1.5`–`4.0`s) before firing — this is deliberate, not
network/rendering wait time: Cardmarket sits behind Cloudflare's bot
management, and a burst of clicks with no human-scale pauses between them is
exactly the pattern that gets flagged (see "Why not full automation" above).
You *can* lower these to speed a run up, e.g.:

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
detection. **The submit-and-confirm step of applying a price (`apply_price_updates`,
used by both `reprice` and `import` without `--dry-run`) is still unverified**
against a live account — start with `--dry-run --debug-browser --limit 1`,
run `--inspect-modal` first, and share the `reports/browser_apply_*` dump if
something doesn't work as expected.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

Covers pricing (`src/pricing.py`), the CSV export/import round-trip
(`src/cardmarket/csv_io.py`), pure helpers in the browser client
(`is_blocked`, `make_match_key`, tab picking), and the GUI's CLI-arg building
(`src/gui.py`) — all network-free logic. The actual browser interaction was
verified manually against a live account (see above), not by these tests.
