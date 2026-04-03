
import os
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

from data import upstox_provider as nse

def test_upstox():
    print("Testing Upstox Provider...")
    nse.init_session()
    
    symbol = "RELIANCE"
    print(f"\nFetching quote for {symbol}...")
    quote = nse.get_quote(symbol)
    if quote:
        print(f"✅ Quote Success: {quote}")
    else:
        print(f"❌ Quote Failed")

    print(f"\nFetching candles for {symbol}...")
    candles = nse.get_candles(symbol)
    if not candles.empty:
        print(f"✅ Candles Success: {len(candles)} rows")
        print(candles.tail(5))
    else:
        print(f"❌ Candles Failed")

    print(f"\nFetching daily candles for {symbol}...")
    daily = nse.get_daily_candles(symbol, days=5)
    if not daily.empty:
        print(f"✅ Daily Candles Success: {len(daily)} rows")
        print(daily)
    else:
        print(f"❌ Daily Candles Failed")

if __name__ == "__main__":
    test_upstox()
