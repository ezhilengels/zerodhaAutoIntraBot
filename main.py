"""
main.py
────────
Entry point. Boots all subsystems in order, then runs the blocking scan loop.

Run:
  python main.py

  # macOS — keep the machine awake during market hours:
  caffeinate -i python main.py
"""

import time
from typing import Dict, Optional

from utils.logger             import get_logger
from config.settings          import WATCHLIST, scanner_cfg, execution_cfg, prescan_cfg, strategy_cfg, STRATEGY_MODE, STRATEGY_MODES
from config.v2.short_intraday import short_intraday_v2_cfg
from config.v4.short_intraday import short_intraday_v4_cfg
from core.signal              import Signal
from core.prescan             import build_prescan_result, build_short_prescan_result
from core.session             import SessionState
from data                     import nse_provider as nse
from strategy                 import ema_crossover, orb, pivot_breakout, pullback, vwap_reclaim, vwap_rsi
from strategy.v1             import short_intraday as short_intraday_v1
from strategy.v2             import pivot_breakout as pivot_breakout_v2
from strategy.v2             import short_intraday as short_intraday_v2
from strategy.v2             import vwap_reclaim as vwap_reclaim_v2
from strategy.v2             import vwap_rsi as vwap_rsi_v2
from strategy.v3             import short_intraday as short_intraday_v3
from strategy.v4             import short_intraday as short_intraday_v4
from strategy.v6             import short_intraday_v6
from strategy.v3             import vwap_rsi as vwap_rsi_v3
from strategy.v4             import vwap_rsi_bot as vwap_rsi_v4
from broker                   import kite_broker
from notifications             import telegram_notifier as telegram
from utils.time_helpers        import current_hhmm, is_trading_time, is_past_end_time

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Boot helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(state: SessionState) -> None:
    log.info("=" * 57)
    log.info("  PULLBACK TRADER + TELEGRAM BOT — STARTING")
    log.info(f"  Mode         : {'PAPER' if execution_cfg.paper_trading else 'LIVE'}")
    active_strategies = ", ".join(STRATEGY_MODES) if STRATEGY_MODES else STRATEGY_MODE
    log.info(f"  Strategy     : {active_strategies}")
    log.info(f"  Watchlist    : {WATCHLIST}")
    log.info(f"  Max trades   : {scanner_cfg.max_trades_per_day}")
    log.info(f"  Trade window : {scanner_cfg.trade_start_time} – {scanner_cfg.trade_end_time}")
    log.info("=" * 57)


def _load_prev_closes(state: SessionState) -> None:
    """Cache previous-close prices so gap-up % can be computed later."""
    log.info("Fetching previous-close prices…")
    for symbol in WATCHLIST:
        quote = nse.get_quote(symbol)
        if quote:
            state.prev_close_map[symbol] = quote["prev_close"]
            log.info(f"  {symbol}: ₹{state.prev_close_map[symbol]}")


# ─────────────────────────────────────────────────────────────────────────────
#  Scan loop
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_scan_summary(results: Dict[str, bool]) -> str:
    lines = [f"{symbol}: {'TRUE' if matched else 'FALSE'}" for symbol, matched in results.items()]
    return "📋 *Scan Summary*\n" + "\n".join(lines)


def _fmt_named_scan_summary(title: str, results: Dict[str, bool]) -> str:
    lines = [f"{symbol}: {'TRUE' if matched else 'FALSE'}" for symbol, matched in results.items()]
    return f"📋 *{title}*\n" + "\n".join(lines)


def _active_strategy_modes() -> list[str]:
    return STRATEGY_MODES if STRATEGY_MODES else [STRATEGY_MODE]


def _is_short_strategy_mode(mode: str) -> bool:
    return mode in {
        "short_intraday_v1",
        "short_intraday_v2",
        "short_intraday_v3",
        "short_intraday_v4",
        "short_intraday_v6",
        "ath_reversal_v5",
    }


def _scan_symbols_for_mode(state: SessionState, mode: str) -> list[str]:
    if prescan_cfg.enabled and prescan_cfg.shortlist_only:
        if _is_short_strategy_mode(mode):
            return [symbol for symbol in WATCHLIST if symbol in state.short_prescan_candidates]
        return [symbol for symbol in WATCHLIST if symbol in state.prescan_candidates]
    return list(WATCHLIST)


