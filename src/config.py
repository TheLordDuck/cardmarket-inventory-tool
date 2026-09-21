"""Central configuration, loaded from .env (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent

# Repricing discount applied per condition, relative to Cardmarket's own
# near-mint trend price. Tune these to taste.
DEFAULT_CONDITION_MULTIPLIERS = {
    "MT": 1.05,   # Mint
    "NM": 1.0,    # Near Mint
    "EX": 0.9,    # Excellent
    "GD": 0.75,   # Good
    "LP": 0.6,    # Light Played
    "PL": 0.45,   # Played
    "PO": 0.3,    # Poor
}


def _env_float(name: str, default: float | None) -> float | None:
    val = os.getenv(name, "")
    if val.strip() == "":
        return default
    return float(val)


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name, "")
    if val.strip() == "":
        return default
    return int(val)


@dataclass
class Config:
    # Attaches to your own, already-running, already-logged-in Chrome via
    # CDP instead of launching a fresh one -- see README for why (avoids
    # tripping Cloudflare's bot detection on a scripted `goto()`).
    cardmarket_cdp_url: str = field(
        default_factory=lambda: os.getenv("CARDMARKET_CDP_URL", "http://localhost:9222")
    )
    browser_action_delay_min_seconds: float = field(
        default_factory=lambda: _env_float("BROWSER_ACTION_DELAY_MIN_SECONDS", 1.5)
    )
    browser_action_delay_max_seconds: float = field(
        default_factory=lambda: _env_float("BROWSER_ACTION_DELAY_MAX_SECONDS", 4.0)
    )
    # Optional: lets the CLI launch Chrome itself (if it isn't already
    # running with the debugging port open) and fill in your username/
    # password on the login page -- you still have to complete 2FA/CAPTCHA
    # yourself, that step can't be automated. Treat these like your actual
    # password, because they are one: never commit .env (already gitignored).
    cardmarket_username: str = field(default_factory=lambda: os.getenv("CARDMARKET_USERNAME", ""))
    cardmarket_password: str = field(default_factory=lambda: os.getenv("CARDMARKET_PASSWORD", ""))
    chrome_executable_path: str = field(
        default_factory=lambda: os.getenv(
            "CHROME_EXECUTABLE_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        )
    )
    cardmarket_browser_profile_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("CARDMARKET_BROWSER_PROFILE_DIR")
            or str(ROOT_DIR / ".cardmarket-browser-profile")
        )
    )
    # After auto-filling your username/password, how long to keep polling
    # for you to finish 2FA/verification in the browser before giving up --
    # lets a command proceed automatically once you're done instead of you
    # needing to rerun it yourself.
    login_wait_timeout_seconds: int = field(
        default_factory=lambda: _env_int("LOGIN_WAIT_TIMEOUT_SECONDS", 300)
    )

    # new_price = source_price(eur) * condition_multiplier * (1 + adjustment_pct / 100),
    # then clamped to [MIN_PRICE, MAX_PRICE] and rounded to PRICE_STEP.
    min_price: float = field(default_factory=lambda: _env_float("MIN_PRICE", 0.05))
    max_price: float | None = field(default_factory=lambda: _env_float("MAX_PRICE", None))
    price_step: float = field(default_factory=lambda: _env_float("PRICE_STEP", 0.01))

    # Caps how many rows get a changed price written/applied per run, so you
    # can roll out repricing gradually instead of relisting your whole stock
    # at once. 0 = no limit.
    max_changes_per_run: int = field(default_factory=lambda: _env_int("MAX_CHANGES_PER_RUN", 0))

    condition_multipliers: dict = field(default_factory=lambda: dict(DEFAULT_CONDITION_MULTIPLIERS))

    reports_dir: Path = field(default_factory=lambda: ROOT_DIR / "reports")
    logs_dir: Path = field(default_factory=lambda: ROOT_DIR / "logs")


def load_config() -> Config:
    cfg = Config()
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return cfg
