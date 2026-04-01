"""
notifications/telegram_notifier.py
────────────────────────────────────
Everything Telegram: plain messages, signal alerts (with inline buttons),
and the button-callback handler that triggers order placement.

Two concerns are intentionally separated:
  • Outbound messages  — raw HTTP POST (thread-safe, works from any thread)
  • Button callbacks   — python-telegram-bot async Application (bot thread)

Usage:
  init(kite, session_state)   ← call once in main.py before run_bot_thread()
  run_bot_thread()            ← starts background daemon thread
  send_message(text)          ← plain Markdown message
  send_signal_alert(signal, state)  ← alert with Place/Skip buttons
"""

import time
import asyncio
import threading
import requests
from typing import Optional
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from core.signal     import Signal
from core.prescan    import build_prescan_result
from core.session    import SessionState
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, scanner_cfg, execution_cfg, strategy_cfg
from data import nse_provider as nse
from utils.logger    import get_logger

log = get_logger(__name__)

# Injected by init() — used inside the async button handler
_kite          = None
_session_state: Optional[SessionState] = None


def mode_name() -> str:
    """Return the current execution mode label."""
    return "PAPER" if execution_cfg.paper_trading else "LIVE"


# ─────────────────────────────────────────────────────────────────────────────
#  Dependency injection
# ─────────────────────────────────────────────────────────────────────────────

def init(kite, session_state: SessionState) -> None:
    """
    Inject the Kite handle and session state so the button handler
    can place orders and update trade counters.
    Call once in main.py before run_bot_thread().
    """
    global _kite, _session_state
    _kite          = kite
    _session_state = session_state


# ─────────────────────────────────────────────────────────────────────────────
#  Outbound helpers  (thread-safe raw HTTP — no async needed)
# ─────────────────────────────────────────────────────────────────────────────

