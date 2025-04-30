from datetime import datetime
import threading
import time
from strategy.dynamic_trend_breakout import DynamicTrendBreakoutStrategy
from strategy.scalping_breakout import ScalpingBreakoutStrategy
from misc.config import *
import tkinter as tk
from trade.force_close import close_orders
from core.scan_pair import scan_pairs
from trade.place_trade import place_trade
from core.shared_state import get_gui_instance
from utilities.utility import clear_cache, display_best_pair, get_latest_trade_from_log, update_trading_table


#region SCHEDULED ROUTINE 

# New routine for checking trade status and logging P&L
def scheduled_routine():    
    
    gui = get_gui_instance()
    
    global latest_pair
    
    with threading.Lock():
        gui.is_running = True
        
    trade_timer_seconds = 0
    
    while gui.is_running:        
        #gui.add_to_console(f"Pending Orders ID: {placed_order_ids}")
        # If no orders remain in the list, check open trades as a fallback
        if len(placed_order_ids) == 0 :
            trade_timer_seconds = 0
            gui.update_trade_timer(trade_timer_seconds)
            open_trades = client.get_open_orders()
            if not open_trades:             
                gui.add_to_console("No open trades found. Resuming routine...")
                gui.quit_button.config(state=tk.ACTIVE)         
                gui.close_button.config(state=tk.DISABLED)       
                gui.stop_trade_graph()
                if not run_bot_logic():
                    gui.is_running = False
                    gui.add_to_console("Error occurred in BOT LOGIC...")
            else:
                for report in open_trades:
                    latest_pair = report['symbol']                    
                                                
                gui.update_trade_details_table(open_trades)   
                # Disable buttons
                gui.close_button.config(state=tk.ACTIVE)
                gui.quit_button.config(state=tk.DISABLED)
                trade_timer_seconds = get_latest_trade_from_log()
        else:
            trade_timer_seconds += 5
            gui.update_trade_timer(trade_timer_seconds)
            update_trading_table()      
        
        if trade_timer_seconds >= config['TRADE_MAX_TIME_RUNNING']:   # Trades been running for 1h
            gui.add_to_console(f"Trades been running for {config['TRADE_MAX_TIME_RUNNING']} minutes, forcing closure")
            close_orders()
            with threading.Lock():
                gui.is_running = True
            gui.schedule_button.config(state=tk.DISABLED)
            
        # Wait for 5 sec before repeating
        time.sleep(5)
                    
        
# endregion 

#region BOT LOGIC
def run_bot_logic():
    gui = get_gui_instance()
    global forced_trade_closure
    global latest_pair
    global trade_counter
    global placed_order_ids
    
    # Initialize strategies
    trend_breakout_strategy = DynamicTrendBreakoutStrategy(config, indicator_cache)
    scalping_strategy = ScalpingBreakoutStrategy(config, skip_trade_logger)
    
    # Clear cache after each minute
    clear_cache()
    
    start_time = time.time()
    tradable_pairs = scan_pairs()

    if tradable_pairs:
        gui.add_to_console("\nTop Bullish Opportunities:")

       # Step 2: Apply Dynamic Trend Breakout Strategy
        try:
            first_screening_results = []
            for pair in tradable_pairs:
                try:
                    result = trend_breakout_strategy.evaluate_pair(pair)  # First screening
                    if result:
                        first_screening_results.append(result)
                except Exception as e:
                    logging.error(f"Error in evaluate_pair_with_score for {pair}: {e}")

            if not first_screening_results:
                gui.add_to_console("No pairs passed the Dynamic Trend Breakout strategy.\n")
                gui.quit_button.config(state=tk.ACTIVE)
                gui.close_button.config(state=tk.DISABLED)
                return True

        except Exception as e:
            logging.error(f"Error applying Dynamic Trend Breakout strategy: {e}")
            return False

        # Step 3: Apply Scalping Breakout Strategy
        try:
            
            best_pair = scalping_strategy.refine_best_pair(first_screening_results)  # Second screening
            
        except Exception as e:
            logging.error(f"Error in refine_best_pair: {e}")
            gui.add_to_console("\nError during refinement. Exiting cycle.\n")
            return False
        
        
        
        # Place the trade for the best pair with score higher then threshold 
        if best_pair:
            display_best_pair(best_pair)
            if latest_pair != best_pair['pair'] :
                trade_successful = place_trade(best_pair)
                if trade_successful:
                    latest_pair = best_pair['pair'] 
                    forced_trade_closure = False
                    trade_counter += 1
                    gui.update_trade_number(trade_counter)
                    # Log trade details directly from best_pair and related subdata
                    detailed_trade_logger.info(
                        f"{trade_counter} | {best_pair['pair']} | {best_pair['trade_suggestion']['entry']} | "
                        f"{best_pair['trade_suggestion']['stop_loss']} | {best_pair['trade_suggestion']['take_profit']} | "
                        f"{best_pair['trade_suggestion']['rr_ratio']} | {best_pair['analysis']['atr']} | "
                        f"{best_pair['analysis']['support']} | {best_pair['analysis']['resistance']} | "
                        f"{best_pair['analysis'].get('trend', 'N/A')} | {best_pair['score']} | "
                        f"{config['MIN_VOLUME']} | {config['EMA_SPANS']} | {config['RSI_BOUNDS']} | "
                        f"{best_pair['analysis']['HGT']} | {config['SCORE_THRESHOLD']} | "
                        f"{config['RR_THRESHOLD']} | {config['ROOM_MULTIPLIER']} | {config['TIMEFRAME']} | "
                        f"{config['TRADE_MAX_TIME_RUNNING']} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                        f"{best_pair['analysis']['bollinger']['bb_upper']} | {best_pair['analysis']['bollinger']['bb_middle']} | "
                        f"{best_pair['analysis']['bollinger']['bb_lower']} | {best_pair['analysis']['adx']} | {best_pair['breakout']} | "
                        f"{best_pair['analysis']['breakout']} | {best_pair['analysis']['breakout_strength']:.2f} | "
                        f"{best_pair['analysis']['breakout_probability']:.2f} | {best_pair['analysis']['momentum_confirmed']} | "
                        f"{best_pair['analysis']['volume_spike']} | {best_pair['analysis']['DMI']}"
                        )
                    # Disable buttons
                    gui.quit_button.config(state=tk.DISABLED)     
                    gui.close_button.config(state=tk.ACTIVE)           
                    #send_email_notification(
                    #    subject="Trade Placed",
                    #    body=f"A trade has been placed for {best_pair['pair']} with a score of {best_pair['score']}.\n"                                
                    #)      
            else:
                gui.add_to_console(f"Repeated pair. The following pair was traded last: {latest_pair}")              
        else:
            gui.add_to_console("\nNo bullish opportunities found.\n")
            gui.quit_button.config(state=tk.ACTIVE)
            gui.close_button.config(state=tk.DISABLED)
    
    end_time = time.time()
    formatted_time = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
    gui.add_to_console(f"\nTime stamp: {formatted_time}")
    gui.add_to_console(f"Script runtime: {end_time - start_time:.2f} seconds")    
    
    
    return True
# endregion 