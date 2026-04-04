"""
scripts/download_nifty_batches.py
──────────────────────────────────
Downloads 1 month of 5-minute data for Nifty 200/500 in batches.
Prevents rate-limiting by downloading in groups.
"""

import os
import time
import pandas as pd
import yfinance as yf
from config.settings import NIFTY200_WATCHLIST, NIFTY100_WATCHLIST

# Since we don't have a NIFTY500 list, we'll use NIFTY200 as the base for now.
# You can manually add more symbols to the NIFTY500 list below.
NIFTY500_LIST = NIFTY200_WATCHLIST # Placeholder

def download_batch(symbols, folder_name, period="1mo", interval="5m"):
    base_dir = f"trades/{folder_name}"
    os.makedirs(base_dir, exist_ok=True)
    
    print(f"🚀 Starting download for {len(symbols)} symbols into {base_dir}...")
    
    for i, symbol in enumerate(symbols):
        yf_sym = f"{symbol}.NS"
        try:
            print(f"[{i+1}/{len(symbols)}] Downloading {yf_sym}...", end="\r")
            df = yf.download(yf_sym, period=period, interval=interval, progress=False, auto_adjust=True)
            
            if df.empty:
                continue
                
            df = df.reset_index()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

            time_col = "Datetime" if "Datetime" in df.columns else "Date"
            df = df.rename(columns={time_col: "time", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            
            # Convert to IST and remove timezone information
            df['time'] = pd.to_datetime(df['time'])
            if df['time'].dt.tz is not None:
                df['time'] = df['time'].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
            
            # Important: Add the symbol column back for the backtester
            df['symbol'] = symbol.upper()
            
            # Calculate prev_close for each row
            # For intraday data, prev_close is the last 'close' of the PREVIOUS day.
            df['date'] = pd.to_datetime(df['time']).dt.date
            daily_closes = df.groupby('date')['close'].last().shift(1)
            df['prev_close'] = df['date'].map(daily_closes)
            
            # Drop temporary date col
            df = df.drop(columns=['date'])
            
            output_path = f"{base_dir}/{symbol.lower()}_5m_replay.csv"
            df.to_csv(output_path, index=False)
            
            # Sleep briefly to avoid IP ban
            time.sleep(0.5)
            
        except Exception as e:
            print(f"\n❌ Error downloading {symbol}: {e}")

    print(f"\n✅ Batch {folder_name} complete!")

def main():
    # 1. Download Nifty 200
    download_batch(NIFTY200_WATCHLIST, "nifty200_replay")
    
    # 2. Example: Download Nifty 500 in segments (If you provide the list)
    # segment1 = NIFTY500_LIST[:100]
    # download_batch(segment1, "nifty500_batch1")

if __name__ == "__main__":
    main()
