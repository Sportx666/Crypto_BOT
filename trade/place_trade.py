import json
import logging
import math
import subprocess
from misc.config import *
from binance.enums import *

from core.shared_state import get_gui_instance


def place_trade(best_pair):
    global placed_order_ids 
    gui = get_gui_instance()
    """Place a limit buy order and set OCO (TP and SL) for the best pair."""
    if not best_pair:
        logging.warning("\nNo trade placed as no pair met the criteria.")
        return   
  
    pair = best_pair['pair']    
    suggestion = best_pair['trade_suggestion']
    entry_price = suggestion['entry']
    stop_loss = suggestion['stop_loss']
    take_profit = suggestion['take_profit']   
    atr = best_pair['analysis']['atr']
    # Cap TP just below resistance ONLY when a confirmed breakout has already
    # cleared that level (price > resistance).  In consolidation / pre-breakout
    # the TP calculated by the strategy already accounts for resistance distance
    # and capping it here would only shrink the reward-to-risk ratio needlessly.
    if best_pair['breakout']:
        resistance = best_pair['analysis']['resistance']
        logging.info(f"Breakout trade - capping TP just below resistance: {pair} - TP: {take_profit} → {resistance * 0.999}")
        take_profit = min(take_profit, resistance * 0.999)
    
    
    # Dynamic Slippage Tolerance
    slippage_tolerance = atr / entry_price
    max_entry_price = entry_price * (1 + slippage_tolerance)
    min_entry_price = entry_price * (1 - slippage_tolerance)
    if entry_price < min_entry_price or entry_price > max_entry_price:
        gui.add_to_console(f"Trade skipped due to slippage. Entry: {entry_price}, Allowed Range: [{min_entry_price}, {max_entry_price}]\n")
        return False
    
    # Fetch account balance
    try:
        # Get account details
        account_info = client.get_account()
        # Find the available balance for the quote currency (e.g., USDT)
        available_funds = next(
            (float(asset['free']) for asset in account_info['balances'] if asset['asset'] == 'USDT'),
            0.0  # Default to 0.0 if USDT balance is not found
        )
        bnb_funds = next(
            (float(asset['free']) for asset in account_info['balances'] if asset['asset'] == 'BNB'),
            0.0  # Default to 0.0 if USDT balance is not found
        )
        gui.add_to_console(f"\nAvailable balance: {available_funds}\n")
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return
    
    # Adjust TP/SL to comply with PRICE_FILTER
    try:
        exchange_info = client.get_exchange_info()
        symbol_info = next(
            symbol for symbol in exchange_info['symbols'] if symbol['symbol'] == pair
        )
        filters = {f['filterType']: f for f in symbol_info['filters']}
        # Adjust prices to comply with PRICE_FILTER tickSize
        tick_size = float(filters['PRICE_FILTER']['tickSize'])
        decimal_tick_size = max(0, len(str(tick_size).split('.')[-1].rstrip('0')))
        entry_price = round(entry_price / tick_size) * tick_size        
        take_profit = round(take_profit / tick_size) * tick_size     
        stop_loss = round(stop_loss / tick_size) * tick_size     
            
        entry_price, stop_loss, take_profit = map(lambda x: round(x, decimal_tick_size), [entry_price, stop_loss, take_profit])

        gui.add_to_console(f"Adjusted prices:\n - Entry: {entry_price},\n - SL: {stop_loss},\n - TP: {take_profit}\n")
    except Exception as e:
        logging.error(f"Error fetching PRICE_FILTER for {pair}: {e}")
        return


    # Ensure quantity meets Binance's minimum trading requirements
    try:
        # Fetch exchange info
        exchange_info = client.get_exchange_info()
        
        # Find the specific symbol info
        symbol_info = next(
            symbol for symbol in exchange_info['symbols'] if symbol['symbol'] == pair
        )

        # Get the minimum quantity filter
        filters = {f['filterType']: f for f in symbol_info['filters']}
        min_quantity = float(filters['LOT_SIZE']['minQty'])
        min_notional = float(filters['NOTIONAL']['minNotional'])
        step_size = float(filters['LOT_SIZE']["stepSize"])  # Precision for quantity

        
        # Reserve funds for fees (default trading fee is 0.1%)
        trading_fee_rate = config['EXCHANGE_FEES']  # Adjust if needed
        adjusted_funds = available_funds * (1 - trading_fee_rate)

        # Calculate maximum quantity
        quantity = adjusted_funds / entry_price

        # Round down to nearest step size
        quantity = math.floor(quantity / step_size) * step_size

        # Ensure quantity meets minimum requirements
        quantity = max(quantity, min_quantity)
        
        # Calculate required BNB for fees
        bnb_fee_rate = 0.00075  # 0.075% when using BNB for fees
        required_bnb = 2 * (quantity * entry_price * bnb_fee_rate)  # Buy + Sell fees
         
        # Prepare sell_quantity (adjusted for trading fees)
        sell_quantity = quantity
        if bnb_funds < required_bnb:  # If insufficient BNB for fees
            #gui.add_to_console(f"Insufficient BNB for fees. \nRequired: {required_bnb:.6f}, Available: {bnb_funds:.6f}.\n")
            #Calculate reduced quantity to sell due to fees
            sell_quantity = sell_quantity * (1 - trading_fee_rate)         # Reduce by fee percentage
            sell_quantity = math.floor(float(sell_quantity) / step_size) * step_size
        
        # Calculate order value
        order_value = entry_price * quantity

        # Check if order value meets MIN_NOTIONAL
        if order_value < min_notional:
            gui.add_to_console(f"Order value {order_value:.2f} is below MIN_NOTIONAL ({min_notional:.2f}).\n")
            return False  # Indicate the trade should be skipped
        
        quantity = round(quantity, 8)                           
        sell_quantity = round(sell_quantity, 8)                           
        
        quantity = f"{quantity:.8f}"                      # Format to 8 decimal places        
        sell_quantity = f"{sell_quantity:.8f}"            # Format to 8 decimal places        
        
        gui.add_to_console(f"Adjusted quatity:\n - Buy: {quantity},\n - Sell: {sell_quantity}")
        
    except Exception as e:
        logging.error(f"Error fetching market info for {pair}: {e}")
        return

    # OTOCO parameters
    params = {
            "symbol": pair,                         # Trading pair
            "workingType": ORDER_TYPE_LIMIT,        # Working order type
            "workingSide": SIDE_BUY,                # Side of the working order
            "workingPrice": entry_price,            # Price for the working order
            "workingQuantity": quantity,            # Quantity for the working order
            'workingTimeInForce': 'GTC',            
            "pendingSide": "SELL",                  # Side of the pending orders
            "pendingQuantity": sell_quantity,       # Quantity for take-profit and stop-loss reduced due to fees
            "pendingAboveType": "TAKE_PROFIT",      # Type of the take-profit order
            "pendingAboveStopPrice": take_profit,   # Trigger price for the take-profit order
            "pendingBelowType": "STOP_LOSS",        # Type of the stop-loss order        
            "pendingBelowStopPrice": stop_loss      # Trigger price for the stop-loss order
    }
    
    try:
        
        # Path to the worker script
        # Run the worker script
        order = client.ws_create_otoco_order(**params)
        #process = subprocess.Popen(
        #    ["python", worker_script],
        #    stdin=subprocess.PIPE,
        #    stdout=subprocess.PIPE,
        #    stderr=subprocess.PIPE,
        #    text=True
        #)

        # Send parameters to the worker
        #output, error = process.communicate(input=json.dumps(params))
        
        #if process.returncode == 0:
        #    result = json.loads(output)
        #if result["status"] == "success":
        gui.add_to_console("OTOCO order placed successfully.\n")
        placed_order_ids = {}
        # Update the table with the order reports
        gui.update_trade_details_table(order['orderReports'])
        gui.start_trade_graph(entry_price=entry_price, stop_loss=stop_loss,take_profit=take_profit,pair_name=pair)
        return True                

    except Exception as e:
        logging.error(f"Error placing order: {e}")
        gui.add_to_console(f"Error placing order: {e}\n")
        return False   
    
# endregion