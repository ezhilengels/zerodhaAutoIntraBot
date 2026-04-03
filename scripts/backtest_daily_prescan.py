#!/usr/bin/env python3
"""
Replay a strategy with a day-specific pre-scan shortlist.

This is closer to the live workflow:
  1. For each day, build a shortlist from that day's gap-up names.
  2. Run the selected strategy only on that shortlist.

Usage:
  python3 scripts/backtest_daily_prescan.py --dir trades/nifty100_replay --strategy vwap_rsi_v2
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# backtest_replay parses argv on import, so give it a harmless default
_ORIGINAL_ARGV = sys.argv[:]
sys.argv = [
    "backtest_replay.py",
    "--csv",
    str(PROJECT_ROOT / "trades" / "nalco_5min_replay.csv"),
]

from scripts.backtest_replay import (  # type: ignore
    ReplayTrade,
    load_candles,
    replay_symbol_day,
)
from core.prescan_short_sell_filters import (
    PrescanConfig,
    apply_prescan_filters,
    build_gap_data_from_candles,
)
sys.argv = _ORIGINAL_ARGV


SHORT_STRATEGIES = {
    "short_intraday_v1",
    "short_intraday_v2",
    "short_intraday_v3",
    "short_intraday_v4",
    "short_intraday_v6",
    "ath_reversal_v5",
}


def is_short_strategy(strategy_name: str) -> bool:
    return strategy_name in SHORT_STRATEGIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay a strategy on a daily pre-scan shortlist.")
    parser.add_argument("--dir", required=True, help="Directory of replay-ready CSVs.")
    parser.add_argument(
        "--strategy",
        required=True,
        choices=[
            "pullback",
            "orb",
            "vwap_reclaim",
            "vwap_reclaim_v2",
            "vwap_rsi",
            "vwap_rsi_v2",
            "vwap_rsi_v3",
            "vwap_rsi_v4",
            "pivot_breakout",
            "pivot_breakout_v2",
            "ema_crossover",
            "short_intraday_v1",
            "short_intraday_v2",
            "short_intraday_v3",
            "short_intraday_v4",
            "short_intraday_v6",
            "ath_reversal_v5",
            "master_v1",
            "multi",
        ],
        help="Strategy to replay on the daily shortlist.",
    )
    parser.add_argument(
        "--gap-threshold",
        type=float,
        default=0.5,
        help="Minimum open-vs-prev-close gap percentage to include in the day shortlist.",
    )
    parser.add_argument(
        "--shortlist-size",
        type=int,
        default=15,
        help="Maximum number of symbols to keep per day after ranking by gap-up.",
    )
    parser.add_argument(
        "--gap-max",
        type=float,
        default=0.0,
        help="Maximum open-vs-prev-close gap percentage allowed for shortlist candidates (short strategies use 5.0 if omitted).",
    )
    parser.add_argument(
        "--min-prev-volume",
        type=float,
        default=0.0,
        help="Minimum previous-day total volume required for shortlist candidates (short strategies use 500000 if omitted).",
    )
    parser.add_argument(
        "--min-price",
        type=float,
        default=0.0,
        help="Minimum previous close required for shortlist candidates (short strategies use 200 if omitted).",
    )
    parser.add_argument(
        "--exclude-sectors",
        default="",
        help="Comma-separated sector presets to exclude for short prefilter, e.g. INFRA,CAPITAL_GOODS,PSU_BANK.",
    )
    return parser.parse_args()


def load_directory_csvs(folder: Path) -> Dict[str, pd.DataFrame]:
    data: Dict[str, pd.DataFrame] = {}
    for path in sorted(folder.glob("*_5m_replay.csv")):
        df = load_candles(str(path))
        if df.empty:
            continue
        symbol = str(df.iloc[0]["symbol"]).strip().upper()
        data[symbol] = df
    return data


def build_daily_shortlists(
    all_data: Dict[str, pd.DataFrame],
    strategy_name: str,
    gap_threshold: float,
    shortlist_size: int,
    gap_max: float = 0.0,
    min_prev_volume: float = 0.0,
    min_price: float = 0.0,
    exclude_sectors: str = "",
) -> Dict[str, List[Tuple[str, float]]]:
    daily_by_symbol: Dict[str, pd.DataFrame] = {}
    all_days: set[str] = set()

    for symbol, df in all_data.items():
        daily_df = (
            df.assign(trade_date=df["time"].dt.date)
            .groupby("trade_date", sort=True)
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .reset_index()
            .rename(columns={"trade_date": "time"})
        )
        daily_df["time"] = pd.to_datetime(daily_df["time"])
        daily_by_symbol[symbol] = daily_df
        all_days.update(daily_df["time"].dt.strftime("%Y-%m-%d").tolist())

    final_shortlists: Dict[str, List[Tuple[str, float]]] = {}
    short_cfg = None
    if is_short_strategy(strategy_name):
        short_cfg = PrescanConfig()
        short_cfg.gap_min_pct = max(gap_threshold, short_cfg.gap_min_pct)
        if gap_max > 0:
            short_cfg.gap_max_pct = gap_max
        if min_prev_volume > 0:
            short_cfg.min_prev_volume = int(min_prev_volume)
        if min_price > 0:
            short_cfg.min_price = float(min_price)
        if shortlist_size > 0:
            short_cfg.shortlist_size = shortlist_size

        if exclude_sectors.strip():
            requested = {sector.strip().upper() for sector in exclude_sectors.split(",") if sector.strip()}
            blocklist = set()
            sector_map = {
                "INFRA": {"ULTRACEMCO", "LT", "NTPC", "POWERGRID", "ONGC", "COALINDIA", "BHEL", "SIEMENS", "ABB", "ADANIPORTS"},
                "CAPITAL_GOODS": {"LT", "ABB", "SIEMENS", "CUMMINSIND", "CGPOWER", "BHEL"},
                "PSU_BANK": {"SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK"},
                "PHARMA": {"SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA"},
            }
            for sector in requested:
                blocklist.update(sector_map.get(sector, set()))
            if blocklist:
                from core import prescan_short_sell_filters as short_filters
                short_filters.SECTOR_BLOCKLIST.clear()
                short_filters.SECTOR_BLOCKLIST.update(blocklist)

    for day in sorted(all_days):
        if is_short_strategy(strategy_name):
            daily_snapshot = {
                symbol: daily_df[daily_df["time"] <= pd.Timestamp(day)].copy()
                for symbol, daily_df in daily_by_symbol.items()
            }
            gap_data = build_gap_data_from_candles(daily_snapshot)
            raw_symbols = [symbol for symbol, meta in gap_data.items() if float(meta.get("gap_pct", 0.0)) >= gap_threshold]
            shortlist = apply_prescan_filters(raw_symbols, gap_data, short_cfg)
            final_shortlists[day] = [
                (symbol, round(float(gap_data[symbol]["gap_pct"]), 2))
                for symbol in shortlist
            ]
            continue

        rows: list[Tuple[str, float]] = []
        for symbol, daily_df in daily_by_symbol.items():
            day_df = daily_df[daily_df["time"] == pd.Timestamp(day)]
            if day_df.empty:
                continue
            idx = day_df.index[0]
            if idx == 0:
                continue
            prev_close_val = float(daily_df.iloc[idx - 1]["close"])
            open_price = float(day_df.iloc[0]["open"])
            if prev_close_val <= 0 or open_price <= 0:
                continue
            gap_pct = ((open_price - prev_close_val) / prev_close_val) * 100
            if gap_pct >= gap_threshold:
                rows.append((symbol, round(gap_pct, 2)))
        final_shortlists[day] = sorted(rows, key=lambda row: row[1], reverse=True)[:shortlist_size]

    return final_shortlists


def main() -> None:
    args = parse_args()
    folder = Path(args.dir)
    all_data = load_directory_csvs(folder)
    if not all_data:
        print("No replay CSV files found.")
        return

    shortlists = build_daily_shortlists(
        all_data=all_data,
        strategy_name=args.strategy,
        gap_threshold=args.gap_threshold,
        shortlist_size=args.shortlist_size,
        gap_max=args.gap_max,
        min_prev_volume=args.min_prev_volume,
        min_price=args.min_price,
        exclude_sectors=args.exclude_sectors,
    )

    all_trades: List[ReplayTrade] = []

    print("Daily pre-scan shortlist")
    if is_short_strategy(args.strategy):
        cfg = PrescanConfig()
        cfg.gap_min_pct = max(args.gap_threshold, cfg.gap_min_pct)
        if args.gap_max > 0:
            cfg.gap_max_pct = args.gap_max
        if args.min_prev_volume > 0:
            cfg.min_prev_volume = int(args.min_prev_volume)
        if args.min_price > 0:
            cfg.min_price = float(args.min_price)
        if args.shortlist_size > 0:
            cfg.shortlist_size = args.shortlist_size
        print(
            "Short prefilter: "
            f"gap {cfg.gap_min_pct:.1f}% to {cfg.gap_max_pct:.1f}%, "
            f"prev volume >= {int(cfg.min_prev_volume)}, "
            f"price >= {int(cfg.min_price)}, "
            f"shortlist cap = {cfg.shortlist_size}"
        )
    for day in sorted(shortlists):
        rows = shortlists[day]
        names = ", ".join(f"{symbol} ({gap:+.2f}%)" for symbol, gap in rows) if rows else "None"
        print(f"{day}: {names}")

    print("\nReplay results")
    for day in sorted(shortlists):
        for symbol, _gap in shortlists[day]:
            df = all_data[symbol]
            day_df = df[df["time"].dt.strftime("%Y-%m-%d") == day].copy()
            if day_df.empty:
                continue
            trade = replay_symbol_day(symbol, day_df, args.strategy, symbol_history_df=df)
            if trade:
                all_trades.append(trade)

    if not all_trades:
        print("No trades triggered.")
        return

    per_symbol: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "be": 0, "eod": 0, "pnl": 0.0})
    for trade in all_trades:
        row = per_symbol[trade.symbol]
        row["trades"] += 1
        if trade.outcome == "TARGET":
            row["wins"] += 1
        elif trade.outcome == "SL":
            row["losses"] += 1
        elif trade.outcome == "BE":
            row["be"] += 1
        else:
            row["eod"] += 1
        row["pnl"] += trade.pnl_per_share

    for symbol in sorted(per_symbol):
        row = per_symbol[symbol]
        print(
            f"{symbol}: {int(row['trades'])} trades, "
            f"{int(row['wins'])} wins, "
            f"{int(row['losses'])} losses, "
            f"{row['pnl']:+.2f}"
        )

    total_trades = len(all_trades)
    total_wins   = sum(1 for t in all_trades if t.outcome == "TARGET")
    total_losses = sum(1 for t in all_trades if t.outcome == "SL")
    total_be     = sum(1 for t in all_trades if t.outcome == "BE")
    total_eod    = sum(1 for t in all_trades if t.outcome == "EOD")
    total_pnl    = round(sum(t.pnl_per_share for t in all_trades), 2)

    print("\nSummary")
    print(f"strategy: {args.strategy}")
    print(f"days with shortlist: {len(shortlists)}")
    print(f"trades: {total_trades}")
    print(f"wins: {total_wins}")
    print(f"losses: {total_losses}")
    print(f"be exits: {total_be}")
    print(f"eod exits: {total_eod}")
    print(f"total pnl: {total_pnl:+.2f}")


if __name__ == "__main__":
    main()
