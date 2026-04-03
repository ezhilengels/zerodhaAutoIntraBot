"""
core/prescan.py
────────────────
Thin wrapper around prescanV2 so the rest of the bot can keep using the same
`build_prescan_result()` interface while the underlying logic comes from the
newer pre-market filter pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import WATCHLIST, STRATEGY_MODE, prescan_cfg
from data import upstox_provider as nse
from core.prescan_short_sell_filters import PrescanConfig as ShortPrescanConfig, apply_prescan_filters
from prescanV2.premarket_filter import (
    PremarketFilterConfig,
    FilteredStock,
    run_premarket_filter,
)
from master_v1.prescan import run_daily_prescan as run_master_v1_prescan


@dataclass
class PreScanResult:
    summary: str
    candidates: list[str]
    is_api_fail: bool = False


def _fmt_stock(stock: FilteredStock) -> str:
    news_tag = " news" if stock.is_news_day else ""
    beta_tag = stock.beta_category.lower()
    ath_tag = f", ATH-{stock.ath_distance_pct:.2f}%" if stock.is_near_ath else ""
    return f"{stock.symbol} ({stock.gap_pct:+.2f}%, {beta_tag}{ath_tag}{news_tag})"


def build_prescan_result() -> PreScanResult:
    ath_scan_enabled = prescan_cfg.ath_scan_enabled and STRATEGY_MODE == "short_intraday_v3"

    cfg = PremarketFilterConfig(
        gap_min_pct=prescan_cfg.gap_threshold_pct,
        allow_gap_down=False,
        enable_news_check=prescan_cfg.enable_news_check,
        skip_news_stocks=False,
        skip_medium_beta=False,
        ath_scan_enabled=ath_scan_enabled,
        ath_near_pct=prescan_cfg.ath_near_pct,
        ath_lookback_days=prescan_cfg.ath_lookback_days,
        ath_min_avg_turnover_rs=prescan_cfg.ath_min_avg_turnover_rs,
    )

    stocks = run_premarket_filter(cfg=cfg, universe=list(WATCHLIST))
    is_api_fail = (len(WATCHLIST) > 0 and not stocks)

    gap_up = [stock for stock in stocks if stock.gap_direction == "UP"]
    gap_down = [stock for stock in stocks if stock.gap_direction == "DOWN"]
    fo_ban = set(nse.get_fo_ban_list())
    banned = [symbol for symbol in WATCHLIST if symbol in fo_ban]
    news = [stock.symbol for stock in stocks if stock.is_news_day]
    near_ath = [stock for stock in stocks if stock.is_near_ath]
    near_ath_line = (
        f"Near ATH within {prescan_cfg.ath_near_pct:.1f}%: "
        f"{', '.join(_fmt_stock(stock) for stock in near_ath[:10]) if near_ath else 'None'}\n"
        if ath_scan_enabled else ""
    )

    candidates = [stock.symbol for stock in stocks[: prescan_cfg.shortlist_size]]

    summary = (
        "📡 *MIS Pre-Scan v2*\n"
        + ("⚠️ *API FAILURE: Could not fetch quotes from NSE*\n" if is_api_fail else "") +
        f"Gap up > {prescan_cfg.gap_threshold_pct:.1f}%: "
        f"{', '.join(_fmt_stock(stock) for stock in gap_up[:10]) if gap_up else 'None'}\n"
        f"Gap down < -{prescan_cfg.gap_threshold_pct:.1f}%: "
        f"{', '.join(_fmt_stock(stock) for stock in gap_down[:10]) if gap_down else 'None'}\n"
        f"{near_ath_line}"
        f"F&O ban overlap: {', '.join(banned) if banned else 'None'}\n"
        f"News movers: {', '.join(news) if news else 'None'}\n"
        f"Today's trade candidates: {', '.join(candidates) if candidates else 'None'}"
    )

    return PreScanResult(summary=summary, candidates=candidates, is_api_fail=is_api_fail)


def build_prescan_summary() -> str:
    return build_prescan_result().summary


def build_short_prescan_result() -> PreScanResult:
    cfg = ShortPrescanConfig(shortlist_size=prescan_cfg.shortlist_size)

    gap_data: dict[str, dict] = {}
    raw_symbols: list[str] = []

    for symbol in WATCHLIST:
        quote = nse.get_quote(symbol)
        if not quote:
            continue

        prev_close = float(quote.get("prev_close") or 0.0)
        open_price = float(quote.get("open") or 0.0)
        if prev_close <= 0 or open_price <= 0:
            continue

        daily_df = nse.get_daily_candles(symbol, days=3)
        prev_volume = int(float(daily_df.iloc[-2]["volume"])) if len(daily_df) >= 2 else 0
        gap_pct = ((open_price - prev_close) / prev_close) * 100

        symbol_key = symbol.upper()
        gap_data[symbol_key] = {
            "gap_pct": round(gap_pct, 4),
            "prev_volume": prev_volume,
            "prev_close": prev_close,
        }
        raw_symbols.append(symbol_key)

    shortlist = apply_prescan_filters(raw_symbols, gap_data, cfg)
    candidates = shortlist[: prescan_cfg.shortlist_size]
    is_api_fail = (len(WATCHLIST) > 0 and not raw_symbols)

    lines = []
    for symbol in candidates:
        meta = gap_data.get(symbol, {})
        lines.append(
            f"{symbol} ({float(meta.get('gap_pct', 0.0)):+.2f}%, vol {int(meta.get('prev_volume', 0)):,})"
        )

    summary = (
        "📡 *Short Pre-Scan*\n"
        + ("⚠️ *API FAILURE: Could not fetch quotes from NSE*\n" if is_api_fail else "") +
        f"Gap 1.5% to 5.0%, prev vol >= 500000, price >= 200\n"
        f"Today's short candidates: {', '.join(lines) if lines else 'None'}"
    )

    return PreScanResult(summary=summary, candidates=candidates, is_api_fail=is_api_fail)

def build_master_v1_result() -> PreScanResult:
    """Connector for the Master V1 Island."""
    candidates = run_master_v1_prescan(list(WATCHLIST))
    summary = (
        "👑 *MASTER V1 PRE-SCAN*\n"
        f"Filters: Gap > 0.5%, Turnover > 5Cr, Beating Nifty\n"
        f"Today's Elite Shortlist: {', '.join(candidates) if candidates else 'None'}"
    )
    return PreScanResult(summary=summary, candidates=candidates)
