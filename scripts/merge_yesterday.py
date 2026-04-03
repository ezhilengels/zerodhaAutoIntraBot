
import pandas as pd
import glob
import os

files = glob.glob("trades/yahoo_yesterday/*_replay.csv")
all_df = []
for f in files:
    df = pd.read_csv(f)
    # Filter for only April 2nd, 2026
    df['time'] = pd.to_datetime(df['time'])
    yesterday = df[df['time'].dt.date == pd.to_datetime('2026-04-02').date()]
    if not yesterday.empty:
        all_df.append(yesterday)

if all_df:
    final = pd.concat(all_df).sort_values(['symbol', 'time'])
    final.to_csv("trades/yesterday_nifty5_replay.csv", index=False)
    print(f"Merged {len(all_df)} stocks into trades/yesterday_nifty5_replay.csv")
else:
    print("No data found for yesterday.")
