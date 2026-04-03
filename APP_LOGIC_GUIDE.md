# Pullback Trader - Application Logic Guide

This document explains how the app filters stocks and maps data from the Upstox API.

## 1. The "Prescan" Filter Logic
The goal of the prescan is to ignore the "noise" and find only the stocks that are likely to move today.

### Phase 1: The Funnel (Premarket)
Located in `core/prescan.py` and `prescanV2/premarket_filter.py`.
1. **Universe:** Start with Nifty 100 or Nifty 500 stocks.
2. **Liquidity Check:** Remove any stock with low volume.
3. **Price Bracket:** Ensure the price is high enough to avoid pump-and-dump stocks.
4. **Gap Analysis:** Identify stocks that opened +/- 1% away from yesterday's price.

### Phase 2: Technical Filtering (Intraday)
Located in `core/short_prescan_filters.py`.
* **Weakness Check:** If the stock is trading *below* its opening price and *below* VWAP, it is considered "weak" (ideal for Shorting).
* **Pullback Check:** The app waits for a weak stock to "bounce" back up to a resistance (like the 8 EMA or 20 EMA) before entering a Short trade.

## 2. Variable Mappings
How the code talks to the market:

| Variable | Description | Source |
| :--- | :--- | :--- |
| `symbol` | Trading Name (e.g., RELIANCE) | User Input / CSV |
| `instrument_key` | Upstox internal ID (Pipe format) | `upstox_instruments.csv` |
| `ltp` | Current Live Price | `get_quote()` |
| `ohlc` | Open, High, Low, Close | `get_candles()` |
| `volume` | Total shares traded today | `get_quote()` |
| `interval_min` | Candle timeframe (usually 5 or 15) | Strategy Config |

## 3. How to Read the Logs
* **INFO:** "Loaded instrument keys" -> App is starting correctly.
* **WARNING:** "No candles returned" -> Usually means the market is closed or it's a holiday.
* **ERROR:** "401 Unauthorized" -> Your `UPSTOX_ACCESS_TOKEN` has expired (Run `scripts/upstox_login.py`).
* **ERROR:** "400 Bad Request" -> The symbol or key format is wrong.

## 4. Execution Workflow
1. `main.py` starts and calls `session.init()`.
2. `prescan.py` runs to find today's stocks.
3. `signal.py` monitors the live feed for those stocks.
4. If a condition (like `close < vwap`) hits, it triggers a trade.
5. `telegram_notifier.py` sends an alert to your phone.