def _run_strategy_scan(mode: str, scan_symbols: list[str], state: SessionState) -> Dict[str, bool]:
    results = {symbol: False for symbol in scan_symbols}
    mode_scan_symbols = _scan_symbols_for_mode(state, mode)
    candidates = [symbol for symbol in mode_scan_symbols if not state.already_traded(symbol)]

    if mode == "vwap_rsi_v4":
        log.info(f"📌 VWAP+RSI v4 candidates: {len(candidates)} / {len(mode_scan_symbols)}")
        for symbol in candidates:
            signal = vwap_rsi_v4.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi_v4"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state, title="VWAP_RSI_V4 SIGNAL")
                time.sleep(2)
        return results

    if mode == "short_intraday_v4":
        log.info(f"📌 short_intraday_v4 candidates: {len(candidates)} / {len(mode_scan_symbols)}")
        found: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v4.detect(symbol, state)
            if signal:
                found.append(signal)

        ranked = sorted(found, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked[: short_intraday_v4_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v4"]
            results[signal.symbol] = True
            telegram.send_signal_alert(signal, state, title="SHORT_INTRADAY_V4 SIGNAL")
            time.sleep(2)

        if len(ranked) > short_intraday_v4_cfg.max_ranked_signals:
            log.info(
                f"✂️ short_intraday_v4 ranked {len(ranked)} signals, "
                f"alerted top {short_intraday_v4_cfg.max_ranked_signals}"
            )
        return results

    return results


def _gap_up_quote(symbol: str, state: SessionState) -> Optional[dict]:
    """Return the live quote only when the stock still qualifies as a gap-up name."""
    if state.already_traded(symbol):
        return None
    if symbol not in state.prev_close_map:
        return None

    quote = nse.get_quote(symbol)
    if not quote or quote["ltp"] <= 0:
        return None

    prev_close = state.prev_close_map.get(symbol, quote["prev_close"])
    open_price = quote["open"]
    if prev_close <= 0 or open_price <= 0:
        return None

    gap_pct = ((open_price - prev_close) / prev_close) * 100
    return quote if gap_pct >= strategy_cfg.gap_up_threshold else None


def _run_prescan(state: SessionState) -> None:
    """Send a once-per-session pre-scan summary and cache the shortlist candidates."""
    if not prescan_cfg.enabled or state.prescan_sent:
        return

    prescan = build_prescan_result()
    state.prescan_candidates = set(prescan.candidates)
    telegram.send_message(prescan.summary)
    if any(_is_short_strategy_mode(mode) for mode in _active_strategy_modes()):
        short_prescan = build_short_prescan_result()
        state.short_prescan_candidates = set(short_prescan.candidates)
        telegram.send_message(short_prescan.summary)
    state.prescan_sent = True
    log.info(
        f"📡 Pre-scan summary sent. Long candidates: {sorted(state.prescan_candidates)} | "
        f"Short candidates: {sorted(state.short_prescan_candidates)}"
    )


def _scan_symbols(state: SessionState) -> list[str]:
    """Return the symbols to scan this cycle, optionally restricted by pre-scan shortlist."""
    if prescan_cfg.enabled and prescan_cfg.shortlist_only:
        return [symbol for symbol in WATCHLIST if symbol in state.prescan_candidates]
    return list(WATCHLIST)


def _scan_once(state: SessionState) -> None:
    """One full pass: check every watchlist symbol and alert on signals."""
    scan_symbols = _scan_symbols(state)
    log.info(f"🔍 Scanning {len(scan_symbols)} stocks…")
    results = {symbol: False for symbol in scan_symbols}

    active_modes = _active_strategy_modes()
    if len(active_modes) > 1:
        titled_results: list[tuple[str, Dict[str, bool]]] = []
        for mode in active_modes:
            mode_results = _run_strategy_scan(mode, scan_symbols, state)
            titled_results.append((mode, mode_results))
            for symbol, matched in mode_results.items():
                results[symbol] = results[symbol] or matched

        if execution_cfg.show_scan_results:
            for mode, mode_results in titled_results:
                telegram.send_message(_fmt_named_scan_summary(f"{mode.upper()} Scan Summary", mode_results))
        return

    effective_mode = active_modes[0]
    scan_symbols = _scan_symbols_for_mode(state, effective_mode)
    results = {symbol: False for symbol in scan_symbols}

    if effective_mode == "dual_v4":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 Dual v4 candidates: {len(candidates)} / {len(scan_symbols)}")

        long_results = {symbol: False for symbol in scan_symbols}
        short_results = {symbol: False for symbol in scan_symbols}

        for symbol in candidates:
            signal = vwap_rsi_v4.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi_v4"]
                results[symbol] = True
                long_results[symbol] = True
                telegram.send_signal_alert(signal, state, title="VWAP_RSI_V4 SIGNAL")
                time.sleep(2)

        found_short: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v4.detect(symbol, state)
            if signal:
                found_short.append(signal)

        ranked_short = sorted(found_short, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked_short[: short_intraday_v4_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v4"]
            results[signal.symbol] = True
            short_results[signal.symbol] = True
            telegram.send_signal_alert(signal, state, title="SHORT_INTRADAY_V4 SIGNAL")
            time.sleep(2)

        if len(ranked_short) > short_intraday_v4_cfg.max_ranked_signals:
            log.info(
                f"✂️ short_intraday_v4 ranked {len(ranked_short)} signals, "
                f"alerted top {short_intraday_v4_cfg.max_ranked_signals}"
            )

        if execution_cfg.show_scan_results:
            telegram.send_message(_fmt_named_scan_summary("VWAP_RSI_V4 Scan Summary", long_results))
            telegram.send_message(_fmt_named_scan_summary("SHORT_INTRADAY_V4 Scan Summary", short_results))
        return
    elif effective_mode == "multi":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 Multi-strategy candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            triggered: list[tuple[str, Signal]] = []

            orb_signal = orb.detect(symbol, state)
            if orb_signal:
                triggered.append(("orb", orb_signal))

            vwap_rsi_signal = vwap_rsi.detect(symbol, state)
            if vwap_rsi_signal:
                triggered.append(("vwap_rsi", vwap_rsi_signal))

            ema_signal = ema_crossover.detect(symbol, state)
            if ema_signal:
                triggered.append(("ema_crossover", ema_signal))

            if len(triggered) < execution_cfg.min_strategy_confirmations:
                continue

            primary_signal = triggered[0][1]
            primary_signal.strategy_names = [name for name, _ in triggered]
            results[symbol] = True
            telegram.send_signal_alert(primary_signal, state, title="MULTI-STRATEGY SIGNAL")
            time.sleep(2)
    elif effective_mode == "orb":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 ORB candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = orb.detect(symbol, state)
            if signal:
                signal.strategy_names = ["orb"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_reclaim":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP reclaim candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_reclaim.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_reclaim"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_reclaim_v2":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP reclaim v2 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_reclaim_v2.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_reclaim_v2"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_rsi":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP+RSI candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_rsi.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_rsi_v2":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP+RSI v2 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_rsi_v2.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi_v2"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_rsi_v3":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP+RSI v3 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_rsi_v3.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi_v3"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "vwap_rsi_v4":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 VWAP+RSI v4 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = vwap_rsi_v4.detect(symbol, state)
            if signal:
                signal.strategy_names = ["vwap_rsi_v4"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "pivot_breakout":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 Pivot breakout candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = pivot_breakout.detect(symbol, state)
            if signal:
                signal.strategy_names = ["pivot_breakout"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "pivot_breakout_v2":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 Pivot breakout v2 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = pivot_breakout_v2.detect(symbol, state)
            if signal:
                signal.strategy_names = ["pivot_breakout_v2"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "ema_crossover":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 EMA crossover candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = ema_crossover.detect(symbol, state)
            if signal:
                signal.strategy_names = ["ema_crossover"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "short_intraday_v1":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 short_intraday_v1 candidates: {len(candidates)} / {len(scan_symbols)}")

        for symbol in candidates:
            signal = short_intraday_v1.detect(symbol, state)
            if signal:
                signal.strategy_names = ["short_intraday_v1"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)
    elif effective_mode == "short_intraday_v2":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 short_intraday_v2 candidates: {len(candidates)} / {len(scan_symbols)}")
        found: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v2.detect(symbol, state)
            if signal:
                found.append(signal)

        ranked = sorted(found, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked[: short_intraday_v2_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v2"]
            results[signal.symbol] = True
            telegram.send_signal_alert(signal, state)
            time.sleep(2)

        if len(ranked) > short_intraday_v2_cfg.max_ranked_signals:
            log.info(
                f"✂️ short_intraday_v2 ranked {len(ranked)} signals, "
                f"alerted top {short_intraday_v2_cfg.max_ranked_signals}"
            )
    elif effective_mode == "short_intraday_v3":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 short_intraday_v3 candidates: {len(candidates)} / {len(scan_symbols)}")
        found: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v3.detect(symbol, state)
            if signal:
                found.append(signal)

        ranked = sorted(found, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked[: short_intraday_v2_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v3"]
            results[signal.symbol] = True
            telegram.send_signal_alert(signal, state)
            time.sleep(2)
    elif effective_mode == "short_intraday_v4":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 short_intraday_v4 candidates: {len(candidates)} / {len(scan_symbols)}")
        found: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v4.detect(symbol, state)
            if signal:
                found.append(signal)

        ranked = sorted(found, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked[: short_intraday_v4_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v4"]
            results[signal.symbol] = True
            telegram.send_signal_alert(signal, state)
            time.sleep(2)
    elif effective_mode == "short_intraday_v6":
        candidates = [symbol for symbol in scan_symbols if not state.already_traded(symbol)]
        log.info(f"📌 short_intraday_v6 candidates: {len(candidates)} / {len(scan_symbols)}")
        found: list[Signal] = []
        for symbol in candidates:
            signal = short_intraday_v6.detect(symbol, state)
            if signal:
                found.append(signal)

        ranked = sorted(found, key=lambda sig: getattr(sig, "ema_dist", 0.0), reverse=True)
        for signal in ranked[: short_intraday_v4_cfg.max_ranked_signals]:
            signal.strategy_names = ["short_intraday_v6"]
            results[signal.symbol] = True
            telegram.send_signal_alert(signal, state)
            time.sleep(2)
    else:
        shortlisted_quotes: dict[str, dict] = {}

        for symbol in scan_symbols:
            quote = _gap_up_quote(symbol, state)
            if quote:
                shortlisted_quotes[symbol] = quote

        log.info(f"📌 Gap-up shortlist: {len(shortlisted_quotes)} / {len(scan_symbols)}")

        for symbol, quote in shortlisted_quotes.items():
            signal = pullback.detect(symbol, state, quote=quote)
            if signal:
                signal.strategy_names = ["pullback"]
                results[symbol] = True
                telegram.send_signal_alert(signal, state)
                time.sleep(2)   # Brief gap between consecutive alerts

    if execution_cfg.show_scan_results:
        telegram.send_message(_fmt_scan_summary(results))


def _run_scan_loop(state: SessionState, kite) -> None:
    """Main loop — runs until end-of-day or max-trades limit is hit."""
    log.info(f"⏳ Waiting for trade window: {scanner_cfg.trade_start_time}…")
    max_trades_announced = False

    while True:
        if (
            execution_cfg.order_product == "MIS" and
            current_hhmm() >= scanner_cfg.mis_squareoff_time and
            state.live_signals
        ):
            exited = kite_broker.square_off_live_mis_positions(kite, state)
            if exited:
                telegram.send_message(
                    f"⏰ *MIS Square-Off Triggered*\nClosed: {', '.join(exited)}"
                )

        if is_past_end_time():
            log.info("⏹ Trade window ended.")
            telegram.send_message(f"⏹ *Session Complete*\n{state.summary()}")
            break

        if state.trade_count >= scanner_cfg.max_trades_per_day:
            if not max_trades_announced:
                log.info("⏸ Max trades reached — stopping new entries, keeping MIS safeguards active.")
                telegram.send_message(
                    f"✅ Max {scanner_cfg.max_trades_per_day} trades done.\n"
                    f"No new entries will be taken today.\n{state.summary()}"
                )
                max_trades_announced = True
            time.sleep(30)
            continue

        if not is_trading_time():
            time.sleep(30)
            continue

        _run_prescan(state)
        _scan_once(state)
        log.info(f"💤 Next scan in {scanner_cfg.scan_interval_secs // 60} min…\n")
        time.sleep(scanner_cfg.scan_interval_secs)

    log.info("🏁 Bot stopped.")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    state = SessionState()
    _print_banner(state)

    # 1. Authenticate with Zerodha
    kite = kite_broker.create_kite_session()

    # 2. Prime NSE session cookie + cache prev closes
    nse.init_session()
    _load_prev_closes(state)

    # 3. Wire up the notifier with kite + state, then start the bot thread
    telegram.init(kite, state)
    telegram.run_bot_thread()
    time.sleep(2)   # Give the bot thread a moment to initialise

    # 4. Startup notification
    telegram.send_message(
        f"🚀 *Pullback Trader Started*\n"
        f"Mode      : {telegram.mode_name()}\n"
        f"Strategy  : {STRATEGY_MODE}\n"
        f"Watchlist : {', '.join(WATCHLIST)}\n"
        f"Window    : {scanner_cfg.trade_start_time} – {scanner_cfg.trade_end_time}\n"
        f"Max trades: {scanner_cfg.max_trades_per_day}"
    )

    # 5. Blocking scan loop
    _run_scan_loop(state, kite)


if __name__ == "__main__":
    main()
