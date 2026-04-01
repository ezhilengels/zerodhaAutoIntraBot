"""
Download and convert multiple NSE Yahoo Finance symbols for replay testing.

Examples:
  python3 scripts/download_convert_batch_yfinance.py
  python3 scripts/download_convert_batch_yfinance.py --symbols NATIONALUM ONGC COALINDIA
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import CUSTOM_WATCHLIST  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch download Yahoo intraday CSVs and convert them for replay.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=CUSTOM_WATCHLIST[:5],
        help="NSE symbols without .NS suffix. Defaults to the first 5 custom-watchlist symbols.",
    )
    parser.add_argument("--period", default="5d", help="Yahoo period, e.g. 5d, 1mo")
    parser.add_argument("--interval", default="5m", help="Yahoo interval, e.g. 5m, 15m")
    parser.add_argument("--raw-dir", default="trades/yahoo_raw", help="Folder for raw Yahoo CSVs")
    parser.add_argument("--replay-dir", default="trades/yahoo_replay", help="Folder for replay-ready CSVs")
    return parser.parse_args()


def normalize_yahoo_csv(symbol: str, raw_path: Path, replay_path: Path) -> int:
    df = pd.read_csv(raw_path)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"{symbol}: missing columns in Yahoo CSV: {', '.join(sorted(missing))}")

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["symbol"] = symbol
    out["time_ts"] = pd.to_datetime(out["time"])
    out["trade_date"] = out["time_ts"].dt.date
    out["prev_close"] = pd.NA

    previous_close_by_day = {}
    previous_close = None
    for trade_date in sorted(out["trade_date"].unique()):
        if previous_close is not None:
            previous_close_by_day[trade_date] = previous_close
        day_rows = out[out["trade_date"] == trade_date]
        previous_close = float(day_rows.iloc[-1]["close"])

    out["prev_close"] = out["trade_date"].map(previous_close_by_day)
    out = out[["symbol", "time", "open", "high", "low", "close", "volume", "prev_close"]]
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(replay_path, index=False)
    return len(out)


def download_symbol(symbol: str, period: str, interval: str, raw_path: Path) -> int:
    yahoo_symbol = f"{symbol}.NS"
    df = yf.download(yahoo_symbol, period=period, interval=interval, auto_adjust=False, progress=False)
    if df.empty:
        raise ValueError(f"{symbol}: no Yahoo data returned")

    df = df.reset_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={time_col: "time"})
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(raw_path, index=False)
    return len(df)


def main() -> None:
    args = parse_args()
    raw_dir = Path(args.raw_dir) if Path(args.raw_dir).is_absolute() else PROJECT_ROOT / args.raw_dir
    replay_dir = Path(args.replay_dir) if Path(args.replay_dir).is_absolute() else PROJECT_ROOT / args.replay_dir

    successes: list[str] = []
    failures: list[str] = []

    for symbol in args.symbols:
        raw_path = raw_dir / f"{symbol.lower()}_{args.interval}.csv"
        replay_path = replay_dir / f"{symbol.lower()}_{args.interval}_replay.csv"
        try:
            raw_rows = download_symbol(symbol, args.period, args.interval, raw_path)
            replay_rows = normalize_yahoo_csv(symbol, raw_path, replay_path)
            successes.append(f"{symbol}: raw={raw_rows}, replay={replay_rows}")
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")

    print("Completed batch download/convert.")
    if successes:
        print("\nSuccesses")
        for line in successes:
            print(f"- {line}")
    if failures:
        print("\nFailures")
        for line in failures:
            print(f"- {line}")


if __name__ == "__main__":
    main()
