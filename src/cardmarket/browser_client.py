"""Direct-browser client for Cardmarket that attaches to your own, already-
running, already-logged-in Chrome instead of launching a new one (see
README).

History: a first version of this launched a *fresh* Playwright-controlled
Chrome (persistent profile, non-headless, human did login/2FA/captcha) and
had the script `page.goto()` straight to the login page. On the very first
real run that `goto()` itself got an immediate Cloudflare WAF block --
"Sorry, you have been blocked" -- before any human interaction happened at
all. So a scripted, referrer-less `goto()` can be enough to trip the WAF
regardless of headless/2FA/persistent-profile mitigations.

This version removes that entirely: it never calls `page.goto()` against
cardmarket.com. Instead it connects Playwright to your real, already-open
Chrome via the Chrome DevTools Protocol (`connect_over_cdp`), finds a tab
you already have open and logged into Cardmarket, and only ever reads the
DOM or dispatches `.click()`/`.fill()` on elements already rendered there --
the same kind of interaction a real click produces, mirroring why a
browser-extension-driven click (rather than a remote-controlled bot) isn't
flagged either. You drive every navigation (opening the login page, the
stock page, paging through it if pagination isn't link-based) yourself, in
the browser, *before* running the CLI command.

Note this CLI runs non-interactively (no live stdin to block on), so
`ensure_logged_in`/`ensure_on_stock_page` never wait for you mid-run --
they just check the tab's *current* state and raise a clear error telling
you what to do if it's not ready. The workflow is: do the step yourself in
the browser, then (re)run the command.

This is *not* risk-free -- Cloudflare's bot management watches behavior
continuously, and repeated `.click()`s with no mouse movement between them
can still look automated. Mitigations kept from the previous version:
  - randomized human-scale delays between actions (see `delay_range`)
  - bulk edits per page (one save click per page of rows), not one
    request per article
  - `is_blocked()` is checked after every click/save; the moment a
    Cloudflare challenge/block page is detected, everything stops
    immediately (`CardmarketBrowserBlockedError`) rather than retrying

Setup required once per Chrome session: Chrome refuses to enable the CDP
port on your *default* profile (a real Chrome security restriction), so
this needs a separate, dedicated profile. Close every Chrome window fully
(Windows silently ignores the flags below if any Chrome process is still
running), then:

    & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
      --remote-debugging-port=9222 --user-data-dir="<project>\\.cardmarket-browser-profile"

Log into Cardmarket in the Chrome window that opens (that profile persists,
so later runs stay logged in), then run `reprice` or `export`. Note the
debugging port has no authentication -- anything running locally (or
reachable, if you're on a shared/untrusted network) can fully control that
browser session while it's open. Close that Chrome window (or restart it
without the flag) once you're done.

CSS selectors below (`SELECTORS`) started as a best-effort guess. Run with
`debug_dir` set (the `--debug-browser` CLI flag) and, the moment something
doesn't match, open the dumped `browser_*.html` / `.png` in reports/ and
correct the relevant SELECTORS entry.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Fragments (checked case-insensitively) that show up on Cloudflare's block/
# challenge pages. Kept broad on purpose -- a false positive here just aborts
# a run early, which is cheap; a false negative risks hammering a block page.
BLOCK_MARKERS = [
    "error 1015",
    "you have been blocked",
    "checking your browser",
    "attention required! | cloudflare",
    "cf-error-details",
    "ray id:",
    "please stand by, while we are checking your browser",
    "access denied",
]

# Candidate CSS/text selectors, tried in order, per page element. Verified
# end-to-end against a real account on 2026-09-15: login detection, the
# Selling -> My Offers navigation, the cookie-consent banner, pagination
# (next/prev, including recovering from being left mid-listing on a
# non-first page), and reading all fields on every row across a real
# multi-page stock (34 articles / 2 pages) all confirmed working, producing
# a correct pricing report matched against live Scryfall data. The stock
# listing shows price/quantity as plain text, not inputs -- there's no
# page-level bulk-edit form. Editing a row opens a modal (see
# `edit_link`/`modal_*` below); its container becomes visible immediately
# but the real form loads via AJAX afterwards, showing only a spinner
# (`modal_loader`) until ready. Confirmed via a live dump on 2026-09-16:
# `modal_price_input`/`modal_submit`/`modal_close` all match the real "Edit
# Article" form (a `name="price"` text input, a `type="submit"` button).
# Only the actual submit-and-confirm step of `apply_price_updates` remains
# untested against a live account -- everything up to filling the price
# field has been verified read-only. The quantity +/- stepper buttons use an
# obfuscated `jcp(...)` JS call this client deliberately does not touch.
SELECTORS: dict[str, list[str]] = {
    "cookie_banner": ["#CookiesConsent"],
    "cookie_accept_required": ["[data-testid='AcceptRequiredCookies']", "button[aria-label='Only Required Cookies']"],
    "logged_in_marker": ["a[href*='Logout']", "#logout-link"],
    "selling_menu": ["#selling-dropdown"],
    "my_offers_link": ["a[href*='/Stock/Offers']", "a[title='My Offers']"],
    # 'My Offers' sometimes lands on a category *overview* page (a gallery
    # card per game category, e.g. "Singles" with a count) instead of
    # jumping straight to the Singles listing -- confirmed happening on
    # 2026-09-16, session-state dependent (why wasn't pinned down). This
    # drills into it with one more real click.
    "singles_category_link": ["a[href$='/Stock/Offers/Singles']"],
    "row": [".article-row", "[data-article-id]"],
    "name": [".col-seller a", ".col-seller .name a", "a.article-link"],
    "expansion": [".expansion-symbol"],
    "condition": [".article-condition .badge", ".article-condition"],
    "language": [".product-attributes .icon[aria-label]:not(.st_SpecialIcon):not(.article-condition)"],
    "foil_marker": [".st_SpecialIcon"],
    "price_text": [".col-offer .price-container .color-primary", ".col-offer .price-container span"],
    "quantity_text": [".col-offer .amount-container .item-count", "input.amount-input"],
    "edit_link": ["a[data-modal*='EditArticleModal']"],
    "next_page": ["a[data-direction='next']:not(.disabled)", "a[aria-label='Next page']:not(.disabled)"],
    "prev_page": ["a[data-direction='prev']:not(.disabled)"],
    # Inside the edit modal (#modal), once opened -- unverified, see
    # `apply_price_updates` docstring; correct once you've inspected a real
    # dump of the opened modal.
    "modal_price_input": ["#modal input[name*='price' i]", "#modal input[type='number'][name*='price' i]"],
    "modal_submit": ["#modal button[type='submit']", "#modal button:has-text('Save')"],
    "modal_close": ["#modal .btn-close", "#modal [data-bs-dismiss='modal']", "#modal button[aria-label='Close']"],
    # The modal container (#modal) becomes visible immediately on click, but
    # its real form content loads via a separate AJAX call and starts out as
    # just this spinner -- confirmed via a live dump on 2026-09-16, where
    # #modal contained nothing but the close button and a `.loader`/
    # `.spinner` right after becoming visible, no form fields yet.
    "modal_loader": ["#modal .loader"],
    # A card's product page (opened by Ctrl+clicking `name`'s link) shows
    # this labeled dt/dd list in its "Info" tab, including "Price Trend",
    # "30-days average price", "7-days average price" and "1-day average
    # price" -- confirmed against a live account on 2026-09-16.
    #
    # IMPORTANT (confirmed live 2026-09-21): this panel defaults to the
    # NON-FOIL trend regardless of which variant the row itself is -- a
    # foil row's product page still shows the same numbers as the non-foil
    # one unless the page's own "Only Foils?" toggle (below) is clicked
    # first, which reloads the panel scoped to foil listings only (confirmed
    # via a live dump: a card's 7-days average went from 0.25 to 2.74 after
    # toggling). This bug produced badly-undervalued prices for every foil
    # row (e.g. a real 2.88 listing repriced down to 0.25).
    "product_info_dl": ["dl.labeled"],
    # The "Only Foils?" switch inside the same "Info" tab as product_info_dl.
    # Its underlying `<input type="checkbox" name="foilMode">` is CSS-hidden
    # (a styled toggle switch), so it must be clicked via this visible
    # `<label>` wrapper, not the input itself (Playwright's actionability
    # check fails on a zero-size hidden input even with force=True). A real
    # click on it runs the page's own `onchange` handler, which sets a
    # hidden filter field and submits the page's own filter form -- a normal
    # in-page navigation triggered by a real click, not a scripted `.goto()`.
    "foil_price_toggle": ["label.switch-button:has(input[name='foilMode'])"],
    # Login form -- confirmed against a live (logged-out) dump on
    # 2026-09-16: it's `#header-login`, a desktop-header form (a duplicate,
    # differently-scoped one exists for the mobile offcanvas nav, hence
    # scoping these to `#header-login` specifically rather than matching
    # either). The submit control is `<input type="submit">`, not a
    # `<button>` -- that mismatch was the original bug.
    #
    # `login_link`'s first candidate used to be the unqualified
    # `a[href*='Login']` -- confirmed (2026-09-21 dump) to be a real bug:
    # the only real matches for that on a live page are the "Forgot
    # Username"/"Forgot Password" links (`/Login/ForgotUsername` etc, both
    # `class="forgot-link"`) and the language-switcher's `/es/Magic/Login`
    # variant, never an actual "open the login form" trigger. `.first` on
    # that locator clicked "Forgot Username" instead, landing on (and
    # switching the page to Spanish via) that page instead of logging in.
    # `:not(.forgot-link)` excludes those; `#login-link` / `a[title='Login']`
    # are tried first regardless since they're more specific.
    "login_link": ["#login-link", "a[title='Login']", "a[href*='Login']:not(.forgot-link)"],
    "login_username_input": ["#header-login input[name='username']"],
    "login_password_input": ["#header-login input[name='userPassword']"],
    "login_submit": ["#header-login input[type='submit']"],
}

# Real URL fragment for the stock/article management page ("My Offers"),
# confirmed against a live account -- used to detect whether we're already
# there.
STOCK_PAGE_URL_MARKER = "/stock/offers"

# The labeled dt/dd pairs available in a product page's "Info" tab
# (SELECTORS['product_info_dl']), keyed by the short CLI/GUI-facing name
# `reprice --price-source` accepts.
PRICE_SOURCE_LABELS: dict[str, str] = {
    "trend": "Price Trend",
    "30d": "30-days average price",
    "7d": "7-days average price",
    "1d": "1-day average price",
}
DEFAULT_PRICE_SOURCE = "7d"

# Passed as Chrome's startup URL when launching it ourselves (see
# `main.cmd_browser_reprice`'s auto-launch step) so the browser's own,
# non-automation-driven navigation lands on cardmarket.com -- never a
# Playwright/CDP `.goto()` after attaching (see module docstring for why
# that specifically is what's avoided here).
LOGIN_URL = "https://www.cardmarket.com/en/Magic"


def is_blocked(html: str, title: str = "") -> bool:
    haystack = f"{title}\n{html}".lower()
    return any(marker in haystack for marker in BLOCK_MARKERS)


class CardmarketBrowserError(RuntimeError):
    pass


class CardmarketBrowserBlockedError(CardmarketBrowserError):
    """Raised the instant a Cloudflare block/challenge page is detected.
    Never caught-and-retried automatically inside this module -- surface it,
    stop, and let a human decide whether/when to try again.
    """


class SelectorNotFoundError(CardmarketBrowserError):
    pass


@dataclass
class BrowserRow:
    match_key: str
    name: str
    expansion: str
    collector_number: str
    foil: bool
    condition: str
    language: str
    quantity: int
    current_price: float
    id_article: str = ""
    id_product: int | None = None
    product_url: str = ""
    trend_price: float | None = None


_ROW_ID_RE = re.compile(r"stockRow(\d+)")


def make_match_key(name: str, expansion: str, condition: str, language: str, foil: bool) -> str:
    return "|".join([name.strip().lower(), expansion.strip().lower(), condition.strip().lower(),
                      language.strip().lower(), "foil" if foil else "nonfoil"])


def pick_cardmarket_tab_index(urls: list[str]) -> int | None:
    """Given the URLs of a browser's currently-open tabs, returns the index
    of the first one already on cardmarket.com, or None if there isn't one.
    """
    for i, url in enumerate(urls):
        if "cardmarket.com" in url:
            return i
    return None


class CardmarketBrowserClient:
    def __init__(
        self,
        cdp_url: str,
        debug_dir: Path | None = None,
        delay_range: tuple[float, float] = (1.5, 4.0),
    ):
        self.cdp_url = cdp_url
        self.debug_dir = debug_dir
        self.delay_range = delay_range
        self._playwright = None
        self._browser = None
        self.page = None

    def __enter__(self) -> "CardmarketBrowserClient":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise CardmarketBrowserError(
                f"Couldn't connect to Chrome at {self.cdp_url} ({exc}). Close every Chrome window "
                "completely, then relaunch it with the debugging flag, e.g.:\n"
                '  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                "--remote-debugging-port=9222\n"
                "then log into Cardmarket in that window and try again."
            ) from exc

        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        idx = pick_cardmarket_tab_index([p.url for p in context.pages])
        self.page = context.pages[idx] if idx is not None else (
            context.pages[0] if context.pages else context.new_page()
        )
        return self

    def __exit__(self, *exc) -> None:
        # Deliberately does NOT close the context/browser: this is the
        # user's real, everyday Chrome with their own other tabs open.
        # Only tear down Playwright's own connection to it.
        if self._playwright is not None:
            self._playwright.stop()

    def _human_delay(self) -> None:
        time.sleep(random.uniform(*self.delay_range))

    def _dump_debug(self, label: str) -> None:
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        (self.debug_dir / f"browser_{label}_{stamp}.html").write_text(self.page.content(), encoding="utf-8")
        try:
            self.page.screenshot(path=str(self.debug_dir / f"browser_{label}_{stamp}.png"), full_page=True)
        except Exception:
            logger.debug("Screenshot capture failed for %s (non-fatal)", label)
        logger.warning("Dumped page state for %r to %s for inspection", label, self.debug_dir)

    def _check_not_blocked(self, label: str) -> None:
        html = self.page.content()
        title = self.page.title()
        if is_blocked(html, title):
            self._dump_debug(f"blocked_{label}")
            raise CardmarketBrowserBlockedError(
                f"Cloudflare block/challenge detected at {label!r} (title: {title!r}). Stopping now -- "
                "do not retry immediately. Close this browser session and wait a while before trying again."
            )

    def _find(self, page_or_locator, kind: str):
        for selector in SELECTORS[kind]:
            locator = page_or_locator.locator(selector)
            if locator.count() > 0:
                return locator
        return None

    def _require(self, page_or_locator, kind: str):
        locator = self._find(page_or_locator, kind)
        if locator is None:
            self._dump_debug(f"selector_miss_{kind}")
            raise SelectorNotFoundError(
                f"None of the candidate selectors for {kind!r} matched "
                f"({SELECTORS[kind]}). Cardmarket's real markup differs from what's assumed here -- "
                f"open the dumped browser_selector_miss_{kind}_*.html and add the real selector to "
                f"SELECTORS[{kind!r}] in src/cardmarket/browser_client.py."
            )
        return locator

    def _wait_for_modal_ready(self) -> None:
        """Call after `#modal` becomes visible, before reading/filling
        anything inside it -- its real form content only appears once the
        loading spinner (SELECTORS['modal_loader']) is gone. See that
        selector's comment for how this was confirmed.
        """
        try:
            self.page.wait_for_selector(SELECTORS["modal_loader"][0], state="hidden", timeout=15000)
        except Exception:
            logger.debug("Modal loader didn't confirm hidden within timeout (non-fatal); continuing.")

    def looks_logged_in(self) -> bool:
        return self._find(self.page, "logged_in_marker") is not None

    def _dismiss_cookie_banner(self) -> None:
        """The fixed-position cookie consent banner intercepts clicks on
        anything underneath it (confirmed: it blocked the pagination "next"
        button) until dismissed. Picks "Only Required Cookies" rather than
        "Accept All" since that's the more conservative choice available.
        """
        banner = self._find(self.page, "cookie_banner")
        if banner is None or banner.count() == 0:
            return
        accept = self._find(self.page, "cookie_accept_required")
        if accept is None:
            return
        self._human_delay()
        accept.first.click()
        try:
            self.page.wait_for_selector(SELECTORS["cookie_banner"][0], state="hidden", timeout=10000)
        except Exception:
            logger.debug("Cookie banner didn't confirm hidden after clicking (non-fatal)")

    def ensure_logged_in(self) -> None:
        """Never navigates and never blocks on input -- CLI invocations here
        run non-interactively (no live stdin), so this just checks the
        attached tab's *current* state and fails fast with instructions if
        it's not ready yet. Log in yourself in the Chrome window, then rerun
        the command.
        """
        self._check_not_blocked("initial_check")

        if "cardmarket.com" not in self.page.url:
            raise CardmarketBrowserError(
                "The attached Chrome tab isn't on cardmarket.com yet (current URL: "
                f"{self.page.url!r}). Open cardmarket.com yourself in that Chrome window, "
                "then rerun this command."
            )

        self._dismiss_cookie_banner()

        if not self.looks_logged_in():
            self._dump_debug("login_not_detected")
            raise CardmarketBrowserError(
                "Doesn't look logged in yet (none of SELECTORS['logged_in_marker'] matched). Log in "
                "(2FA, Cloudflare check, all of it) yourself in that Chrome window, then rerun this "
                "command. If you're sure you're actually logged in, check the dumped "
                "browser_login_not_detected_*.html/.png in reports/ and correct SELECTORS['logged_in_marker']."
            )
        logger.info("Detected an already-logged-in Cardmarket session in this tab.")

    def attempt_auto_login(self, username: str, password: str) -> bool:
        """Best-effort: fills your username/password into Cardmarket's login
        form with real `.fill()` calls and clicks submit -- never touches
        2FA/CAPTCHA, that has to stay a human's step. If the login form
        isn't immediately on the page, tries clicking a 'Login' link first
        (a real click, never a `.goto()`).

        UNVERIFIED against real markup (see SELECTORS comment) -- there was
        no logged-out session available to check this against while writing
        it. Returns True if a form was found and submitted (go complete 2FA
        in the browser now), False if no login form could be found at all
        (check the dumped browser_login_form_not_found_*.html and correct
        SELECTORS['login_*'] if you're sure you were actually logged out).
        Raises CardmarketBrowserBlockedError immediately if a Cloudflare
        block/challenge is detected instead of a login form.
        """
        self._check_not_blocked("auto_login_check")
        self._dismiss_cookie_banner()

        user_input = self._find(self.page, "login_username_input")
        if user_input is None:
            login_link = self._find(self.page, "login_link")
            if login_link is not None:
                self._human_delay()
                login_link.first.click()
                self._check_not_blocked("after_login_link_click")
                user_input = self._find(self.page, "login_username_input")

        if user_input is None:
            self._dump_debug("login_form_not_found")
            return False

        pass_input = self._require(self.page, "login_password_input")
        submit = self._require(self.page, "login_submit")

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            self._human_delay()
            user_input.first.fill(username, timeout=10000)
            self._human_delay()
            pass_input.first.fill(password, timeout=10000)
            self._human_delay()
            submit.first.click(timeout=10000)
        except PlaywrightTimeoutError as exc:
            self._dump_debug("login_form_not_interactable")
            raise CardmarketBrowserError(
                "Found the login form's fields in the page, but couldn't interact with them within 10s "
                "-- they may be present but hidden (Cardmarket's desktop login form only shows above a "
                "certain window width; a too-narrow Chrome window can hide it while still leaving it in "
                "the DOM). Check the dumped browser_login_form_not_interactable_*.html/.png in reports/, "
                "widen the Chrome window if the form looks cut off there, and try again."
            ) from exc

        self.page.wait_for_load_state("domcontentloaded")
        self._check_not_blocked("after_auto_login_submit")
        return True

    def ensure_on_stock_page(self) -> None:
        """If not already on the Stock/Offers page, gets there itself via a
        real `.click()` through the "Selling" menu -> "My Offers" (confirmed
        real markup, see SELECTORS comment) -- not a scripted `goto()`, but
        also not requiring you to click it by hand every run, now that we're
        working from an authenticated session rather than a cold one. Fails
        fast with instructions if that menu isn't where we expect it.
        """
        self._check_not_blocked("stock_page_check")

        if STOCK_PAGE_URL_MARKER not in self.page.url.lower():
            menu = self._find(self.page, "selling_menu")
            if menu is None:
                self._dump_debug("selling_menu_not_found")
                raise CardmarketBrowserError(
                    "Not on the stock page and couldn't find the 'Selling' menu to navigate there "
                    f"(current URL: {self.page.url!r}). Click your way to your Stock/Articles "
                    "management page yourself in that Chrome window, then rerun this command. Check "
                    "the dumped browser_selling_menu_not_found_*.html/.png and correct "
                    "SELECTORS['selling_menu'] if the real page looks different now."
                )
            self._human_delay()
            menu.first.click()

            link = self._find(self.page, "my_offers_link")
            if link is None:
                self._dump_debug("my_offers_link_not_found")
                raise CardmarketBrowserError(
                    "Opened the 'Selling' menu but couldn't find the 'My Offers' link in it. Click "
                    "your way to your Stock/Articles management page yourself, then rerun this "
                    "command. Check the dumped browser_my_offers_link_not_found_*.html/.png and "
                    "correct SELECTORS['my_offers_link'] if Cardmarket's menu has changed."
                )
            self._human_delay()
            link.first.click()
            self.page.wait_for_load_state("domcontentloaded")
            self._check_not_blocked("after_navigate_to_stock_page")

            if STOCK_PAGE_URL_MARKER not in self.page.url.lower():
                self._dump_debug("stock_page_not_reached")
                raise CardmarketBrowserError(
                    f"Clicked through to 'My Offers' but ended up at {self.page.url!r}, not the "
                    "expected stock page. Check the dump and correct SELECTORS/STOCK_PAGE_URL_MARKER."
                )
            logger.info("Navigated to the Stock/Offers page.")

        self._go_to_first_page()

        if self._find(self.page, "row") is None:
            singles_link = self._find(self.page, "singles_category_link")
            if singles_link is not None:
                self._human_delay()
                singles_link.first.click()
                self.page.wait_for_load_state("domcontentloaded")
                self._check_not_blocked("after_singles_category_click")
                self._go_to_first_page()

        if self._find(self.page, "row") is None:
            self._dump_debug("stock_page_not_detected")
            raise CardmarketBrowserError(
                "On the Stock/Offers page (by URL) but none of SELECTORS['row'] matched its rows, even "
                "after trying to drill into 'Singles' from a possible category-overview page. Check the "
                "dumped browser_stock_page_not_detected_*.html/.png in reports/ and correct "
                "SELECTORS['row'] to match the real markup."
            )

    def _go_to_first_page(self, max_clicks: int = 200) -> None:
        """The stock page can be sitting on any page of results (e.g. left
        over from a previous run) when we attach -- confirmed: a run once
        silently scraped only page 2 of 2 this way. Clicks "previous" for
        real until it's gone/disabled, so `get_stock` always starts clean.
        """
        for _ in range(max_clicks):
            prev_link = self._find(self.page, "prev_page")
            if prev_link is None or prev_link.count() == 0:
                return
            self._human_delay()
            prev_link.first.click()
            self.page.wait_for_load_state("domcontentloaded")
            self._check_not_blocked("go_to_first_page")

    def _row_to_browser_row(self, row) -> BrowserRow | None:
        name_el = self._find(row, "name")
        if name_el is None:
            return None
        name = name_el.first.inner_text().strip()

        expansion_el = self._find(row, "expansion")
        if expansion_el is not None:
            expansion = (expansion_el.first.get_attribute("data-bs-original-title")
                         or expansion_el.first.get_attribute("aria-label") or "").strip()
        else:
            expansion = ""

        condition_el = self._find(row, "condition")
        condition = (condition_el.first.inner_text().strip() if condition_el else "NM") or "NM"

        price_el = self._find(row, "price_text")
        if price_el is None:
            return None
        current_price = _safe_float(price_el.first.inner_text())

        quantity_el = self._find(row, "quantity_text")
        quantity = _safe_int(quantity_el.first.inner_text()) if quantity_el else 1

        foil = self._find(row, "foil_marker") is not None

        row_id = row.get_attribute("id") or ""
        id_match = _ROW_ID_RE.match(row_id)
        id_article = id_match.group(1) if id_match else ""

        match_key = make_match_key(name, expansion, condition, "", foil)
        product_url = name_el.first.get_attribute("href") or ""
        return BrowserRow(
            match_key=match_key, name=name, expansion=expansion, collector_number="",
            foil=foil, condition=condition, language="", quantity=quantity, current_price=current_price,
            id_article=id_article, product_url=product_url,
        )

    def get_trend_price(self, name_link, foil: bool = False, price_source: str = DEFAULT_PRICE_SOURCE) -> float | None:
        """Opens a stock row's card-name link in a new tab -- a real
        Ctrl+click on an anchor already on the page, never a scripted
        `goto()` -- and reads whichever of Cardmarket's own price stats
        `price_source` selects (see `PRICE_SOURCE_LABELS` -- "trend",
        "30d", "7d" (the default) or "1d") for whatever that link points
        to. Closes the tab again before returning. Returns None if the
        price panel, or that specific stat, isn't present (e.g. a listing
        with no trade history yet shows "N/A").

        If `foil` is True, first clicks the page's own "Only Foils?" toggle
        (SELECTORS['foil_price_toggle'], a real click on a visible label,
        which triggers the page's own filter-form resubmit -- not a scripted
        `.goto()`) before reading the stats. This matters: confirmed live
        that the price panel defaults to the NON-foil trend regardless of
        which variant you arrived from, so skipping this for a foil row
        silently returns the wrong (usually much lower) number. If the
        toggle can't be found, falls back to the page's default number and
        logs a warning rather than failing the whole row.
        """
        card_name = name_link.inner_text().strip()
        ctx = self.page.context
        popup = None
        try:
            with ctx.expect_page() as popup_info:
                self._human_delay()
                name_link.click(modifiers=["Control"])
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            self._human_delay()

            if is_blocked(popup.content(), popup.title()):
                self._dump_debug("product_page_blocked")
                raise CardmarketBrowserBlockedError(
                    "Cloudflare block/challenge detected while reading a product page's price. "
                    "Stopping now -- do not retry immediately."
                )

            if foil:
                toggle = popup.locator(SELECTORS["foil_price_toggle"][0])
                if toggle.count() > 0:
                    self._human_delay()
                    toggle.first.click()
                    popup.wait_for_load_state("domcontentloaded")
                    self._human_delay()
                    if is_blocked(popup.content(), popup.title()):
                        self._dump_debug("product_page_blocked_after_foil_toggle")
                        raise CardmarketBrowserBlockedError(
                            "Cloudflare block/challenge detected after toggling a product page to its "
                            "foil price. Stopping now -- do not retry immediately."
                        )
                else:
                    logger.warning(
                        "%r's product page has no SELECTORS['foil_price_toggle'] match -- using the "
                        "page's default (non-foil) price trend, which is likely inaccurate for this foil "
                        "row. Check its pricing report row and the product page manually.", card_name,
                    )

            dl = popup.locator(SELECTORS["product_info_dl"][0]).first
            if dl.count() == 0:
                return None
            dts, dds = dl.locator("dt"), dl.locator("dd")
            count = min(dts.count(), dds.count())
            stats = {dts.nth(i).inner_text().strip(): dds.nth(i).inner_text().strip() for i in range(count)}
            label = PRICE_SOURCE_LABELS.get(price_source, PRICE_SOURCE_LABELS[DEFAULT_PRICE_SOURCE])
            return _parse_average_price(stats.get(label, ""))
        finally:
            if popup is not None and not popup.is_closed():
                popup.close()

    def get_stock(
        self, max_pages: int = 200, fetch_trend_prices: bool = False,
        price_source: str = DEFAULT_PRICE_SOURCE,
    ) -> list[BrowserRow]:
        """Scrapes your stock/article listing starting from whatever page is
        currently open (see `ensure_on_stock_page`), paging via `.click()` on
        an on-page "next" link -- never a `goto()`. Leaves you back on page 1
        via `page.go_back()` (real browser history navigation, not a scripted
        `goto()`) so `apply_price_updates` can start from the top without
        needing you to click back yourself. See module docstring re:
        selector uncertainty -- pass debug_dir and inspect the dumps if this
        returns nothing or obviously-wrong data.

        With `fetch_trend_prices=True`, also opens each row's product page
        (see `get_trend_price`) to fill in `BrowserRow.trend_price` -- this
        is what makes `reprice`'s prices come straight from Cardmarket
        itself, but it's one extra tab open/close per row, so a full stock
        takes noticeably longer. `price_source` picks which of Cardmarket's
        own stats (see `PRICE_SOURCE_LABELS`) is read.
        """
        items: list[BrowserRow] = []
        page = self.page
        pages_advanced = 0

        for page_num in range(1, max_pages + 1):
            rows = self._require(page, "row")
            count = rows.count()
            for i in range(count):
                row = rows.nth(i)
                parsed = self._row_to_browser_row(row)
                if parsed is None:
                    continue
                if fetch_trend_prices:
                    logger.info("Reading trend price for %r (card %d)...", parsed.name, len(items) + 1)
                    name_link = self._find(row, "name")
                    if name_link is not None:
                        parsed.trend_price = self.get_trend_price(
                            name_link.first, foil=parsed.foil, price_source=price_source,
                        )
                    logger.info(
                        "Read trend price for %r: %s", parsed.name,
                        f"{parsed.trend_price:.2f} EUR" if parsed.trend_price is not None else "not available",
                    )
                items.append(parsed)
            logger.info("Finished scraping page %d (%d card(s) read so far).", page_num, len(items))
            if page_num == 1 and self.debug_dir is not None:
                self._dump_debug("stock_first_page")

            next_link = self._find(page, "next_page")
            if next_link is None or next_link.count() == 0:
                break
            self._human_delay()
            next_link.first.click()
            page.wait_for_load_state("domcontentloaded")
            self._check_not_blocked(f"stock_page_{page_num + 1}")
            pages_advanced += 1

        logger.info("Scraped %d stock rows across %d page(s).", len(items), page_num)

        for _ in range(pages_advanced):
            self._human_delay()
            page.go_back()
            page.wait_for_load_state("domcontentloaded")
        if pages_advanced:
            self._check_not_blocked("stock_page_back_to_1")

        return items

    def inspect_login_page(self) -> bool:
        """Dumps the current page state so you can verify/correct
        SELECTORS['login_*'] against Cardmarket's real markup -- never fills
        or submits anything. Works whether you're logged in or out: if
        SELECTORS['login_username_input'] isn't immediately present, tries
        one real click on SELECTORS['login_link'] (the same fallback
        `attempt_auto_login` uses) and dumps again. Returns True if the
        username input was found (in either dump).

        A raw HTTP fetch of cardmarket.com from outside a real browser
        session gets Cloudflare's "Just a moment..." challenge instead of
        the real page (confirmed independently) -- this is why the login
        selectors can only be verified through your own real, cookied Chrome
        session, the same way every other selector here is.
        """
        if self.debug_dir is None:
            raise CardmarketBrowserError("inspect_login_page needs debug_dir set (pass --debug-browser).")

        self._check_not_blocked("inspect_login_initial")
        self._dismiss_cookie_banner()

        found = self._find(self.page, "login_username_input") is not None
        self._dump_debug("login_page_initial")

        if not found:
            login_link = self._find(self.page, "login_link")
            if login_link is not None:
                self._human_delay()
                login_link.first.click()
                self.page.wait_for_load_state("domcontentloaded")
                self._check_not_blocked("inspect_login_after_link_click")
                found = self._find(self.page, "login_username_input") is not None
                self._dump_debug("login_page_after_link_click")

        return found

    def inspect_edit_modal(self, limit: int = 1, max_pages: int = 200) -> int:
        """Opens the Edit Article modal for the first `limit` rows (paging if
        needed), dumps each one's HTML/screenshot to debug_dir, then closes
        it again -- never fills or submits anything. Use this to verify/
        correct SELECTORS['modal_price_input'] / ['modal_submit'] against a
        real account before ever trusting apply_price_updates (i.e. running
        `reprice` or `import` without --dry-run).
        """
        if self.debug_dir is None:
            raise CardmarketBrowserError("inspect_edit_modal needs debug_dir set (pass --debug-browser).")

        page = self.page
        opened = 0
        for page_num in range(1, max_pages + 1):
            rows = self._require(page, "row")
            count = rows.count()
            for i in range(count):
                if opened >= limit:
                    return opened
                row = rows.nth(i)
                edit_link = self._find(row, "edit_link")
                if edit_link is None:
                    continue

                self._human_delay()
                edit_link.first.click()
                page.wait_for_selector("#modal", state="visible", timeout=10000)
                self._check_not_blocked(f"inspect_modal_open_{opened}")
                self._wait_for_modal_ready()
                self._dump_debug(f"modal_inspect_{opened}")
                opened += 1

                close = self._find(page, "modal_close")
                self._human_delay()
                if close is not None:
                    close.first.click()
                else:
                    page.keyboard.press("Escape")
                try:
                    page.wait_for_selector("#modal", state="hidden", timeout=10000)
                except Exception:
                    logger.warning("Modal didn't confirm hidden after closing (non-fatal); continuing.")
                self._check_not_blocked(f"inspect_modal_close_{opened}")

            if opened >= limit:
                break
            next_link = self._find(page, "next_page")
            if next_link is None or next_link.count() == 0:
                break
            self._human_delay()
            next_link.first.click()
            page.wait_for_load_state("domcontentloaded")
            self._check_not_blocked(f"inspect_modal_page_{page_num + 1}")

        return opened

    def _close_stray_modal(self) -> None:
        """Best-effort cleanup after a stuck modal (see `apply_price_updates`)
        so the next row isn't blocked by it -- never raises.
        """
        try:
            close = self._find(self.page, "modal_close")
            if close is not None:
                close.first.click(timeout=3000)
            else:
                self.page.keyboard.press("Escape")
            self.page.wait_for_selector("#modal", state="hidden", timeout=5000)
        except Exception:
            logger.debug("Couldn't confirm a stray modal closed (non-fatal); continuing.")

    def apply_price_updates(self, new_prices_by_match_key: dict[str, float], max_pages: int = 200) -> dict:
        """Re-walks the stock pages from whatever's currently open. Cardmarket's
        stock page has no bulk-edit form -- each row's price is only editable
        by clicking its pencil icon, which opens Cardmarket's own "Edit
        Article" modal (`#modal`, populated via AJAX from `edit_link`'s
        `data-modal` URL) -- so this is one modal open/fill/submit per
        article, same as a human would do it. `edit_link`, `#modal`,
        `modal_loader`, `modal_price_input` and `modal_submit` have all been
        confirmed against a live account's real markup (see SELECTORS
        comment).

        If a single article's modal doesn't open/close within 10s (confirmed
        to happen live -- e.g. a validation error banner keeping the modal
        open, or just a slow response), that one article is dumped to
        reports/ and counted as `failed` rather than crashing the whole run
        and losing every remaining update.
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        remaining = dict(new_prices_by_match_key)
        total = len(remaining)
        applied = 0
        failed: list[str] = []
        page = self.page

        for page_num in range(1, max_pages + 1):
            rows = self._require(page, "row")
            count = rows.count()
            for i in range(count):
                row = rows.nth(i)
                parsed = self._row_to_browser_row(row)
                if parsed is None or parsed.match_key not in remaining:
                    continue
                new_price = remaining.pop(parsed.match_key)
                label = f"apply_{parsed.id_article or i}"
                progress = applied + len(failed) + 1

                try:
                    logger.info(
                        "Updating card %d/%d: %r (%s) %.2f -> %.2f EUR...",
                        progress, total, parsed.name, parsed.condition, parsed.current_price, new_price,
                    )
                    edit_link = self._require(row, "edit_link")
                    self._human_delay()
                    edit_link.first.click()
                    page.wait_for_selector("#modal", state="visible", timeout=10000)
                    self._check_not_blocked(f"{label}_open")
                    self._wait_for_modal_ready()

                    price_input = self._require(page, "modal_price_input")
                    self._human_delay()
                    price_input.first.fill(f"{new_price:.2f}")

                    submit = self._require(page, "modal_submit")
                    self._human_delay()
                    submit.first.click()
                    page.wait_for_selector("#modal", state="hidden", timeout=10000)
                    self._check_not_blocked(f"{label}_close")
                    applied += 1
                    logger.info("Updated %r -- %d/%d card(s) updated so far.", parsed.name, applied, total)
                except PlaywrightTimeoutError:
                    self._dump_debug(f"{label}_stuck")
                    failed.append(parsed.id_article or parsed.match_key)
                    logger.warning(
                        "Timed out applying the price for %r (id_article=%s) -- its modal never opened/"
                        "closed within 10s (it may be showing a validation error, or was just slow). "
                        "Dumped reports/browser_%s_stuck_*.html/.png -- check it, then retry just this "
                        "article. Continuing with the rest of the run.",
                        parsed.name, parsed.id_article, label,
                    )
                    self._close_stray_modal()

            next_link = self._find(page, "next_page")
            if not remaining or next_link is None or next_link.count() == 0:
                break
            self._human_delay()
            next_link.first.click()
            page.wait_for_load_state("domcontentloaded")
            self._check_not_blocked(f"apply_page_{page_num + 1}")

        if failed:
            logger.warning("%d price update(s) failed (modal timed out) -- see the dumped debug files.",
                            len(failed))
        if remaining:
            logger.warning("%d price update(s) never matched a row on any page (stock may have "
                            "changed since get_stock() ran).", len(remaining))
        return {"applied": applied, "unmatched": len(remaining), "failed": len(failed)}


def _safe_float(text: str) -> float:
    cleaned = re.sub(r"[^\d,.\-]", "", text or "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _safe_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def _parse_average_price(text: str) -> float | None:
    """Unlike `_safe_float` (which deliberately falls back to 0.0 for stock-
    row fields where that's a safe default), a missing/unparseable price
    stat here must come back as None, not 0.0 -- a brand-new listing with no
    trade history shows this as "N/A" or "-", and silently pricing a card at
    EUR 0 would be a real, costly bug rather than a safe fallback.
    """
    cleaned = re.sub(r"[^\d,.\-]", "", text or "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None
