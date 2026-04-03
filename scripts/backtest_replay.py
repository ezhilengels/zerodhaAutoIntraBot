"""
Replay historical intraday candles through the existing strategy modules.

Expected CSV columns:
  symbol,time,open,high,low,close,volume[,prev_close]

Usage examples:
  python3 scripts/backtest_replay.py --csv data/sample.csv --strategy orb
  python3 scripts/backtest_replay.py --csv data/sample.csv --strategy multi --min-confirmations 2
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.session import SessionState
from core.signal import Signal
from strategy import ema_crossover, orb, pivot_breakout, pullback, vwap_reclaim, vwap_rsi
from strategy.v1 import short_intraday as short_intraday_v1
from strategy.v2 import pivot_breakout as pivot_breakout_v2
from strategy.v2 import short_intraday as short_intraday_v2
from strategy.v2 import vwap_reclaim as vwap_reclaim_v2
from strategy.v2 import vwap_rsi as vwap_rsi_v2
from strategy.v3 import short_intraday as short_intraday_v3
from strategy.v4 import short_intraday as short_intraday_v4
from strategy.v6 import short_intraday_v6
from strategy.v3 import vwap_rsi as vwap_rsi_v3
from strategy.v4 import vwap_rsi_bot as vwap_rsi_v4
from strategy.v5 import ath_reversal_bot as ath_reversal_v5
from master_v1 import strategy as master_v1


StrategyModule = object


@dataclass
class ReplayTrade:
    date: str
    symbol: str
    strategy: str
    triggered_by: str
    entry_time: str
    exit_time: str
    entry: float
    stop_loss: float
    target: float
    exit_price: float
    quantity: int
    outcome: str
    pnl_per_share: float
    gross_pnl_rupees: float
    charges_rupees: float
    net_pnl_rupees: float
    rr: float


STRATEGY_MODULES: Dict[str, StrategyModule] = {
    "pullback": pullback,
    "orb": orb,
    "vwap_reclaim": vwap_reclaim,
    "vwap_reclaim_v2": vwap_reclaim_v2,
    "vwap_rsi": vwap_rsi,
    "vwap_rsi_v2": vwap_rsi_v2,
    "vwap_rsi_v3": vwap_rsi_v3,
    "vwap_rsi_v4": vwap_rsi_v4,
    "pivot_breakout": pivot_breakout,
    "pivot_breakout_v2": pivot_breakout_v2,
    "ema_crossover": ema_crossover,
    "short_intraday_v1": short_intraday_v1,
    "short_intraday_v2": short_intraday_v2,
    "short_intraday_v3": short_intraday_v3,
    "short_intraday_v4": short_intraday_v4,
    "short_intraday_v6": short_intraday_v6,
    "ath_reversal_v5": ath_reversal_v5,
    "master_v1": master_v1,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay CSV candles through strategy logic.")
    parser.add_argument("--csv", required=True, help="Path to CSV with candle data.")
    parser.add_argument(
        "--strategy",
        default="multi",
        choices=["pullback", "orb", "vwap_reclaim", "vwap_reclaim_v2", "vwap_rsi", "vwap_rsi_v2", "vwap_rsi_v3", "vwap_rsi_v4", "pivot_breakout", "pivot_breakout_v2", "ema_crossover", "short_intraday_v1", "short_intraday_v2", "short_intraday_v3", "short_intraday_v4", "short_intraday_v6", "ath_reversal_v5", "master_v1", "multi"],
        help="Strategy mode to replay.",
    )
    parser.add_argument(
        "--min-confirmations",
        type=int,
        default=1,
        help="For multi mode, minimum strategies required to trigger a trade.",
    )
    return parser.parse_args()


def load_candles(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"symbol", "time", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    if "prev_close" not in df.columns:
        df["prev_close"] = pd.NA

    df = df.sort_values(["symbol", "time"]).reset_index(drop=True)
    return df


def infer_prev_close(day_df: pd.DataFrame) -> float:
    prev_close = day_df["prev_close"].dropna()
    if not prev_close.empty:
        return float(prev_close.iloc[0])
    return float(day_df.iloc[0]["open"])


def build_quote(symbol: str, partial_df: pd.DataFrame, prev_close: float) -> dict:
    last = partial_df.iloc[-1]
    return {
        "symbol": symbol,
        "ltp": float(last["close"]),
        "open": float(partial_df.iloc[0]["open"]),
        "high": float(partial_df["high"].max()),
        "low": float(partial_df["low"].min()),
        "prev_close": float(prev_close),
        "volume": int(partial_df["volume"].sum()),
    }


def build_daily_history(symbol_df: pd.DataFrame, current_day: object) -> pd.DataFrame:
    history = symbol_df[symbol_df["time"].dt.date < current_day].copy()
    if history.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    history["trade_date"] = history["time"].dt.date
    daily = (
        history.groupby("trade_date", sort=True)
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
    daily["time"] = pd.to_datetime(daily["time"])
    return daily[["time", "open", "high", "low", "close", "volume"]]


@contextmanager
def patched_market_view(partial_df: pd.DataFrame, quote: dict, hhmm: str, daily_history_df: Optional[pd.DataFrame] = None):
    originals: List[Tuple[object, str, object]] = []

    def patch(obj: object, attr: str, value: object) -> None:
        if not hasattr(obj, attr):
            return
        originals.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    for module in STRATEGY_MODULES.values():
        patch(module.nse, "get_candles", lambda symbol, df=partial_df: df.copy())
        patch(module, "current_hhmm", lambda hhmm=hhmm: hhmm)

    patch(pivot_breakout.nse, "get_quote", lambda symbol, q=quote: q.copy())
    patch(pullback.nse, "get_quote", lambda symbol, q=quote: q.copy())
    if daily_history_df is not None:
        patch(pivot_breakout_v2.nse, "get_daily_candles", lambda symbol, days=5, df=daily_history_df: df.tail(days).copy())
    patch(pivot_breakout_v2.nse, "get_index_candles", lambda index_name: pd.DataFrame())

    try:
        yield
    finally:
        for obj, attr, value in reversed(originals):
            setattr(obj, attr, value)


def detect_signal(strategy_name: str, symbol: str, state: SessionState, quote: dict) -> Optional[Signal]:
    if strategy_name == "pullback":
        signal = pullback.detect(symbol, state, quote=quote)
        if signal:
            signal.strategy_names = ["pullback"]
        return signal

    if strategy_name == "multi":
        triggered: List[Tuple[str, Signal]] = []

        orb_signal = orb.detect(symbol, state)
        if orb_signal:
            triggered.append(("orb", orb_signal))

        vwap_rsi_signal = vwap_rsi.detect(symbol, state)
        if vwap_rsi_signal:
            triggered.append(("vwap_rsi", vwap_rsi_signal))

        ema_signal = ema_crossover.detect(symbol, state)
        if ema_signal:
            triggered.append(("ema_crossover", ema_signal))

        if len(triggered) < max(1, ARGS.min_confirmations):
            return None

        primary = triggered[0][1]
        primary.strategy_names = [name for name, _ in triggered]
        return primary

    module = STRATEGY_MODULES[strategy_name]
    signal = module.detect(symbol, state)
    if signal:
        signal.strategy_names = [strategy_name]
    return signal


def exit_trade(signal: Signal, future_df: pd.DataFrame, strategy_name: str = "") -> Tuple[str, pd.Timestamp, float]:
    direction   = getattr(signal, "direction", "LONG")
    sl          = signal.stop_loss
    be_trigger  = getattr(signal, "be_stop_trigger", 0.0)
    be_active   = False

    module = STRATEGY_MODULES.get(strategy_name)
    can_exit_early = hasattr(module, "should_exit_early")

    for i, (_, row) in enumerate(future_df.iterrows()):
        low  = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])

        # Early Exit Logic (EMA Flip / VWAP Break)
        if can_exit_early:
            # We mock the market view for the exit check
            partial_until_now = future_df.iloc[: i + 1]
            # (Simple version: we just check if the rule triggers)
            if module.should_exit_early(signal.symbol, signal):
                return "EARLY_EXIT", pd.Timestamp(row["time"]), close

        if direction == "SHORT":
            # SHORT: SL is ABOVE entry, TP is BELOW entry
            if high >= sl:
                return "SL", pd.Timestamp(row["time"]), float(sl)
            if low <= signal.target:
                return "TARGET", pd.Timestamp(row["time"]), float(signal.target)
        else:
            # LONG: BE slide logic + standard SL/TP
            if be_trigger and not be_active and high >= be_trigger:
                sl        = signal.entry
                be_active = True
                continue   # evaluate new stop from the NEXT candle onward

            if low <= sl:
                outcome = "BE" if be_active else "SL"
                return outcome, pd.Timestamp(row["time"]), float(sl)
            if high >= signal.target:
                return "TARGET", pd.Timestamp(row["time"]), float(signal.target)

    last = future_df.iloc[-1]
    return "EOD", pd.Timestamp(last["time"]), float(last["close"])


def estimate_zerodha_intraday_equity_charges(entry: float, exit_price: float, quantity: int) -> float:
    """
    Estimate Zerodha NSE equity intraday charges.

    Rates used:
      - Brokerage: 0.03% or Rs 20 per executed order, whichever is lower
      - STT: 0.025% on sell side
      - NSE transaction charges: 0.00307% on turnover
      - SEBI charges: Rs 10 / crore on turnover
      - Stamp duty: 0.003% on buy side
      - GST: 18% on (brokerage + transaction charges + SEBI charges)

    Sources:
      - https://zerodha.com/charges/
      - https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/exchange-transaction-charges
      - https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated
    """
    buy_turnover = entry * quantity
    sell_turnover = exit_price * quantity
    turnover = buy_turnover + sell_turnover

    brokerage = min(buy_turnover * 0.0003, 20.0) + min(sell_turnover * 0.0003, 20.0)
    stt = round(sell_turnover * 0.00025)
    txn_charges = turnover * 0.0000307
    sebi = turnover * 0.000001
    stamp = buy_turnover * 0.00003
    gst = 0.18 * (brokerage + txn_charges + sebi)

    return round(brokerage + stt + txn_charges + sebi + stamp + gst, 2)


def replay_symbol_day(symbol: str, day_df: pd.DataFrame, strategy_name: str, symbol_history_df: Optional[pd.DataFrame] = None) -> Optional[ReplayTrade]:
    day_df = day_df.sort_values("time").reset_index(drop=True)
    prev_close = infer_prev_close(day_df)
    state = SessionState(prev_close_map={symbol: prev_close})
    daily_history_df = build_daily_history(symbol_history_df, day_df.iloc[0]["time"].date()) if symbol_history_df is not None else None

    for idx in range(len(day_df)):
        partial_df = day_df.iloc[: idx + 1].copy()
        hhmm = pd.Timestamp(partial_df.iloc[-1]["time"]).strftime("%H:%M")
        quote = build_quote(symbol, partial_df, prev_close)

        with patched_market_view(partial_df, quote, hhmm, daily_history_df=daily_history_df):
            signal = detect_signal(strategy_name, symbol, state, quote)

        if not signal:
            continue

        future_df = day_df.iloc[idx + 1 :].copy()
        if future_df.empty:
            continue

        outcome, exit_time, exit_price = exit_trade(signal, future_df, strategy_name=strategy_name)
        entry_time = pd.Timestamp(partial_df.iloc[-1]["time"])
        direction  = getattr(signal, "direction", "LONG")
        # PnL is positive when trade moves in intended direction
        if direction == "SHORT":
            pnl  = round(signal.entry - exit_price, 2)   # profit when price falls
            risk = signal.stop_loss - signal.entry        # SL is above entry
        else:
            pnl  = round(exit_price - signal.entry, 2)   # profit when price rises
            risk = signal.entry - signal.stop_loss        # SL is below entry
        gross_pnl_rupees = round(pnl * signal.quantity, 2)
        charges_rupees = estimate_zerodha_intraday_equity_charges(signal.entry, exit_price, signal.quantity)
        net_pnl_rupees = round(gross_pnl_rupees - charges_rupees, 2)
        rr = round(pnl / risk, 2) if risk > 0 else 0.0

        return ReplayTrade(
            date=entry_time.strftime("%Y-%m-%d"),
            symbol=symbol,
            strategy=strategy_name,
            triggered_by=", ".join(signal.strategy_names) if signal.strategy_names else strategy_name,
            entry_time=entry_time.strftime("%H:%M"),
            exit_time=exit_time.strftime("%H:%M"),
            entry=round(signal.entry, 2),
            stop_loss=round(signal.stop_loss, 2),
            target=round(signal.target, 2),
            exit_price=round(exit_price, 2),
            quantity=signal.quantity,
            outcome=outcome,
            pnl_per_share=pnl,
            gross_pnl_rupees=gross_pnl_rupees,
            charges_rupees=charges_rupees,
            net_pnl_rupees=net_pnl_rupees,
            rr=rr,
        )

    return None


def summarise(trades: Iterable[ReplayTrade]) -> None:
    trades = list(trades)
    if not trades:
        print("No trades triggered.")
        return

    wins = sum(1 for trade in trades if trade.outcome == "TARGET")
    losses = sum(1 for trade in trades if trade.outcome == "SL")
    early = sum(1 for trade in trades if trade.outcome == "EARLY_EXIT")
    eod = sum(1 for trade in trades if trade.outcome == "EOD")
    total_pnl = round(sum(trade.pnl_per_share for trade in trades), 2)
    gross_total = round(sum(trade.gross_pnl_rupees for trade in trades), 2)
    total_charges = round(sum(trade.charges_rupees for trade in trades), 2)
    net_total = round(sum(trade.net_pnl_rupees for trade in trades), 2)
    avg_rr = round(sum(trade.rr for trade in trades) / len(trades), 2)

    print("\nTrades")
    print("date       symbol      strategy        triggered_by                         entry  exit   qty  outcome  pnl/share  gross      charges    net        rr")
    for trade in trades:
        print(
            f"{trade.date}  "
            f"{trade.symbol:<10}  "
            f"{trade.strategy:<14}  "
            f"{trade.triggered_by:<35}  "
            f"{trade.entry:>6.2f}  "
            f"{trade.exit_price:>6.2f}  "
            f"{trade.quantity:>3}  "
            f"{trade.outcome:<7}  "
            f"{trade.pnl_per_share:>9.2f}  "
            f"{trade.gross_pnl_rupees:>9.2f}  "
            f"{trade.charges_rupees:>9.2f}  "
            f"{trade.net_pnl_rupees:>9.2f}  "
            f"{trade.rr:>4.2f}"
        )

    print("\nSummary")
    print(f"trades      : {len(trades)}")
    print(f"wins        : {wins}")
    print(f"losses      : {losses}")
    print(f"early exits : {early}")
    print(f"eod exits   : {eod}")
    print(f"win rate    : {round((wins / len(trades)) * 100, 1)}%")
    print(f"total pnl   : ₹{total_pnl} per share")
    print(f"gross pnl   : ₹{gross_total}")
    print(f"charges     : ₹{total_charges}")
    print(f"net pnl     : ₹{net_total}")
    print(f"average rr  : {avg_rr}")


def main() -> None:
    df = load_candles(ARGS.csv)
    trades: List[ReplayTrade] = []

    for symbol, symbol_df in df.groupby("symbol", sort=True):
        symbol_df = symbol_df.sort_values("time").reset_index(drop=True)
        for _trade_date, day_df in symbol_df.groupby(symbol_df["time"].dt.date, sort=True):
            trade = replay_symbol_day(symbol, day_df, ARGS.strategy, symbol_history_df=symbol_df)
            if trade:
                trades.append(trade)

    summarise(trades)


ARGS = parse_args()


if __name__ == "__main__":
    main()
