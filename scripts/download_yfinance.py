"""
Download recent Yahoo Finance intraday candles and save them as CSV.

Examples:
  python3 scripts/download_yfinance.py
  python3 scripts/download_yfinance.py --symbol NATIONALUM.NS --period 5d --interval 5m
"""

from __future__ import annotations

import argparse
import os

import pandas as pd
import yfinance as yf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download intraday candles from Yahoo Finance.")
    parser.add_argument("--symbol", default="NATIONALUM.NS", help="Yahoo Finance symbol, e.g. NATIONALUM.NS")
    parser.add_argument("--period", default="5d", help="Recent history period, e.g. 5d, 1mo")
    parser.add_argument("--interval", default="5m", help="Candle interval, e.g. 5m, 15m, 1h")
    parser.add_argument(
        "--output",
        default="trades/nalco_5min.csv",
        help="CSV output path relative to project root or absolute path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    df = yf.download(args.symbol, period=args.period, interval=args.interval, auto_adjust=False, progress=False)
    if df.empty:
        raise SystemExit(f"No data returned for {args.symbol}. Try a different period/interval.")

    df = df.reset_index()

    # Flatten multi-index columns if Yahoo returns them.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={time_col: "time"})
    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename_map)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_path = args.output if os.path.isabs(args.output) else os.path.join(project_root, args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    df.to_csv(output_path, index=False)

    print(f"Saved {len(df)} rows to {output_path}")
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    main()
