"""
core/prescan.py
────────────────
Thin wrapper around prescanV2 so the rest of the bot can keep using the same
`build_prescan_result()` interface while the underlying logic comes from the
newer pre-market filter pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import WATCHLIST, prescan_cfg
from data import nse_provider as nse
from prescanV2.premarket_filter import (
    PremarketFilterConfig,
    FilteredStock,
    run_premarket_filter,
)


@dataclass
class PreScanResult:
    summary: str
    candidates: list[str]


def _fmt_stock(stock: FilteredStock) -> str:
    news_tag = " news" if stock.is_news_day else ""
    beta_tag = stock.beta_category.lower()
    return f"{stock.symbol} ({stock.gap_pct:+.2f}%, {beta_tag}{news_tag})"


def build_prescan_result() -> PreScanResult:
    cfg = PremarketFilterConfig(
        gap_min_pct=prescan_cfg.gap_threshold_pct,
        allow_gap_down=False,
        enable_news_check=prescan_cfg.enable_news_check,
        skip_news_stocks=False,
        skip_medium_beta=False,
    )

    stocks = run_premarket_filter(cfg=cfg, universe=list(WATCHLIST))

    gap_up = [stock for stock in stocks if stock.gap_direction == "UP"]
    gap_down = [stock for stock in stocks if stock.gap_direction == "DOWN"]
    fo_ban = set(nse.get_fo_ban_list())
    banned = [symbol for symbol in WATCHLIST if symbol in fo_ban]
    news = [stock.symbol for stock in stocks if stock.is_news_day]

    candidates = [stock.symbol for stock in stocks[: prescan_cfg.shortlist_size]]

    summary = (
        "📡 *MIS Pre-Scan v2*\n"
        f"Gap up > {prescan_cfg.gap_threshold_pct:.1f}%: "
        f"{', '.join(_fmt_stock(stock) for stock in gap_up[:10]) if gap_up else 'None'}\n"
        f"Gap down < -{prescan_cfg.gap_threshold_pct:.1f}%: "
        f"{', '.join(_fmt_stock(stock) for stock in gap_down[:10]) if gap_down else 'None'}\n"
        f"F&O ban overlap: {', '.join(banned) if banned else 'None'}\n"
        f"News movers: {', '.join(news) if news else 'None'}\n"
        f"Today's trade candidates: {', '.join(candidates) if candidates else 'None'}"
    )

    return PreScanResult(summary=summary, candidates=candidates)


def build_prescan_summary() -> str:
    return build_prescan_result().summary
