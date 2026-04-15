"""
Scheduler
=========
Changes vs original:
  • Market regime detection — bot adapts thresholds based on BTC/ETH trend
  • Pair cooldown check    — no re-entry on same pair within cooldown window
  • Null-safe detailed logging (all analysis.get() instead of direct access)
  • Regime displayed in console on every cycle
"""

from datetime import datetime
import logging
import threading
import time

from strategy.dynamic_trend_breakout import DynamicTrendBreakoutStrategy
from strategy.scalping_breakout import ScalpingBreakoutStrategy
from misc.config import *
import tkinter as tk
from trade.force_close import close_orders
from core.scan_pair import scan_pairs
from trade.place_trade import place_trade
from core.shared_state_v2 import (
    get_gui_instance, set_gui_instance,
    set_pair_cooldown, is_pair_in_cooldown, get_cooled_pairs,
)
from core.market_regime import (
    get_regime, apply_regime_to_config, regime_label,
    BEAR_STRONG,
)
from utilities.utility import (
    clear_cache, display_best_pair,
    get_latest_trade_from_log, update_trading_table,
)


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Scheduled Routine
# ╚══════════════════════════════════════════════════════════════════════════════
def scheduled_routine():
    gui = get_gui_instance()

    global latest_pair
    with threading.Lock():
        gui.is_running = True

    trade_timer_seconds = 0

    while gui.is_running:

        if len(placed_order_ids) == 0:
            trade_timer_seconds = 0
            gui.update_trade_timer(trade_timer_seconds)
            open_trades = client.get_open_orders()

            if not open_trades:
                gui.add_to_console("No open trades. Resuming routine…")
                gui.quit_button.config(state=tk.ACTIVE)
                gui.close_button.config(state=tk.DISABLED)
                gui.stop_trade_graph()

                if not run_bot_logic():
                    gui.is_running = False
                    gui.add_to_console("Error in BOT LOGIC — stopping.")
            else:
                for report in open_trades:
                    latest_pair = report['symbol']
                gui.update_trade_details_table(open_trades)
                gui.close_button.config(state=tk.ACTIVE)
                gui.quit_button.config(state=tk.DISABLED)
                trade_timer_seconds = get_latest_trade_from_log()
        else:
            trade_timer_seconds += 5
            gui.update_trade_timer(trade_timer_seconds)
            update_trading_table()

        if trade_timer_seconds >= config['TRADE_MAX_TIME_RUNNING']:
            gui.add_to_console(
                f"Trade timeout ({config['TRADE_MAX_TIME_RUNNING']}s) — forcing closure"
            )
            close_orders()
            with threading.Lock():
                gui.is_running = True
                set_gui_instance(gui)
            gui.schedule_button.config(state=tk.DISABLED)

        time.sleep(5)


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Bot Logic (called on every scan cycle)
# ╚══════════════════════════════════════════════════════════════════════════════
def run_bot_logic() -> bool:
    gui = get_gui_instance()
    global forced_trade_closure, latest_pair, trade_counter, placed_order_ids

    # ── 1. Market regime ───────────────────────────────────────────────────────
    regime         = get_regime()
    label, colour  = regime_label(regime)
    gui.add_to_console(f"\n{'─'*40}")
    gui.add_to_console(f"Market Regime: {label}")

    if regime == BEAR_STRONG:
        gui.add_to_console("⛔  BEAR_STRONG — skipping all trades this cycle.")
        gui.quit_button.config(state=tk.ACTIVE)
        gui.close_button.config(state=tk.DISABLED)
        return True   # not an error, just no action

    # Build regime-adjusted config for this cycle
    live_config = apply_regime_to_config(config, regime)

    # ── 2. Cooldown status ────────────────────────────────────────────────────
    cooled = get_cooled_pairs()
    if cooled:
        for p, mins in cooled.items():
            gui.add_to_console(f"  ⏳ {p} in cooldown ({mins:.0f} min remaining)")

    # ── 3. Initialise strategies with regime-adjusted config ──────────────────
    trend_strategy   = DynamicTrendBreakoutStrategy(live_config, indicator_cache)
    scalping_strategy = ScalpingBreakoutStrategy(live_config, skip_trade_logger)

    clear_cache()
    start_time = time.time()

    # ── 4. Scan pairs ─────────────────────────────────────────────────────────
    tradable_pairs = scan_pairs()

    if not tradable_pairs:
        gui.add_to_console("No bullish opportunities found.")
        gui.quit_button.config(state=tk.ACTIVE)
        gui.close_button.config(state=tk.DISABLED)
        return True

    # Filter out pairs still in cooldown
    tradable_pairs = [p for p in tradable_pairs if not is_pair_in_cooldown(p)]
    if not tradable_pairs:
        gui.add_to_console("All candidate pairs are in cooldown. Waiting…")
        gui.quit_button.config(state=tk.ACTIVE)
        gui.close_button.config(state=tk.DISABLED)
        return True

    gui.add_to_console(f"\n🔍 {len(tradable_pairs)} pairs after cooldown filter")

    # ── 5. First screening ────────────────────────────────────────────────────
    try:
        first_results = []
        for pair in tradable_pairs:
            try:
                result = trend_strategy.evaluate_pair(pair)
                if result:
                    first_results.append(result)
            except Exception as e:
                logging.error(f"evaluate_pair error for {pair}: {e}")
    except Exception as e:
        logging.error(f"First screening error: {e}")
        return False

    if not first_results:
        gui.add_to_console("No pairs passed first screening.")
        gui.quit_button.config(state=tk.ACTIVE)
        gui.close_button.config(state=tk.DISABLED)
        return True

    gui.add_to_console(f"  ✓ {len(first_results)} pairs passed first screen")

    # ── 6. Second screening ───────────────────────────────────────────────────
    try:
        best_pair = scalping_strategy.refine_best_pair(first_results)
    except Exception as e:
        logging.error(f"refine_best_pair error: {e}")
        gui.add_to_console("Error during refinement.")
        return False

    # ── 7. Place trade ────────────────────────────────────────────────────────
    if best_pair:
        display_best_pair(best_pair)

        if is_pair_in_cooldown(best_pair['pair']):
            gui.add_to_console(f"Best pair {best_pair['pair']} just entered cooldown — skipping.")
            return True

        if latest_pair != best_pair['pair']:
            trade_successful = place_trade(best_pair)
            if trade_successful:
                latest_pair = best_pair['pair']
                forced_trade_closure = False
                trade_counter += 1
                gui.update_trade_number(trade_counter)

                # ── Set cooldown on traded pair ──────────────────────────────
                set_pair_cooldown(best_pair['pair'])

                # ── Null-safe detailed logging ───────────────────────────────
                a = best_pair.get('analysis', {})
                b = a.get('bollinger', {})
                detailed_trade_logger.info(
                    f"{trade_counter} | {best_pair['pair']} | "
                    f"{best_pair['trade_suggestion']['entry']} | "
                    f"{best_pair['trade_suggestion']['stop_loss']} | "
                    f"{best_pair['trade_suggestion']['take_profit']} | "
                    f"{best_pair['trade_suggestion']['rr_ratio']} | "
                    f"{a.get('atr','N/A')} | {a.get('support','N/A')} | "
                    f"{a.get('resistance','N/A')} | {a.get('trend','N/A')} | "
                    f"{best_pair['score']} | {config['MIN_VOLUME']} | "
                    f"{config['EMA_SPANS']} | {config['RSI_BOUNDS']} | "
                    f"{a.get('HGT','N/A')} | {config['SCORE_THRESHOLD']} | "
                    f"{config['RR_THRESHOLD']} | {config['ROOM_MULTIPLIER']} | "
                    f"{config['TIMEFRAME']} | {config['TRADE_MAX_TIME_RUNNING']} | "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"{b.get('bb_upper','N/A')} | {b.get('bb_middle','N/A')} | "
                    f"{b.get('bb_lower','N/A')} | {a.get('adx','N/A')} | "
                    f"{best_pair.get('breakout','N/A')} | "
                    f"{a.get('breakout','N/A')} | "
                    f"{a.get('breakout_strength','N/A')} | "
                    f"{a.get('breakout_probability','N/A')} | "
                    f"{a.get('momentum_confirmed','N/A')} | "
                    f"{a.get('volume_spike','N/A')} | "
                    f"{a.get('DMI','N/A')} | "
                    f"REGIME:{regime}"
                )

                gui.quit_button.config(state=tk.DISABLED)
                gui.close_button.config(state=tk.ACTIVE)
        else:
            gui.add_to_console(f"Repeated pair ({latest_pair}) — skipping.")
    else:
        gui.add_to_console("No pair passed second screening.")
        gui.quit_button.config(state=tk.ACTIVE)
        gui.close_button.config(state=tk.DISABLED)

    elapsed = time.time() - start_time
    gui.add_to_console(f"Cycle runtime: {elapsed:.2f}s  |  Regime: {label}")
    return True
