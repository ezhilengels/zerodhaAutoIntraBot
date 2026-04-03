
import requests
import pandas as pd
import io

def download_instruments():
    print("Downloading Upstox instruments...")
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    response = requests.get(url)
    if response.status_code == 200:
        df = pd.read_csv(io.BytesIO(response.content), compression='gzip')
        # Filter for NSE Equity
        nse_eq = df[(df['exchange'] == 'NSE_EQ') & (df['instrument_type'] == 'EQUITY')]
        print(f"Total NSE Equity instruments: {len(nse_eq)}")
        
        # Create a simple mapping symbol -> instrument_key
        mapping = nse_eq[['tradingsymbol', 'instrument_key']]
        mapping.to_csv("data/upstox_instruments.csv", index=False)
        print("Saved mapping to data/upstox_instruments.csv")
        
        # Let's see RELIANCE
        rel = nse_eq[nse_eq['tradingsymbol'] == 'RELIANCE']
        print(f"RELIANCE info:\n{rel}")
    else:
        print(f"Failed to download: {response.status_code}")

if __name__ == "__main__":
    download_instruments()
