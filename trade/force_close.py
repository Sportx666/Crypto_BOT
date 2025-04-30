import threading
import time
from utilities.binance_call import close_market_trade
from misc.config import *
import tkinter as tk

from core.shared_state import get_gui_instance


def close_orders():
    """Force close orders """   
    global forced_trade_closure
    gui = get_gui_instance()
    quantity = 0
    
    try:
        open_trades = client.get_open_orders()
        for order in open_trades:
            if order['orderId'] != 'PENDING_NEW':    
                order_id = order['orderId']
                pair = order['symbol']
                if order['side'] == 'SELL':
                    quantity = float(order['origQty'])
                else:
                    gui.add_to_console(f"Buy Order {order_id} being canceled") 

                # Cancel all orders
                try:                
                    client.cancel_order(orderId=order_id, symbol=pair)
                    gui.add_to_console(f"Order {order_id} for pair {pair} canceled.")   
                    break                                     
                except Exception as e:
                    gui.add_to_console(f"Error canceling order {order_id}: {e}")                          
        
    except Exception as e:
        gui.add_to_console(f"Error processing open orders: {e}")       
        
        
    if quantity > 0:
        close_market_trade(pair, quantity)
    
    gui.quit_button.config(state=tk.ACTIVE)
    gui.schedule_button.config(state=tk.ACTIVE)
    gui.close_button.config(state=tk.DISABLED)
    with threading.Lock():
        gui.is_running = False
    time.sleep(1)