from datetime import datetime
from email.mime.text import MIMEText
import json
import smtplib
import time
from utilities.binance_call import close_market_trade
from misc.config import *
import os

from core.shared_state import get_gui_instance


def get_dynamic_histogram_threshold(atr):
    """Calculate MACD histogram growth threshold based on ATR and market volatility."""
    if atr < 0.01:
        return 0.00005  # Low volatility: small threshold
    elif atr < 0.05:
        return 0.00015  # Moderate volatility
    else:
        return 0.0003  # High volatility: stricter threshold


def clear_cache():
    global indicator_cache
    with indicator_cache_lock:
        indicator_cache = {}
    logging.info("Indicator cache cleared.")


# Save blacklist to file
def save_blacklist(filename='blacklist.json'):
    global blacklist
    global script_dir
    filename = os.path.join(script_dir, "misc", filename)
    with open(filename, "w") as file:
        json.dump(blacklist, file, indent=4)  # Optional: `indent` for better readability


def save_trade_counter(counter, filename="trade_counter.json"):
    global script_dir
    filename = os.path.join(script_dir, "misc", filename)
    with open(filename, "w") as file:
        json.dump({"trade_count": counter}, file)

        

def send_email_notification(subject, body):
    """
    Send an email notification via Gmail.
    Args:
        subject (str): Subject of the email.
        body (str): Body of the email.
        recipient (str): Recipient's email address.
    """
    sender_email = os.environ.get("GMAIL_SENDER", "")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    smtp_server = "smtp.gmail.com"
    smtp_port = 587

    if not sender_email or not sender_password:
        logging.warning("Email notification skipped: GMAIL_SENDER / GMAIL_APP_PASSWORD not set in .env")
        return

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = sender_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, sender_email, msg.as_string())
        #logging.debug(f"Notification sent to {sender_email}")
    except Exception as e:
        logging.error(f"Failed to send notification: {e}")
        

#region UPDATE TRADING TABLE
def update_trading_table():
    
    gui = get_gui_instance()
    global placed_order_ids, trade_counter, trade_timer_seconds

    is_rejected = False
    sell_quantity = 0
    remove_order_ids = []

    def update_trade_table(symbol, order_id, order_status):
        """ Helper function to update the trade details table in the GUI. """
        gui.update_trade_details_table(
            [{
                'symbol': symbol,
                'transactTime': int(time.time() * 1000),
                'orderId': order_id,
                'type': order_status['type'],
                'side': order_status['side'],
                'price': 1,  # Placeholder price
                'origQty': 0,  # Placeholder quantity
                'status': order_status['status']
            }],
            update_existing=True
        )

    try:
        for order_id, symbol in placed_order_ids.items():
            try:
                # Fetch latest price
                ticker = safe_binance_call(client.get_symbol_ticker, default={}, symbol=symbol)
                current_price = float(ticker['price'])
                gui.update_current_price(current_price)

                # Fetch order status
                order_status = safe_binance_call(client.get_order, default={}, orderId=order_id, symbol=symbol)
                status = order_status['status']
                
                if status == 'FILLED':
                    # Extract trade details
                    side = order_status['side']
                    buy_price = float(order_status['price'])
                    sell_price = float(order_status.get('stopPrice', buy_price))
                    quantity = float(order_status['origQty'])

                    # Calculate P&L
                    pnl = (sell_price - buy_price) * quantity if order_status['type'] != 'MARKET' else abs(float(order_status['cummulativeQuoteQty']))
                    rate = pnl / quantity if quantity > 0 else 0

                    # Log and update UI
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    trade_logger.info(f"Trade closed - [{timestamp}] - {trade_counter} - {symbol} - {side} - {quantity} - {rate:.5f} - {pnl:.5f} - {forced_trade_closure}")
                    gui.add_to_console(f"[{timestamp}] Trade closed for {symbol}. P&L: {pnl:.5f}")

                    # Mark for removal and update trade table
                    remove_order_ids.append(order_id)
                    update_trade_table(symbol, order_id, order_status)

                elif status in {'CANCELED', 'EXPIRED', 'REJECTED'}:
                    remove_order_ids.append(order_id)
                    update_trade_table(symbol, order_id, order_status)

                    if status == 'REJECTED':
                        is_rejected = True
                        sell_quantity = max(sell_quantity, float(order_status['origQty']))

            except Exception as e:
                trade_logger.info(f"Error processing order {order_id} ({symbol}): {e}")

    except Exception as e:
        trade_logger(f"Critical error in update_trading_table: {e}")

    # Remove processed order IDs
    for order_id in remove_order_ids:
        placed_order_ids.pop(order_id, None)

    # Close rejected trades if needed
    if is_rejected and sell_quantity > 0:
        close_market_trade(symbol, sell_quantity)
            
    
# endregion

def get_latest_trade_from_log(filename="detailed_trade_logs.log"):
    """
    Retrieve the most recent trade entry from the log file and calculate the time delay in seconds.
    Args:
        filename (str): Path to the log file.
    Returns:
        int: Seconds since the trade was logged, or None if no trade is found or an error occurs.
    """

    global trade_counter
    gui = get_gui_instance()
    global script_dir
    
    filename = os.path.join(script_dir, "logs", filename)
    try:
        with open(filename, "r") as file:
            lines = file.readlines()
            if not lines:
                return 0
            
            # Parse the latest logged trade
            latest_trade = lines[-1].strip().split(" | ")
            if int(latest_trade[0]) == trade_counter:
                # Extract trade timestamp and calculate delay
                trade_timestamp = datetime.strptime(latest_trade[20], "%Y-%m-%d %H:%M:%S")
                current_time = datetime.now()
                time_delay = (current_time - trade_timestamp).total_seconds()
                time_delay = int(round(time_delay / 5) * 5)
                # Update GUI timer and start trade graph
                gui.start_trade_graph(pair_name=latest_trade[1],entry_price=float(latest_trade[2]),stop_loss=float(latest_trade[3]),take_profit=float(latest_trade[4]))
                gui.update_trade_timer(time_delay)
                
                return time_delay
            else:
                gui.add_to_console("The last trade details do not match with the latest trade counter")
                return 0
    except Exception as e:
        logging.error(f"Error reading log file: {e}")
        return 0
    
    
    
#region DISPLAY  
def display_best_pair(best_pair):
    gui = get_gui_instance()
    """Display the ultimate best pair based on refined criteria."""
    if best_pair:
        
        pair = best_pair['pair']
        score = best_pair['score']
        analysis = best_pair['analysis']
        suggestion = best_pair['trade_suggestion']

        rr = suggestion['rr_ratio']

        gui.add_to_console("\nUltimate Scalping Opportunity:")
        gui.add_to_console(f"Pair: {pair}")
        gui.add_to_console(f"Refined Score: {score:.2f}")
        gui.add_to_console(f"Trend: {analysis['trend']}")
        gui.add_to_console(f"ATR: {analysis['atr']:.4f}")
        gui.add_to_console(f"Support: {analysis['support']:.4f}")
        gui.add_to_console(f"Resistance: {analysis['resistance']:.4f}")
        gui.add_to_console(f"Risk-to-Reward Ratio (R/R): {rr}")
    else:
        gui.add_to_console("No pair satisfies the refinement criteria for scalping.")
# endregion
