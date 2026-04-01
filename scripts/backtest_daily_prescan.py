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
sys.argv = _ORIGINAL_ARGV


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
    gap_threshold: float,
    shortlist_size: int,
) -> Dict[str, List[Tuple[str, float]]]:
    per_day: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    for symbol, df in all_data.items():
        for trade_date, day_df in df.groupby(df["time"].dt.date, sort=True):
            day_df = day_df.sort_values("time").reset_index(drop=True)
            prev_close = day_df["prev_close"].dropna()
            if prev_close.empty:
                continue
            prev_close_val = float(prev_close.iloc[0])
            open_price = float(day_df.iloc[0]["open"])
            if prev_close_val <= 0 or open_price <= 0:
                continue
            gap_pct = ((open_price - prev_close_val) / prev_close_val) * 100
            if gap_pct >= gap_threshold:
                per_day[str(trade_date)].append((symbol, round(gap_pct, 2)))

    for day, rows in list(per_day.items()):
        rows = sorted(rows, key=lambda row: row[1], reverse=True)[:shortlist_size]
        per_day[day] = rows

    return dict(per_day)


def main() -> None:
    args = parse_args()
    folder = Path(args.dir)
    all_data = load_directory_csvs(folder)
    if not all_data:
        print("No replay CSV files found.")
        return

    shortlists = build_daily_shortlists(
        all_data=all_data,
        gap_threshold=args.gap_threshold,
        shortlist_size=args.shortlist_size,
    )

    all_trades: List[ReplayTrade] = []

    print("Daily pre-scan shortlist")
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
