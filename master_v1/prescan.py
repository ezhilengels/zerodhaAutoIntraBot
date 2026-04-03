"""
master_v1/prescan.py
──────────────────────
Morning Gatekeeper for Master Combo v1.
Filters Nifty 50/100 down to the Top 10 Quality Movers.
"""

import pandas as pd
from typing import List, Dict
from data import upstox_provider as nse
from utils.logger import get_logger

log = get_logger(__name__)

# ─────────────────────────────────────────────
#  PRE-MARKET FILTER LOGIC
# ─────────────────────────────────────────────

def run_daily_prescan(symbols: List[str], min_gap=0.5, min_turnover_cr=5.0) -> List[str]:
    """
    Scans a list of symbols and returns the Top 10 based on strength and liquidity.
    """
    log.info(f"🔍 Master V1 Prescan: Scanning {len(symbols)} symbols...")
    
    # 1. Get Market (Nifty 50) Gap for Relative Strength
    market_quote = nse.get_quote("NIFTY 50")
    market_gap = 0.0
    if market_quote:
        m_pc = float(market_quote.get("prev_close") or 0)
        m_op = float(market_quote.get("open") or 0)
        if m_pc > 0:
            market_gap = ((m_op - m_pc) / m_pc) * 100

    log.info(f"📊 Nifty 50 Gap: {market_gap:+.2f}%")

    passed_stocks = []

    for symbol in symbols:
        quote = nse.get_quote(symbol)
        if not quote: continue
        
        pc = float(quote.get("prev_close") or 0)
        op = float(quote.get("open") or 0)
        if pc <= 0 or op <= 0: continue
        
        gap_pct = ((op - pc) / pc) * 100
        
        # Rule 1: Minimum Gap
        if gap_pct < min_gap: continue
        
        # Rule 2: Relative Strength (Must beat Nifty)
        if gap_pct <= market_gap: continue
        
        # Rule 3: Turnover Check (Last 20 days average)
        daily_df = nse.get_daily_candles(symbol, days=20)
        if daily_df.empty: continue
        
        # Average Turnover = Avg(Close * Volume)
        daily_df["turnover"] = daily_df["close"] * daily_df["volume"]
        avg_turnover_cr = daily_df["turnover"].mean() / 10_000_000.0 # to Crores
        
        if avg_turnover_cr < min_turnover_cr: continue
        
        passed_stocks.append({
            "symbol": symbol,
            "gap_pct": gap_pct,
            "turnover": avg_turnover_cr
        })

    # 2. Sort by Gap (Strongest first) and take Top 10
    passed_stocks.sort(key=lambda x: x["gap_pct"], reverse=True)
    top_10 = [s["symbol"] for s in passed_stocks[:10]]
    
    log.info(f"✅ Prescan Complete. Shortlisted {len(top_10)} stocks: {top_10}")
    return top_10
