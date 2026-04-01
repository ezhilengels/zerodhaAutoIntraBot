"""
core/session.py
────────────────
SessionState replaces every global variable from the original script.
One instance is created in main.py and passed/injected everywhere.

Benefits:
  • No module-level globals scattered across files
  • Easy to unit-test (create a fresh SessionState per test)
  • reset_daily() supports future multi-day running
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
from core.signal import Signal


@dataclass
class SessionState:
    # ── Counters ──────────────────────────────────────────────────────────
    trade_count:    int       = 0
    traded_symbols: set       = field(default_factory=set)
    prescan_sent:   bool      = False
    prescan_candidates: set   = field(default_factory=set)
    traded_levels: dict       = field(default_factory=dict)

    # ── Pre-market cache ──────────────────────────────────────────────────
    prev_close_map: Dict[str, float] = field(default_factory=dict)

    # ── Pending Telegram confirmations ────────────────────────────────────
    # Maps callback_key → Signal, until the user taps Place / Skip
    pending_signals: Dict[str, Signal] = field(default_factory=dict)
    live_signals: Dict[str, Signal] = field(default_factory=dict)

    # ─────────────────────────────────────────────────────────────────────
    #  Trade helpers
    # ─────────────────────────────────────────────────────────────────────

    def already_traded(self, symbol: str) -> bool:
        return symbol in self.traded_symbols

    def register_trade(self, signal: Signal) -> None:
        self.trade_count += 1
        self.traded_symbols.add(signal.symbol)
        self.live_signals[signal.symbol] = signal

    def close_live_trade(self, symbol: str) -> None:
        self.live_signals.pop(symbol, None)

    # ─────────────────────────────────────────────────────────────────────
    #  Pending-signal helpers
    # ─────────────────────────────────────────────────────────────────────

    def add_pending(self, key: str, signal: Signal) -> None:
        self.pending_signals[key] = signal

    def pop_pending(self, key: str) -> Optional[Signal]:
        return self.pending_signals.pop(key, None)

    # ─────────────────────────────────────────────────────────────────────
    #  Summary
    # ─────────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        stocks = ", ".join(self.traded_symbols) if self.traded_symbols else "None"
        return f"Trades: {self.trade_count}  |  Stocks: {stocks}"

    # ─────────────────────────────────────────────────────────────────────
    #  Daily reset (for future multi-day support)
    # ─────────────────────────────────────────────────────────────────────

    def reset_daily(self) -> None:
        self.trade_count     = 0
        self.traded_symbols  = set()
        self.prescan_sent    = False
        self.prescan_candidates = set()
        self.traded_levels   = {}
        self.prev_close_map  = {}
        self.pending_signals = {}
        self.live_signals    = {}
