"""
scanner/premarket_filter.py
────────────────────────────
Pre-market stock filter pipeline.

Runs before market open (08:45 – 09:14) or on-demand.

Pipeline (in order):
  1. Universe — high-beta, high-volume NSE stocks
  2. F&O ban list — remove banned stocks
  3. Gap filter — gap up/down > 0.5% vs prev close
  4. News/earnings filter — flag or skip results-day stocks
  5. Beta + liquidity guard — confirm stock is tradeable today

Output:
  List[FilteredStock] — ready to pass to strategy detectors
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from config.settings import WATCHLIST
from data import upstox_provider as nse
from utils.logger import get_logger

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════

# Master universe — high beta, high volume NSE stocks
# Extend this list freely; the pipeline will filter it down daily
DEFAULT_UNIVERSE: list[str] = [
    # Large-cap momentum (high beta)
    "TATASTEEL", "HINDALCO", "JSWSTEEL", "SAIL",         # metals
    "ONGC", "BPCL", "IOC", "RELIANCE",                   # energy
    "SBIN", "ICICIBANK", "AXISBANK", "BANKBARODA",        # banking
    "INFY", "TCS", "WIPRO", "HCLTECH", "TECHM",          # IT
    "TATAMOTORS", "M&M", "BAJAJ-AUTO",                   # auto
    "COALINDIA", "NATIONALUM", "VEDL",                   # commodities
    "ADANIENT", "ADANIPORTS",                            # adani
    "BHARTIARTL",                                         # telecom
]

# Stocks that are HIGH REWARD / HIGH RISK on news days
# These are allowed through even with news flag — but tagged separately
NEWS_AGGRESSIVE_UNIVERSE: list[str] = [
    "INFY", "TCS", "WIPRO", "HCLTECH",   # IT results move 3–8%
    "RELIANCE", "TATAMOTORS",             # big earnings movers
    "SBIN", "ICICIBANK",                  # bank results
]

# NSE headers needed for API calls
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
}


# ══════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════

@dataclass
class FilteredStock:
    symbol:          str
    prev_close:      float
    ltp:             float                  # last traded price / pre-market price
    gap_pct:         float                  # positive = gap up, negative = gap down
    gap_direction:   str                    # "UP" | "DOWN"
    is_news_day:     bool   = False         # earnings / results today
    is_aggressive:   bool   = False         # high-reward news mover
    ban_status:      str    = "OK"          # "OK" | "BANNED" | "PRE_BAN"
    reason:          str    = ""            # why this stock passed
    beta_category:   str    = "UNKNOWN"     # "HIGH" | "MEDIUM"
    ath_price:       float  = 0.0           # reference ATH / rolling high
    ath_distance_pct: float = 999.0         # % below ATH, lower is closer
    avg_turnover_rs: float  = 0.0           # average daily turnover
    is_near_ath:     bool   = False         # within configured ATH distance

    @property
    def is_tradeable(self) -> bool:
        return self.ban_status == "OK"

    @property
    def is_gap_up(self) -> bool:
        return self.gap_pct > 0

    def __str__(self) -> str:
        news_tag = " 📰NEWS" if self.is_news_day else ""
        agg_tag  = " ⚡AGGRESSIVE" if self.is_aggressive else ""
        return (
            f"{self.symbol:<14} | gap={self.gap_pct:+.2f}% {self.gap_direction:<4} | "
            f"prev=₹{self.prev_close:.2f}  ltp=₹{self.ltp:.2f} | "
            f"ban={self.ban_status}{news_tag}{agg_tag}"
        )


# ══════════════════════════════════════════════════════════════════
#  NSE SESSION (reuse cookies)
# ══════════════════════════════════════════════════════════════════

class NSESession:
    """Lightweight NSE API session with cookie management."""

    BASE = "https://www.nseindia.com"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(NSE_HEADERS)
        self._cookie_ts: float = 0.0

    def _refresh_cookies(self):
        """Hit NSE homepage to get fresh cookies (required for API calls)."""
        now = time.time()
        if now - self._cookie_ts < 300:          # reuse for 5 min
            return
        try:
            self._session.get(self.BASE, timeout=10)
            self._cookie_ts = now
            log.debug("NSE cookies refreshed")
        except Exception as e:
            log.warning(f"Cookie refresh failed: {e}")

    def get(self, path: str, params: dict = None) -> dict:
        self._refresh_cookies()
        url = f"{self.BASE}{path}"
        try:
            r = self._session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"NSE GET {path} failed: {e}")
            return {}


_nse = NSESession()


# ══════════════════════════════════════════════════════════════════
#  FILTER 1 — F&O BAN LIST
# ══════════════════════════════════════════════════════════════════

def fetch_fo_ban_list() -> dict[str, str]:
    """
    Fetch today's F&O ban list from NSE.

    Returns dict: { "SYMBOL": "BANNED" | "PRE_BAN" }

    NSE publishes two lists:
      - Securities in F&O ban period (BANNED)
      - Securities approaching ban (PRE_BAN) — optional warning
    """
    symbols = nse.get_fo_ban_list()
    result = {symbol: "BANNED" for symbol in symbols}
    if result:
        log.info(f"📋 F&O ban list: {len(result)} stocks banned today → {list(result.keys())}")
    else:
        log.warning("⚠️ Could not fetch F&O ban list — proceeding without it")
    return result


# ══════════════════════════════════════════════════════════════════
#  FILTER 2 — QUOTE + GAP CALCULATION
# ══════════════════════════════════════════════════════════════════

def fetch_quote(symbol: str) -> Optional[dict]:
    """
    Fetch NSE quote for a symbol.
    Returns dict with: lastPrice, previousClose, open, etc.
    """
    return nse.get_quote(symbol)


def calculate_gap(prev_close: float, open_price: float) -> tuple[float, str]:
    """
    Returns (gap_pct, direction).
    gap_pct: positive = gap up, negative = gap down
    """
    if prev_close <= 0 or open_price <= 0:
        return 0.0, "FLAT"
    gap_pct = ((open_price - prev_close) / prev_close) * 100
    if gap_pct > 0.1:
        return round(gap_pct, 2), "UP"
    elif gap_pct < -0.1:
        return round(gap_pct, 2), "DOWN"
    return round(gap_pct, 2), "FLAT"


# ══════════════════════════════════════════════════════════════════
#  FILTER 3 — NEWS / EARNINGS DAY DETECTION
# ══════════════════════════════════════════════════════════════════

# Manually maintained earnings calendar (update weekly)
# Format: { "SYMBOL": ["YYYY-MM-DD", ...] }
EARNINGS_CALENDAR: dict[str, list[str]] = {
    "INFY":       [],   # fill with actual dates
    "TCS":        [],
    "WIPRO":      [],
    "HCLTECH":    [],
    "RELIANCE":   [],
    "SBIN":       [],
    "ICICIBANK":  [],
    "TATAMOTORS": [],
    "HINDALCO":   [],
    "ONGC":       [],
}


def fetch_corporate_actions(symbol: str) -> list[dict]:
    """
    Fetch NSE corporate actions for a symbol.
    Checks for board meetings, results, dividends announced today.
    """
    data = _nse.get(
        "/api/corporates-corporateActions",
        params={"index": "equities", "symbol": symbol}
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        actions = data.get("data") or data.get("value") or data.get("results")
        if isinstance(actions, list):
            return actions
    return []


def is_news_day(symbol: str, enabled: bool = False) -> bool:
    """
    Returns True if today is a results/earnings/board-meeting day.

    Check order:
      1. Local earnings calendar (fast, offline)
      2. NSE corporate actions API (accurate, live)
    """
    if not enabled:
        return False

    today_str = date.today().isoformat()

    # Check 1: local calendar
    if today_str in EARNINGS_CALENDAR.get(symbol, []):
        log.info(f"📰 {symbol} in earnings calendar for today")
        return True

    # Check 2: NSE corporate actions
    try:
        actions = fetch_corporate_actions(symbol)
        keywords = ["board meeting", "results", "financial results", "quarterly results"]
        for action in actions:
            ex_date = action.get("exDate", "") or action.get("recordDate", "")
            purpose = (action.get("purpose", "") or "").lower()
            if ex_date == today_str and any(k in purpose for k in keywords):
                log.info(f"📰 {symbol} has corporate action today: '{purpose}'")
                return True
    except Exception as e:
        log.debug(f"Corporate action fetch failed for {symbol}: {e}")

    return False


# ══════════════════════════════════════════════════════════════════
#  FILTER 4 — BETA / LIQUIDITY GUARD
# ══════════════════════════════════════════════════════════════════

# Pre-classified beta categories based on 1-year historical data
# HIGH = beta > 1.1 (momentum stocks, good for intraday)
# MEDIUM = beta 0.8–1.1 (moderate movers)
BETA_MAP: dict[str, str] = {
    "TATASTEEL":    "HIGH",
    "HINDALCO":     "HIGH",
    "JSWSTEEL":     "HIGH",
    "SAIL":         "HIGH",
    "ADANIENT":     "HIGH",
    "TATAMOTORS":   "HIGH",
    "NATIONALUM":   "HIGH",
    "VEDL":         "HIGH",
    "BANKBARODA":   "HIGH",
    "AXISBANK":     "HIGH",
    "ICICIBANK":    "HIGH",
    "SBIN":         "HIGH",
    "RELIANCE":     "MEDIUM",
    "ONGC":         "MEDIUM",
    "BPCL":         "MEDIUM",
    "IOC":          "MEDIUM",
    "INFY":         "MEDIUM",
    "TCS":          "MEDIUM",
    "WIPRO":        "MEDIUM",
    "HCLTECH":      "MEDIUM",
    "TECHM":        "HIGH",
    "BHARTIARTL":   "MEDIUM",
    "COALINDIA":    "MEDIUM",
    "BAJAJ-AUTO":   "MEDIUM",
    "M&M":          "MEDIUM",
    "ADANIPORTS":   "HIGH",
}

# Minimum pre-market / early session volume to confirm liquidity
MIN_PREMARKET_VOLUME = 10_000


# ══════════════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════

@dataclass
class PremarketFilterConfig:
    gap_min_pct:          float = 0.5    # minimum gap % to qualify
    gap_max_pct:          float = 5.0    # skip extreme gaps (circuit risk)
    allow_gap_down:       bool  = False  # True = include short setups
    enable_news_check:    bool  = False  # Keep off by default; NSE corporate-actions API is noisy
    skip_news_stocks:     bool  = False  # True = skip results-day stocks
    skip_medium_beta:     bool  = False  # True = only HIGH beta stocks
    min_price:            float = 50.0   # skip penny stocks
    max_price:            float = 5000.0 # skip very high-priced stocks
    ath_scan_enabled:     bool  = False  # True = enrich with ATH/turnover info
    ath_near_pct:         float = 3.0    # treat stocks within this % as near ATH
    ath_lookback_days:    int   = 252    # rolling daily lookback for ATH proxy
    ath_min_avg_turnover_rs: float = 10_000_000.0  # Rs 1 crore average daily turnover


def _compute_ath_snapshot(symbol: str, ltp: float, lookback_days: int) -> tuple[float, float, float]:
    """
    Returns (ath_price, ath_distance_pct, avg_daily_turnover_rs).
    Falls back to zeros on data issues.
    """
    daily_df = nse.get_daily_candles(symbol, days=lookback_days)
    if daily_df.empty:
        return 0.0, 999.0, 0.0

    daily_df = daily_df.copy()
    daily_df["turnover"] = daily_df["close"] * daily_df["volume"]
    ath_price = float(daily_df["high"].max()) if not daily_df["high"].empty else 0.0
    avg_turnover = float(daily_df["turnover"].tail(20).mean()) if "turnover" in daily_df else 0.0
    if ath_price <= 0 or ltp <= 0:
        return ath_price, 999.0, avg_turnover

    ath_distance_pct = max(0.0, ((ath_price - ltp) / ath_price) * 100)
    return round(ath_price, 2), round(ath_distance_pct, 2), round(avg_turnover, 2)


def run_premarket_filter(
    cfg: PremarketFilterConfig = None,
    universe: Optional[list[str]] = None,
) -> list[FilteredStock]:
    """
    Run full pre-market filter pipeline.

    Returns list of FilteredStock objects that passed all filters,
    sorted by absolute gap % (strongest movers first).
    """
    if cfg is None:
        cfg = PremarketFilterConfig()
    symbols_to_scan = universe or WATCHLIST or DEFAULT_UNIVERSE

    log.info("=" * 60)
    log.info("🔍 PRE-MARKET FILTER PIPELINE STARTING")
    log.info(f"   Universe: {len(symbols_to_scan)} stocks")
    log.info(f"   Gap threshold: >{cfg.gap_min_pct}%")
    log.info(f"   Gap down allowed: {cfg.allow_gap_down}")
    log.info("=" * 60)

    # ── Step 1: F&O ban list ──────────────────────────────────────
    ban_list = fetch_fo_ban_list()

    # ── Step 2: Process each symbol ──────────────────────────────
    candidates: list[FilteredStock] = []

    for symbol in symbols_to_scan:
        log.debug(f"Checking {symbol}...")

        # Step 2a: Ban check
        ban_status = ban_list.get(symbol, "OK")
        if ban_status == "BANNED":
            log.debug(f"  ⛔ {symbol} — F&O BANNED, skip")
            continue

        # Step 2b: Quote fetch
        quote = fetch_quote(symbol)
        if not quote:
            log.warning(f"  ⚠️ {symbol} — no quote data, skip")
            continue

        prev_close = float(quote.get("prev_close", 0) or 0)
        open_price = float(quote.get("open", 0) or 0)
        ltp        = float(quote.get("ltp", 0) or 0)
        volume     = int(quote.get("volume", 0) or 0)

        if prev_close <= 0 or ltp <= 0 or open_price <= 0:
            log.debug(f"  ⛔ {symbol} — invalid price data")
            continue

        # Step 2c: Price range guard
        if not (cfg.min_price <= ltp <= cfg.max_price):
            log.debug(f"  ⛔ {symbol} — price ₹{ltp} out of range")
            continue

        # Step 2d: Gap calculation
        gap_pct, gap_dir = calculate_gap(prev_close, open_price)

        if gap_dir == "FLAT":
            log.debug(f"  ⛔ {symbol} — flat gap ({gap_pct:.2f}%), skip")
            continue

        if not cfg.allow_gap_down and gap_dir == "DOWN":
            log.debug(f"  ⛔ {symbol} — gap down ({gap_pct:.2f}%), skip (long-only mode)")
            continue

        if abs(gap_pct) < cfg.gap_min_pct:
            log.debug(f"  ⛔ {symbol} — gap {gap_pct:.2f}% < threshold {cfg.gap_min_pct}%")
            continue

        if abs(gap_pct) > cfg.gap_max_pct:
            log.debug(f"  ⛔ {symbol} — gap {gap_pct:.2f}% too extreme (circuit risk)")
            continue

        # Step 2e: Beta category
        beta_cat = BETA_MAP.get(symbol, "MEDIUM")
        if cfg.skip_medium_beta and beta_cat == "MEDIUM":
            log.debug(f"  ⛔ {symbol} — MEDIUM beta, skip (strict mode)")
            continue

        # Step 2f: Liquidity guard
        if volume > 0 and volume < MIN_PREMARKET_VOLUME:
            log.debug(f"  ⛔ {symbol} — low live volume {volume}, skip")
            continue

        # Step 2g: News / earnings day check
        news_today    = is_news_day(symbol, enabled=cfg.enable_news_check)
        is_aggressive = symbol in NEWS_AGGRESSIVE_UNIVERSE

        if cfg.skip_news_stocks and news_today and not is_aggressive:
            log.debug(f"  ⛔ {symbol} — results day, skip (conservative mode)")
            continue

        ath_price = 0.0
        ath_distance_pct = 999.0
        avg_turnover_rs = 0.0
        is_near_ath = False
        if cfg.ath_scan_enabled:
            ath_price, ath_distance_pct, avg_turnover_rs = _compute_ath_snapshot(
                symbol=symbol,
                ltp=ltp,
                lookback_days=cfg.ath_lookback_days,
            )
            if avg_turnover_rs < cfg.ath_min_avg_turnover_rs:
                log.debug(
                    f"  ⛔ {symbol} — avg turnover ₹{avg_turnover_rs:,.0f} below ATH floor "
                    f"₹{cfg.ath_min_avg_turnover_rs:,.0f}"
                )
                continue
            is_near_ath = ath_distance_pct <= cfg.ath_near_pct

        # Step 2h: Build reason string
        reasons = []
        if beta_cat == "HIGH":
            reasons.append("high-beta")
        if abs(gap_pct) > 1.5:
            reasons.append("strong-gap")
        if is_near_ath:
            reasons.append("near-ath")
        if news_today:
            reasons.append("results-day")
        if ban_status == "PRE_BAN":
            reasons.append("approaching-ban")

        stock = FilteredStock(
            symbol        = symbol,
            prev_close    = round(prev_close, 2),
            ltp           = round(ltp, 2),
            gap_pct       = gap_pct,
            gap_direction = gap_dir,
            is_news_day   = news_today,
            is_aggressive = is_aggressive,
            ban_status    = ban_status,
            beta_category = beta_cat,
            reason        = ", ".join(reasons),
            ath_price     = ath_price,
            ath_distance_pct = ath_distance_pct,
            avg_turnover_rs = avg_turnover_rs,
            is_near_ath   = is_near_ath,
        )
        candidates.append(stock)
        log.info(f"  ✅ {stock}")

    # ── Step 3: Sort — optional ATH priority, otherwise strongest gaps first ──
    if cfg.ath_scan_enabled:
        candidates.sort(
            key=lambda s: (
                not s.is_near_ath,
                s.ath_distance_pct,
                -abs(s.gap_pct),
                -s.avg_turnover_rs,
            )
        )
    else:
        candidates.sort(key=lambda s: abs(s.gap_pct), reverse=True)

    # ── Step 4: Summary ───────────────────────────────────────────
    log.info("=" * 60)
    log.info(f"✅ PRE-MARKET FILTER COMPLETE: {len(candidates)} stocks passed")
    gap_up   = [s for s in candidates if s.is_gap_up]
    gap_down = [s for s in candidates if not s.is_gap_up]
    news_day = [s for s in candidates if s.is_news_day]
    log.info(f"   Gap Up:    {len(gap_up)}  → {[s.symbol for s in gap_up]}")
    log.info(f"   Gap Down:  {len(gap_down)} → {[s.symbol for s in gap_down]}")
    log.info(f"   News Day:  {len(news_day)} → {[s.symbol for s in news_day]}")
    log.info("=" * 60)

    return candidates


# ══════════════════════════════════════════════════════════════════
#  CONVENIENCE FUNCTIONS FOR BOT INTEGRATION
# ══════════════════════════════════════════════════════════════════

def get_tradeable_symbols(cfg: PremarketFilterConfig = None) -> list[str]:
    """
    Quick helper — returns only symbol names for passing to strategies.

    Usage in your main bot loop:
        symbols = get_tradeable_symbols()
        for symbol in symbols:
            signal = vwap_rsi.detect(symbol, state)
    """
    stocks = run_premarket_filter(cfg)
    return [s.symbol for s in stocks if s.is_tradeable]


def get_news_day_symbols(cfg: PremarketFilterConfig = None) -> list[str]:
    """Returns symbols that have results/earnings today — use carefully."""
    stocks = run_premarket_filter(cfg)
    return [s.symbol for s in stocks if s.is_news_day and s.is_tradeable]


def print_premarket_report(cfg: PremarketFilterConfig = None):
    """
    Print a clean pre-market report to console/log.
    Call this at 09:00 before market open.
    """
    stocks = run_premarket_filter(cfg)

    print("\n" + "═" * 65)
    print("  📊 PRE-MARKET REPORT  |  " + datetime.now().strftime("%d-%b-%Y %H:%M"))
    print("═" * 65)

    if not stocks:
        print("  No stocks passed today's filter.")
    else:
        print(f"  {'SYMBOL':<14} {'GAP':>8}  {'DIR':<5} {'PREV':>8} {'LTP':>8}  FLAGS")
        print("  " + "-" * 62)
        for s in stocks:
            flags = []
            if s.is_news_day:   flags.append("📰 RESULTS")
            if s.is_aggressive: flags.append("⚡ MOVER")
            if s.ban_status == "PRE_BAN": flags.append("⚠️ PRE-BAN")
            flag_str = "  ".join(flags)
            print(
                f"  {s.symbol:<14} {s.gap_pct:>+7.2f}%  "
                f"{'↑' if s.is_gap_up else '↓':<5} "
                f"₹{s.prev_close:>7.2f} ₹{s.ltp:>7.2f}  {flag_str}"
            )

    print("═" * 65 + "\n")


# ══════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Run this directly to test the filter:
        python -m scanner.premarket_filter
    """
    cfg = PremarketFilterConfig(
        gap_min_pct    = 0.5,
        gap_max_pct    = 5.0,
        allow_gap_down = False,   # set True for short setups
        skip_news_stocks = False, # set True for conservative mode
    )
    print_premarket_report(cfg)