def send_message(text: str) -> None:
    """Send a plain Markdown message to the configured chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        log.error(f"Telegram send_message error: {exc}")


def send_signal_alert(
    signal: Signal,
    session_state: SessionState,
    title: str = "PULLBACK SIGNAL DETECTED",
) -> None:
    """
    Send a formatted signal card with ✅ Place Order / ❌ Skip buttons.
    Stores the signal in session_state.pending_signals under a unique key
    so the button handler can retrieve it asynchronously.
    """
    callback_key = f"order_{signal.symbol}_{int(time.time())}"
    session_state.add_pending(callback_key, signal)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    action_label = "✅ Sell" if getattr(signal, "direction", "LONG") == "SHORT" else "✅ Buy"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       _fmt_signal(signal, title=title),
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": action_label, "callback_data": f"confirm_{callback_key}"},
                {"text": "❌ Skip",        "callback_data": f"skip_{callback_key}"},
            ]]
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"📲 Alert sent for {signal.symbol}")
        else:
            log.error(f"Alert send failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        log.error(f"send_signal_alert error: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  Message formatters
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_signal(s: Signal, title: str = "PULLBACK SIGNAL DETECTED") -> str:
    side = getattr(s, "direction", "LONG")
    mode_label = f"⚙️  Mode       : {mode_name()}\n"
    strategy_line = (
        f"🧠  Strategy   : {', '.join(s.strategy_names)}\n"
        if s.strategy_names else ""
    )
    return (
        f"📊 *{title}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{mode_label}"
        f"{strategy_line}"
        f"🏷  Stock      : `{s.symbol}`\n"
        f"↕️  Side       : {side}\n"
        f"💰  Entry      : ₹{s.entry}\n"
        f"🛡  Stop Loss  : ₹{s.stop_loss}\n"
        f"🎯  Target     : ₹{s.target}\n"
        f"📦  Quantity   : {s.quantity} shares\n"
        f"💵  Capital    : ₹{s.capital}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈  Gap Up     : {s.gap_pct}%\n"
        f"📊  VWAP       : ₹{s.vwap}\n"
        f"📉  RSI        : {s.rsi}\n"
        f"🔊  Vol Ratio  : {s.vol_ratio}×\n"
        f"⚖️  R:R        : 1:{s.rr_ratio()}\n"
        f"⏰  Time       : {datetime.now().strftime('%H:%M:%S')}\n"
    )


def _fmt_order_placed(s: Signal, buy_id: str, sl_id: str, trade_count: int) -> str:
    title = "🧪 *PAPER ORDER PLACED*" if execution_cfg.paper_trading else "✅ *ORDER PLACED*"
    mode_line = f"⚙️  Mode      : {mode_name()}\n"
    side_line = f"↕️  Side      : {getattr(s, 'direction', 'LONG')}\n"
    strategy_line = (
        f"🧠  Strategy  : {', '.join(s.strategy_names)}\n"
        if s.strategy_names else ""
    )
    return (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{mode_line}"
        f"{side_line}"
        f"{strategy_line}"
        f"🏷  Stock     : `{s.symbol}`\n"
        f"💰  Entry     : ₹{s.entry}\n"
        f"🛡  Stop Loss : ₹{s.stop_loss}\n"
        f"🎯  Target    : ₹{s.target}\n"
        f"📦  Qty       : {s.quantity}\n"
        f"🆔  Entry ID  : `{buy_id}`\n"
        f"🆔  SL ID     : `{sl_id}`\n"
        f"📊  Trades    : {trade_count}/{scanner_cfg.max_trades_per_day}"
    )


def _build_test_signal() -> Signal:
    """Return a sample signal so the Telegram flow can be tested on demand."""
    entry = 250.0
    stop_loss = 245.0
    quantity = 80
    return Signal(
        symbol="TESTNSE",
        entry=entry,
        stop_loss=stop_loss,
        target=260.0,
        quantity=quantity,
        capital=round(entry * quantity, 2),
        gap_pct=3.2,
        vwap=248.7,
        rsi=50.4,
        vol_ratio=0.62,
    )


def _build_manual_signal(symbol: str, quantity: int) -> Optional[Signal]:
    """Build a manual order signal from the latest market price."""
    quote = nse.get_quote(symbol)
    if not quote or quote["ltp"] <= 0:
        return None

    entry = quote["ltp"]
    stop_loss = round(entry * (1 - strategy_cfg.stop_loss_pct / 100), 2)
    risk = entry - stop_loss
    target = round(entry + risk * strategy_cfg.reward_ratio, 2)

    prev_close = quote.get("prev_close", 0)
    open_price = quote.get("open", 0)
    gap_pct = round(((open_price - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=quantity,
        capital=round(entry * quantity, 2),
        gap_pct=gap_pct,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Command handlers
# ─────────────────────────────────────────────────────────────────────────────

async def _help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    text = (
        "🤖 *Pullback Trader Bot — Help*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "This bot scans stocks every 5 mins and alerts you when one of the active strategies finds a setup.\n\n"
        "*How it works:*\n"
        "1️⃣  Bot detects signal → sends you an alert\n"
        "2️⃣  You tap ✅ *Place Order* → order placed on Zerodha instantly\n"
        "3️⃣  Or tap ❌ *Skip* → ignored, bot keeps scanning\n\n"
        "*Signal conditions checked:*\n"
        "📈 Gap up ≥ 2.5% from previous close\n"
        "📊 Price pulled back near VWAP\n"
        "🟢 Last candle is green (bounce)\n"
        "📉 RSI between 42–58\n"
        "🔇 Low volume on the dip\n\n"
        "*Commands:*\n"
        "/help — show this message\n"
        "/add SYMBOL QTY — create a manual order card\n"
        "/mode — show current paper/live mode\n"
        "/prescan — send the MIS pre-scan summary now\n"
        "/status — today's trade summary\n"
        "/testsignal — send a sample signal now\n"
        "/stop — stop scanning for new signals"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def _status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not _session_state:
        await update.message.reply_text("⚠️ Bot not fully initialised yet.")
        return
    stocks = ", ".join(_session_state.traded_symbols) if _session_state.traded_symbols else "None"
    text = (
        f"📊 *Today's Status*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Trades done  : {_session_state.trade_count}/{scanner_cfg.max_trades_per_day}\n"
        f"🏷  Stocks traded: {stocks}\n"
        f"⏰ Time         : {datetime.now().strftime('%H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def _mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mode command."""
    paper_note = (
        "\n🧪 Orders are simulated. No live Zerodha orders will be sent."
        if execution_cfg.paper_trading
        else "\n⚠️ Live Zerodha orders will be placed on confirmation."
    )
    await update.message.reply_text(
        f"⚙️ *Current Mode*: `{mode_name()}`{paper_note}",
        parse_mode="Markdown"
    )


async def _prescan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /prescan command — sends the informational MIS pre-scan summary."""
    if not _session_state:
        await update.message.reply_text("⚠️ Bot not fully initialised yet.")
        return

    send_message(build_prescan_result().summary)
    await update.message.reply_text(
        "📡 *Pre-scan sent.*\nCheck the latest summary above.",
        parse_mode="Markdown",
    )
    log.info("📡 Pre-scan sent via /prescan command.")


async def _stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command — sets trade count to max so scanner exits cleanly."""
    if not _session_state:
        await update.message.reply_text("⚠️ Bot not fully initialised yet.")
        return
    _session_state.trade_count = scanner_cfg.max_trades_per_day
    await update.message.reply_text(
        "⏹ *Scanning stopped.*\nNo more signals will be sent today.",
        parse_mode="Markdown"
    )
    log.info("⏹ Bot stopped via /stop command from Telegram.")


