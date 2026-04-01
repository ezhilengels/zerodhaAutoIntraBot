"""
config/v3/vwap_rsi.py
─────────────────────
Standalone config for VWAP + RSI strategy v3.

This version adds ADX and symbol-specific overrides without changing v1/v2.
"""

from dataclasses import dataclass, field
import copy


@dataclass
class VWAPRsiConfig:
    start_time:                  str   = "09:30"
    end_time:                    str   = "13:30"
    rsi_threshold:               float = 50.0
    rsi_overbought:              float = 68.0
    volume_multiplier_min:       float = 1.2
    max_distance_above_vwap_pct: float = 0.50
    adx_min_threshold:           float = 20.0
    symbol_overrides: dict = field(default_factory=lambda: {
        "COALINDIA": {
            "rsi_threshold": 54.0,
            "volume_multiplier_min": 1.6,
            "max_distance_above_vwap_pct": 0.30,
            "adx_min_threshold": 25.0,
        },
        "ONGC": {
            "adx_min_threshold": 18.0,
        },
    })

    def for_symbol(self, symbol: str) -> "VWAPRsiConfig":
        """Return a config copy with symbol-specific overrides applied."""
        overrides = self.symbol_overrides.get(symbol.upper(), {})
        if not overrides:
            return self
        cfg = copy.copy(self)
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg


vwap_rsi_v3_cfg = VWAPRsiConfig()
