from datetime import datetime, timedelta
from misc.config import config, client, logging, blacklist
import pandas as pd

from core.shared_state import get_gui_instance



def filter_active_pairs():
    """Filter pairs by volume and trading activity."""
    global blacklist

    try:
        tickers = client.get_ticker()
        exchange_info = client.get_exchange_info()
        trading_pairs = {
            symbol['symbol']: symbol['isSpotTradingAllowed']
            for symbol in exchange_info['symbols'] if symbol['status'] == 'TRADING'
        }

        active_pairs = [
            ticker['symbol'] for ticker in tickers
            if ticker['symbol'] in trading_pairs
            and trading_pairs[ticker['symbol']]
            and ticker['symbol'].endswith('USDT')
            and float(ticker['quoteVolume']) >= config["MIN_VOLUME"]
            and ticker['symbol'] not in blacklist
            and (float(ticker['askPrice']) / float(ticker['bidPrice']) - 1) <= config["MAX_SPREAD_PCT"]
        ]

        return active_pairs
    except Exception as e:
        logging.error(f"Error fetching tickers: {e}")
        return []
    
    

def fetch_data(pair, timeframe=config["TIMEFRAME"], limit=config["CANDLES_LIMIT"]):
    
    """Fetch historical data for a given pair and timeframe."""
    try:
        #logging.info(f"Fetching data for {pair} with timeframe {timeframe}.")
        # Fetch klines data
        klines = client.get_klines(
            symbol=pair,
            interval=timeframe,
            limit=limit
        )
        # Convert data to DataFrame
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades', 
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])

        # Keep only relevant columns
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        #logging.debug(f"Data fetched for {pair}. Total rows: {len(df)}")
        return df
    except Exception as e:
        logging.error(f"Error fetching data for {pair}: {e}")
        return None
    


def fetch_recent_data(pair, start_time_str='2025-01-14T10:00:00', trade_length=3600, timeframe="1m", limit=30):
    """
    Fetch historical candlestick data from Binance for a given trading pair.
    
    Args:
        pair (str): Trading pair (e.g., 'BTCUSDT').
        start_time_str (str): Start time in ISO format (e.g., '2025-01-14T10:00:00').
        trade_length (int): Length of the trade period in minutes.
        interval (str): Binance interval (default '1m').

    Returns:
        pd.DataFrame: DataFrame with historical OHLC data.
    """
    try:
        # Parse start_time
        start_time = datetime.fromisoformat(start_time_str)
        trade_length = int(trade_length)  # Ensure trade_length is an integer
        
        # Calculate the end time
        start_time_ms = int(start_time.timestamp() * 1000)
        end_time = start_time + timedelta(minutes=trade_length)
        end_time_ms = int(end_time.timestamp() * 1000)
        
        # Fetch data using Binance API
        klines = client.get_klines(
            symbol=pair.strip(),
            interval=timeframe,
            startTime=start_time_ms,
            endTime=end_time_ms
        )
        
        if not klines:
            raise ValueError(f"No data returned for {pair} from {start_time_str} to {end_time}.")
        
        # Create a DataFrame
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        
        # Return cleaned DataFrame
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None



def close_market_trade (pair, quantity):
    
    gui = get_gui_instance()


    # Place a new MARKET order with the same pair and quantity          
    try:               
        market_order = client.order_market_sell(symbol=pair, quantity=quantity)
        # Add result to trading table
        gui.update_trade_details_table(
            [
                {
                    "symbol": market_order["symbol"],
                    "transactTime": market_order["transactTime"],  
                    "orderId": market_order["orderId"],
                    "type": market_order["type"],
                    "side": market_order["side"],
                    "price": market_order["fills"][0]["price"] if market_order["fills"] else "0",  
                    "origQty": market_order["origQty"],  
                    "status": market_order["status"]
                    }
                ]
            )            
        gui.add_to_console(f"New MARKET order placed for {pair} with quantity {quantity}: {market_order}")     
            
    except Exception as e:
        gui.add_to_console(f"Error processing market orders: {e}")      
