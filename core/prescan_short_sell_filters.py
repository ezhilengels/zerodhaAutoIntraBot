"""
scripts/prescan_filters.py
──────────────────────────
Pre-filter pipeline for short_intraday_v4 exhaustion scanner.

Runs BEFORE the strategy's detect() is called.
Eliminates structurally bad candidates early so the scanner only
sees high-quality exhaustion setups.

Usage (in backtest_daily_prescan.py):
    from scripts.prescan_filters import apply_prescan_filters, PrescanConfig

    cfg     = PrescanConfig()
    symbols = apply_prescan_filters(raw_symbols, gap_data, cfg)

Standalone test:
    python3 scripts/prescan_filters.py
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PrescanConfig:
    # Gap thresholds
    gap_min_pct:     float = 1.5      # stock must have gapped up ≥1.5%
    gap_max_pct:     float = 5.0      # skip >5% gaps (news-driven, unpredictable)

    # Liquidity
    min_prev_volume: int   = 200_000  # at least 2L shares traded previous day (Tactical Mode)
    min_price:       float = 100.0    # allow stocks from ₹100

    # Shortlist cap — increased for higher volume
    shortlist_size:  int   = 15       # allow more candidates

    # Liquidity Guard (Nifty 500 protection)
    liquidity_guard_enabled: bool = os.getenv("LIQUIDITY_GUARD_ENABLED", "true").lower() == "true"
    min_turnover_cr: float = float(os.getenv("LIQUIDITY_MIN_TURNOVER_CR", "2.0"))


# ─────────────────────────────────────────────────────────────────────────────
# Sector / symbol blocklist
# ─────────────────────────────────────────────────────────────────────────────

# Heavily reduced to allow for higher trade volume.
# Only defensive / extremely low-volatility stocks remain blocked.
SECTOR_BLOCKLIST: set[str] = {
    "COFORGE",      # Blocked: Outlier loss
    "EXIDEIND",     # Blocked: Outlier loss
    "OBEROIRLTY",   # Blocked: Outlier loss
    "INDUSTOWER",   # Blocked: Outlier loss
    "ABB",          # Blocked due to extreme volatility / outlier losses
    "HINDUNILVR",   # FMCG defensive — barely moves intraday
    "NESTLEIND",    # same
    "BRITANNIA",    # same
}


# ─────────────────────────────────────────────────────────────────────────────
# Individual filter functions
# ─────────────────────────────────────────────────────────────────────────────

def _passes_gap_filter(sym: str, gap_pct: float, cfg: PrescanConfig) -> bool:
    """Gap must be between gap_min_pct and gap_max_pct."""
    if gap_pct < cfg.gap_min_pct:
        log.debug(f"  ✗ {sym}: gap {gap_pct:.2f}% below min {cfg.gap_min_pct}%")
        return False
    if gap_pct > cfg.gap_max_pct:
        log.debug(f"  ✗ {sym}: gap {gap_pct:.2f}% above max {cfg.gap_max_pct}% (news-driven)")
        return False
    return True


def _passes_volume_filter(sym: str, prev_volume: int, cfg: PrescanConfig) -> bool:
    """Previous day volume must meet minimum threshold."""
    if prev_volume < cfg.min_prev_volume:
        log.debug(
            f"  ✗ {sym}: prev volume {prev_volume:,} below min {cfg.min_prev_volume:,}"
        )
        return False
    return True


def _passes_price_filter(sym: str, prev_close: float, cfg: PrescanConfig) -> bool:
    """Price must be above minimum to avoid wide-spread slippage."""
    if prev_close < cfg.min_price:
        log.debug(f"  ✗ {sym}: price ₹{prev_close:.2f} below min ₹{cfg.min_price:.2f}")
        return False
    return True


def _passes_sector_filter(sym: str) -> bool:
    """Symbol must not be in the structural blocklist."""
    if sym.upper() in SECTOR_BLOCKLIST:
        log.debug(f"  ✗ {sym}: in sector blocklist")
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main filter pipeline
# ─────────────────────────────────────────────────────────────────────────────

def apply_prescan_filters(
    symbols:  list[str],
    gap_data: dict[str, dict],
    cfg:      Optional[PrescanConfig] = None,
) -> list[str]:
    """
    Run all pre-filters on the raw symbol list and return a trimmed shortlist.

    Args:
        symbols:  Raw list of symbols from the prescan (e.g. Nifty 100).
        gap_data: Dict keyed by symbol with at least:
                    {
                        "gap_pct":    float,   # % gap vs prev close
                        "prev_volume": int,    # shares traded previous day
                        "prev_close":  float,  # previous day closing price
                    }
        cfg:      PrescanConfig (uses defaults if not provided).

    Returns:
        Filtered list of symbols, capped at cfg.shortlist_size.
    """
    if cfg is None:
        cfg = PrescanConfig()

    log.info(f"Prescan filter: starting with {len(symbols)} symbols")

    passed:  list[tuple[str, float]] = []   # (symbol, gap_pct) for sorting
    reasons: dict[str, str]          = {}   # for summary logging

    for sym in symbols:
        sym = sym.upper()
        data = gap_data.get(sym, {})

        gap_pct    = float(data.get("gap_pct",     0.0))
        prev_vol   = int(data.get("prev_volume",   0))
        prev_close = float(data.get("prev_close",  0.0))

        # Run filters in priority order (cheapest / most impactful first)
        if not _passes_sector_filter(sym):
            reasons[sym] = "sector_blocklist"
            continue

        if not _passes_gap_filter(sym, gap_pct, cfg):
            reasons[sym] = f"gap={gap_pct:.2f}%"
            continue

        if not _passes_volume_filter(sym, prev_vol, cfg):
            reasons[sym] = f"prev_vol={prev_vol:,}"
            continue

        if not _passes_price_filter(sym, prev_close, cfg):
            reasons[sym] = f"price=₹{prev_close:.2f}"
            continue

        # --- Quality Enhancement: Liquidity Guard ---
        if cfg.liquidity_guard_enabled:
            # turnover = price * volume (approximate using prev_close)
            turnover_cr = (prev_close * prev_vol) / 10_000_000.0
            if turnover_cr < cfg.min_turnover_cr:
                reasons[sym] = f"low_turnover={turnover_cr:.1f}Cr"
                continue

        passed.append((sym, gap_pct))
        log.debug(f"  ✓ {sym}: gap={gap_pct:.2f}% vol={prev_vol:,} price=₹{prev_close:.2f}")

    # Sort by gap_pct descending — strongest gap candidates first
    passed.sort(key=lambda x: x[1], reverse=True)

    # Cap to shortlist_size
    shortlist = [sym for sym, _ in passed[: cfg.shortlist_size]]

    # Summary
    log.info(
        f"Prescan filter: {len(symbols)} → {len(passed)} passed → "
        f"{len(shortlist)} shortlisted (cap={cfg.shortlist_size})"
    )
    if reasons:
        log.info("Filtered out:")
        for sym, reason in sorted(reasons.items()):
            log.info(f"  {sym}: {reason}")

    return shortlist


# ─────────────────────────────────────────────────────────────────────────────
# Backtest integration helper
# ─────────────────────────────────────────────────────────────────────────────

def build_gap_data_from_candles(daily_candles: dict[str, "pd.DataFrame"]) -> dict[str, dict]:
    """
    Build the gap_data dict from a dict of daily OHLCV DataFrames.
    Expects each DataFrame to be sorted ascending by date with columns:
        open, high, low, close, volume

    Usage:
        daily = {sym: nse.get_daily_candles(sym) for sym in universe}
        gap_data = build_gap_data_from_candles(daily)
        shortlist = apply_prescan_filters(universe, gap_data, cfg)
    """
    import pandas as pd

    result: dict[str, dict] = {}
    for sym, df in daily_candles.items():
        if df is None or len(df) < 2:
            continue
        prev  = df.iloc[-2]
        today = df.iloc[-1]

        prev_close  = float(prev["close"])
        today_open  = float(today["open"])
        prev_volume = int(prev["volume"])

        gap_pct = ((today_open - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0

        result[sym.upper()] = {
            "gap_pct":    round(gap_pct, 4),
            "prev_volume": prev_volume,
            "prev_close":  prev_close,
        }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Standalone smoke test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    # Simulated gap_data for smoke test
    mock_gap_data = {
        "RELIANCE":   {"gap_pct": 2.1,  "prev_volume": 3_200_000, "prev_close": 2850.0},
        "TCS":        {"gap_pct": 1.8,  "prev_volume": 1_500_000, "prev_close": 3900.0},
        "HDFCBANK":   {"gap_pct": 0.4,  "prev_volume": 4_000_000, "prev_close": 1650.0},  # gap too low
        "ULTRACEMCO": {"gap_pct": 2.5,  "prev_volume": 800_000,   "prev_close": 9500.0},  # blocklist
        "EICHERMOT":  {"gap_pct": 1.9,  "prev_volume": 600_000,   "prev_close": 4200.0},  # blocklist
        "LT":         {"gap_pct": 2.2,  "prev_volume": 1_200_000, "prev_close": 3600.0},  # blocklist
        "INFY":       {"gap_pct": 3.1,  "prev_volume": 2_800_000, "prev_close": 1480.0},
        "BAJFINANCE": {"gap_pct": 1.6,  "prev_volume": 900_000,   "prev_close": 6800.0},
        "MARUTI":     {"gap_pct": 2.8,  "prev_volume": 350_000,   "prev_close": 11200.0}, # low volume
        "DMART":      {"gap_pct": 6.2,  "prev_volume": 700_000,   "prev_close": 4300.0},  # gap too high
        "SBILIFE":    {"gap_pct": 1.7,  "prev_volume": 1_100_000, "prev_close": 1400.0},
        "ICICIBANK":  {"gap_pct": 2.0,  "prev_volume": 5_000_000, "prev_close": 1100.0},
        "WIPRO":      {"gap_pct": 1.5,  "prev_volume": 2_200_000, "prev_close": 480.0},
        "TATAMOTORS": {"gap_pct": 2.3,  "prev_volume": 6_000_000, "prev_close": 870.0},
        "SUNPHARMA":  {"gap_pct": 2.0,  "prev_volume": 1_800_000, "prev_close": 1650.0},  # blocklist
    }

    universe = list(mock_gap_data.keys())
    cfg      = PrescanConfig()

    print("\n" + "═" * 55)
    print("  short_intraday_v4 — Prescan Filter Smoke Test")
    print("═" * 55)
    print(f"  Universe   : {len(universe)} symbols")
    print(f"  Gap range  : {cfg.gap_min_pct}% – {cfg.gap_max_pct}%")
    print(f"  Min volume : {cfg.min_prev_volume:,}")
    print(f"  Min price  : ₹{cfg.min_price:.0f}")
    print(f"  Shortlist  : top {cfg.shortlist_size}")
    print("═" * 55 + "\n")

    shortlist = apply_prescan_filters(universe, mock_gap_data, cfg)

    print("\n" + "─" * 55)
    print(f"  ✅ Final shortlist ({len(shortlist)} symbols):")
    for sym in shortlist:
        d = mock_gap_data[sym]
        print(
            f"    {sym:<14} gap={d['gap_pct']:.2f}%  "
            f"vol={d['prev_volume']:>9,}  price=₹{d['prev_close']:.2f}"
        )
    print("─" * 55)
