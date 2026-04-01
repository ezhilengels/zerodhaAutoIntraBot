"""
Convert a Yahoo Finance intraday CSV into the replay format used by backtest_replay.py.

Examples:
  python3 scripts/convert_yfinance_csv.py
  python3 scripts/convert_yfinance_csv.py --input trades/nalco_5min.csv --symbol NATIONALUM
"""

from __future__ import annotations

import argparse
import os

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Yahoo intraday CSV to replay format.")
    parser.add_argument("--input", default="trades/nalco_5min.csv", help="Path to Yahoo Finance CSV")
    parser.add_argument("--output", default="trades/nalco_5min_replay.csv", help="Path to replay-ready CSV")
    parser.add_argument("--symbol", default="NATIONALUM", help="Symbol name to stamp into the replay CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_path = args.input if os.path.isabs(args.input) else os.path.join(project_root, args.input)
    output_path = args.output if os.path.isabs(args.output) else os.path.join(project_root, args.output)

    df = pd.read_csv(input_path)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise SystemExit(f"Missing columns in Yahoo CSV: {', '.join(sorted(missing))}")

    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["symbol"] = args.symbol

    # Approximate prev_close per day from the prior candle close across day boundaries.
    out["time_ts"] = pd.to_datetime(out["time"])
    out["trade_date"] = out["time_ts"].dt.date
    out["prev_close"] = pd.NA

    day_opens = out.groupby("trade_date", sort=True).head(1).copy()
    previous_close_by_day = {}
    previous_close = None
    for row in day_opens.itertuples(index=False):
        if previous_close is not None:
            previous_close_by_day[row.trade_date] = previous_close
        day_rows = out[out["trade_date"] == row.trade_date]
        previous_close = float(day_rows.iloc[-1]["close"])

    out["prev_close"] = out["trade_date"].map(previous_close_by_day)
    out = out[["symbol", "time", "open", "high", "low", "close", "volume", "prev_close"]]

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    out.to_csv(output_path, index=False)

    print(f"Saved {len(out)} rows to {output_path}")
    print(out.head().to_string(index=False))


if __name__ == "__main__":
    main()
