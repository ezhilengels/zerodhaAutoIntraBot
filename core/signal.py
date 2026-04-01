"""
core/signal.py
───────────────
The Signal dataclass is the typed contract that flows from strategy → broker
and strategy → notifications.  Using a typed dataclass instead of a raw dict
gives you autocomplete, clear field names, and easy extensibility.

To add a field (e.g. atr, trend_score): just add it here — every consumer
receives it automatically.
"""

from dataclasses import dataclass, asdict, field
from typing import List


@dataclass
class Signal:
    # Trade parameters
    symbol:    str
    entry:     float
    stop_loss: float
    target:    float
    quantity:  int
    capital:   float   # ₹ total deployed (entry × quantity)

    # Diagnostic metadata (shown in Telegram alert + written to trade log)
    gap_pct:   float = 0.0
    vwap:      float = 0.0
    rsi:       float = 0.0
    vol_ratio: float = 0.0
    strategy_names: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def risk(self) -> float:
        return round(self.entry - self.stop_loss, 2)

    def reward(self) -> float:
        return round(self.target - self.entry, 2)

    def rr_ratio(self) -> float:
        r = self.risk()
        return round(self.reward() / r, 2) if r else 0.0