async def _testsignal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /testsignal command — sends a sample signal with action buttons."""
    if not _session_state:
        await update.message.reply_text("⚠️ Bot not fully initialised yet.")
        return

    signal = _build_test_signal()
    send_signal_alert(signal, _session_state)
    await update.message.reply_text(
        "🧪 *Test signal sent.*\nCheck the latest alert above and tap the button flow.",
        parse_mode="Markdown"
    )
    log.info("🧪 Test signal sent via /testsignal command.")


async def _add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add SYMBOL QTY for manual order testing."""
    if not _session_state:
        await update.message.reply_text("⚠️ Bot not fully initialised yet.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: `/add SYMBOL QTY`\nExample: `/add NATIONALUM 1`",
            parse_mode="Markdown",
        )
        return

    symbol = context.args[0].strip().upper()
    try:
        quantity = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ Quantity must be a whole number.")
        return

    if quantity <= 0:
        await update.message.reply_text("⚠️ Quantity must be greater than 0.")
        return

    signal = _build_manual_signal(symbol, quantity)
    if not signal:
        await update.message.reply_text(
            f"⚠️ Could not fetch a live quote for `{symbol}`. Check the symbol and try again.",
            parse_mode="Markdown",
        )
        return

    send_signal_alert(signal, _session_state, title="MANUAL ORDER REQUEST")
    live_note = (
        "⚠️ Confirming this will place a real Zerodha order."
        if not execution_cfg.paper_trading
        else "🧪 Confirming this will simulate the order."
    )
    await update.message.reply_text(
        f"✅ Manual order card created for `{symbol}` qty `{quantity}`.\n{live_note}",
        parse_mode="Markdown",
    )
    log.info(f"📝 Manual order card sent for {symbol} qty={quantity}.")


# ─────────────────────────────────────────────────────────────────────────────
#  Button callback handler  (async — runs in the bot thread)
# ─────────────────────────────────────────────────────────────────────────────

async def _button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ✅ Place Order and ❌ Skip taps from Telegram."""
    from broker import kite_broker  # late import avoids circular dependency

    query = update.callback_query
    await query.answer()
    data  = query.data  # e.g. "confirm_order_ONGC_1712345678"

    if data.startswith("confirm_"):
        callback_key = data[len("confirm_"):]
        signal       = _session_state.pop_pending(callback_key)

        if not signal:
            await query.edit_message_text("⚠️ Signal expired or already processed.")
            return

        if _session_state.already_traded(signal.symbol):
            await query.edit_message_text(f"⚠️ {signal.symbol} already traded today.")
            return

        if _session_state.trade_count >= scanner_cfg.max_trades_per_day:
            await query.edit_message_text(
                f"⚠️ Max {scanner_cfg.max_trades_per_day} trades already done today."
            )
            return

        side = getattr(signal, "direction", "LONG")
        await query.edit_message_text(
            (
                f"⏳ Simulating {side} order for *{signal.symbol}*..."
                if execution_cfg.paper_trading
                else f"⏳ Placing {side} order for *{signal.symbol}*..."
            ),
            parse_mode="Markdown"
        )

        buy_id, sl_id = kite_broker.place_orders(_kite, signal)

        if buy_id:
            _session_state.register_trade(signal)
            msg = _fmt_order_placed(signal, buy_id, sl_id, _session_state.trade_count)
        else:
            label = "Paper order" if execution_cfg.paper_trading else "Order"
            msg = f"❌ *{label} FAILED* for {signal.symbol}. Check logs."

        await query.edit_message_text(msg, parse_mode="Markdown")

    elif data.startswith("skip_"):
        callback_key = data[len("skip_"):]
        signal       = _session_state.pop_pending(callback_key)
        symbol       = signal.symbol if signal else "Unknown"
        await query.edit_message_text(f"❌ *Skipped* `{symbol}`", parse_mode="Markdown")
        log.info(f"⏭  Signal skipped: {symbol}")


# ─────────────────────────────────────────────────────────────────────────────
#  Bot thread
# ─────────────────────────────────────────────────────────────────────────────

def run_bot_thread() -> threading.Thread:
    """
    Launch the Telegram bot listener in a background daemon thread.
    Uses low-level initialize/start/start_polling instead of run_polling()
    so it works correctly from a non-main thread (Python 3.9 compatible).
    Returns the thread handle (main.py can monitor it if needed).
    """
    async def _run_async():
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CallbackQueryHandler(_button_handler))
        app.add_handler(CommandHandler("add", _add_handler))
        app.add_handler(CommandHandler("help",   _help_handler))
        app.add_handler(CommandHandler("mode",   _mode_handler))
        app.add_handler(CommandHandler("prescan", _prescan_handler))
        app.add_handler(CommandHandler("status", _status_handler))
        app.add_handler(CommandHandler("testsignal", _testsignal_handler))
        app.add_handler(CommandHandler("stop",   _stop_handler))
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        log.info("🤖 Telegram bot started — listening for button clicks and commands…")
        # Keep the coroutine alive until the thread is killed
        await asyncio.Event().wait()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_async())

    thread = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    thread.start()
    return thread
