"""
core/short_prescan_filters.py
─────────────────────────────
Shared pre-scan shortlist filters for intraday short strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


SHORT_STRATEGY_NAMES = {
    "short_intraday_v1",
    "short_intraday_v2",
    "short_intraday_v3",
    "short_intraday_v4",
    "short_intraday_v6",
    "ath_reversal_v5",
}


SECTOR_SYMBOLS: dict[str, set[str]] = {
    "INFRA": {
        "ULTRACEMCO", "LT", "NTPC", "POWERGRID", "ONGC", "COALINDIA",
        "SIEMENS", "ABB", "LTM", "RELIANCE",
    },
    "CAPITAL_GOODS": {
        "LT", "ABB", "SIEMENS", "CUMMINSIND", "CGPOWER", "BHEL",
    },
    "PSU_BANK": {
        "SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK",
    },
    "PHARMA": {
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "ZYDUSLIFE",
    },
}


@dataclass(frozen=True)
class ShortPreScanRow:
    symbol: str
    gap_pct: float
    prev_close: float
    prev_day_volume: float


@dataclass(frozen=True)
class ShortPreScanFilterConfig:
    gap_min_pct: float = 1.5
    gap_max_pct: float | None = 5.0
    min_prev_volume: float = 500_000
    min_price: float = 200.0
    exclude_sectors: tuple[str, ...] = ("INFRA", "CAPITAL_GOODS", "PSU_BANK")

    @property
    def blocked_symbols(self) -> set[str]:
        blocked: set[str] = set()
        for sector in self.exclude_sectors:
            blocked.update(SECTOR_SYMBOLS.get(sector.upper(), set()))
        return blocked


def is_short_strategy(strategy_name: str) -> bool:
    return strategy_name in SHORT_STRATEGY_NAMES


def parse_sector_list(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(
        sector.strip().upper()
        for sector in raw.split(",")
        if sector.strip()
    )


def build_short_prefilter_config(
    strategy_name: str,
    gap_threshold: float,
    gap_max: float | None,
    min_prev_volume: float,
    min_price: float,
    exclude_sectors: str | None,
) -> ShortPreScanFilterConfig | None:
    if not is_short_strategy(strategy_name):
        return None

    sector_tuple = parse_sector_list(exclude_sectors)
    if not sector_tuple:
        sector_tuple = ("INFRA", "CAPITAL_GOODS", "PSU_BANK")

    effective_gap_max = gap_max if gap_max and gap_max > 0 else 5.0
    effective_prev_volume = min_prev_volume if min_prev_volume > 0 else 500_000
    effective_min_price = min_price if min_price > 0 else 200.0

    return ShortPreScanFilterConfig(
        gap_min_pct=max(gap_threshold, 1.5),
        gap_max_pct=effective_gap_max,
        min_prev_volume=effective_prev_volume,
        min_price=effective_min_price,
        exclude_sectors=sector_tuple,
    )


def apply_short_prescan_filters(
    rows: Sequence[ShortPreScanRow],
    cfg: ShortPreScanFilterConfig | None,
) -> list[ShortPreScanRow]:
    if not cfg:
        return list(rows)

    blocked = cfg.blocked_symbols
    filtered: list[ShortPreScanRow] = []

    for row in rows:
        if row.symbol in blocked:
            continue
        if row.gap_pct < cfg.gap_min_pct:
            continue
        if cfg.gap_max_pct is not None and row.gap_pct > cfg.gap_max_pct:
            continue
        if row.prev_day_volume < cfg.min_prev_volume:
            continue
        if row.prev_close < cfg.min_price:
            continue
        filtered.append(row)

    return filtered
