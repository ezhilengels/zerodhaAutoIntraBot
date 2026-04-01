# Pullback Trader

Gap-up pullback-to-VWAP intraday scanner with Telegram alerts and Kite Connect auto-orders.

---

## Project Structure

```
pullback_trader/
│
├── main.py                          # Entry point — orchestrates everything
│
├── config/
│   └── settings.py                  # ALL config: credentials, params, paths
│
├── core/
│   ├── signal.py                    # Signal dataclass (typed contract)
│   └── session.py                   # SessionState (replaces all globals)
│
├── data/
│   └── nse_provider.py              # NSE HTTP: quotes + intraday candles
│
├── strategy/
│   ├── indicators.py                # Pure indicator math (RSI, VWAP, ATR…)
│   └── pullback.py                  # Entry logic: gap-up + VWAP pullback
│
├── broker/
│   └── kite_broker.py               # Kite auth, order placement, CSV log
│
├── notifications/
│   └── telegram_notifier.py         # Alerts, inline buttons, bot thread
│
├── utils/
│   ├── logger.py                    # Centralised logging (file + console)
│   └── time_helpers.py              # Trading-window checks, timestamps
│
├── logs/                            # Auto-created — pullback_trader.log
├── trades/                          # Auto-created — trade_log.csv
└── requirements.txt
```

---

## Setup

```bash
pip install -r requirements.txt
```

Edit `config/settings.py`:
- `TelegramConfig` — bot_token + chat_id
- `KiteConfig` — api_key + request_token (refresh every morning)
- `ExecutionConfig` — set `PAPER_TRADING=true` in `.env` to simulate orders safely
- `WATCHLIST` — list of NSE symbols
- `StrategyConfig` — gap threshold, VWAP range, RSI, SL%, R:R, capital
- `ScannerConfig` — trade window, scan interval, max trades

---

## Run

```bash
caffeinate -i python3 main.py     # macOS (keeps machine awake)
python3 main.py                   # any OS
```

Paper mode is enabled by default via `.env` lookup:

```bash
PAPER_TRADING=true
```

Set `PAPER_TRADING=false` only when you are ready to place live Zerodha orders.

To choose the Zerodha product type for live orders, add:

```bash
ORDER_PRODUCT=MIS
```

Use `MIS` for intraday or `CNC` for delivery.

To choose the active strategy, add:

```bash
STRATEGY_MODE=pullback
```

Use `pullback` for the existing gap-up VWAP strategy, `orb` for the opening-range breakout strategy, `vwap_reclaim` for the original VWAP reclaim intraday strategy, `vwap_reclaim_v2` for the higher-quality VWAP reclaim v2 strategy, `vwap_rsi` for the original VWAP + RSI combo strategy, `vwap_rsi_v2` for the newer VWAP pullback + RSI trend strategy, `vwap_rsi_v3` for the ADX and symbol-aware VWAP pullback + RSI trend strategy, `pivot_breakout` for the daily pivot breakout strategy, `ema_crossover` for the EMA crossover strategy, or `multi` to run the available strategies and send one merged alert per stock.

For `multi` mode, you can control how many strategies must agree:

```bash
MIN_STRATEGY_CONFIRMATIONS=1
```

Set it to `1` for "any one strategy passes" or `2` later if you want two-strategy confirmation.

If you want the bot to send a TRUE/FALSE scan summary to Telegram every 5 minutes, add:

```bash
SHOW_SCAN_RESULTS=true
```

Leave it as `false` or omit it to receive only actual signal alerts.

To test the full Telegram button flow instantly in paper mode, send:

```bash
/testsignal
```

The bot will send a sample signal card with `✅ Place Order` and `❌ Skip` buttons.

To check the current execution mode at any time in Telegram, send:

```bash
/mode
```

## Backtest Replay

You can replay historical 5-minute candles through the existing strategy logic:

```bash
python3 scripts/backtest_replay.py --csv /path/to/candles.csv --strategy orb
python3 scripts/backtest_replay.py --csv /path/to/candles.csv --strategy multi --min-confirmations 2
```

Expected CSV columns:

```text
symbol,time,open,high,low,close,volume,prev_close
```

`prev_close` is optional but recommended for `pullback` and `pivot_breakout`.

The replay script:
- feeds candles bar by bar into the current strategy code
- takes the first trade per symbol per day
- exits on stop-loss, target, or end-of-day close
- prints a trade list plus summary stats

While the bot is running live with NSE data, it also appends fetched candles to:

```text
trades/replay_candles.csv
```

So you can build your own replay dataset over time without subscribing to Kite historical data.

---

## Daily Kite Login

1. Visit `https://kite.trade/connect/login?api_key=YOUR_API_KEY&v=3`
2. Login → complete TOTP
3. Copy `request_token` from the redirect URL
4. Paste into `kite_cfg.request_token` in `config/settings.py`

---

## How to Add a New Strategy

1. Create `strategy/orb.py` (or any name) with this interface:
   ```python
   def detect(symbol: str, state: SessionState) -> Signal | None:
       ...
   ```
2. Import and call it in `main.py` alongside or instead of `pullback.detect()`.

No other files need to change.

---

## Strategy Tuning Cheatsheet

All parameters live in `StrategyConfig` in `config/settings.py`:

| Parameter | What it controls |
|---|---|
| `gap_up_threshold` | Min % gap-up required at open |
| `pullback_vwap_range` | How close to VWAP the price must return |
| `rsi_min / rsi_max` | RSI window for a valid pullback |
| `volume_ratio_max` | Ensures pullback happens on quiet volume |
| `stop_loss_pct` | Fixed % SL below entry |
| `reward_ratio` | Target multiplier from risk (e.g. 2 = 1:2 R:R) |
| `max_capital_per_trade` | ₹ position size cap |

---

## Data Flow

```
NSE API ──► nse_provider  (quote + candles)
                │
                ▼
        strategy/pullback.detect()  →  Signal | None
                │
                ▼
        telegram_notifier.send_signal_alert()
                │   user taps ✅ Place Order
                ▼
        broker/kite_broker.place_orders()
                │
                ├──► Zerodha (BUY + SL-M orders)
                └──► trades/trade_log.csv
```
