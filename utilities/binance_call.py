"""
Binance API Utilities
=====================
Changes vs original:
  • filter_active_pairs() now uses the pair quality filter from
    utilities/pair_quality.py, which blocks meme tokens, non-ASCII
    symbols, pump/dumps, and thin liquidity pairs.
"""

from datetime import datetime, timedelta
from misc.config import config, client, logging, blacklist
import pandas as pd

from core.shared_state import get_gui_instance
from utilities.pair_quality import filter_quality_pairs


def filter_active_pairs():
    """
    Return a list of high-quality USDT pairs that pass all quality gates:
      - ASCII-only symbol
      - Not blacklisted
      - Minimum 24h volume (MIN_VOLUME)
      - Minimum 24h trade count (MIN_TRADES_24H)
      - No extreme 24h price change (MAX_24H_CHANGE_PCT)
      - Spread within MAX_SPREAD_PCT
    """
    global blacklist

    try:
        tickers       = client.get_ticker()
        exchange_info = client.get_exchange_info()

        trading_symbols = {
            s['symbol']
            for s in exchange_info['symbols']
            if s['status'] == 'TRADING' and s.get('isSpotTradingAllowed', False)
        }

        # Apply full quality pipeline
        active_pairs = filter_quality_pairs(tickers, trading_symbols, config)

        logging.info(f"Active pairs after quality filter: {len(active_pairs)}")
        return active_pairs

    except Exception as e:
        logging.error(f"Error fetching tickers: {e}")
        return []


def fetch_data(pair, timeframe=config["TIMEFRAME"], limit=config["CANDLES_LIMIT"]):
    """Fetch historical OHLCV data for a given pair and timeframe."""
    try:
        klines = client.get_klines(
            symbol=pair,
            interval=timeframe,
            limit=limit,
        )
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore',
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = \
            df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching data for {pair}: {e}")
        return None


def fetch_recent_data(
    pair,
    start_time_str: str = '2025-01-14T10:00:00',
    trade_length:   int  = 3600,
    timeframe:      str  = "1m",
    limit:          int  = 30,
):
    """Fetch historical data around a specific timestamp (used by backtester)."""
    try:
        start_time    = datetime.fromisoformat(start_time_str)
        trade_length  = int(trade_length)
        start_time_ms = int(start_time.timestamp() * 1000)
        end_time_ms   = int((start_time + timedelta(minutes=trade_length)).timestamp() * 1000)

        klines = client.get_klines(
            symbol=pair.strip(),
            interval=timeframe,
            startTime=start_time_ms,
            endTime=end_time_ms,
        )
        if not klines:
            raise ValueError(f"No data for {pair} from {start_time_str}")

        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore',
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = \
            df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        logging.error(f"fetch_recent_data error: {e}")
        return None


def close_market_trade(pair: str, quantity: float):
    """Place an emergency market sell order to close a position."""
    gui = get_gui_instance()
    try:
        market_order = client.order_market_sell(symbol=pair, quantity=quantity)
        gui.update_trade_details_table([{
            "symbol":        market_order["symbol"],
            "transactTime":  market_order["transactTime"],
            "orderId":       market_order["orderId"],
            "type":          market_order["type"],
            "side":          market_order["side"],
            "price":         market_order["fills"][0]["price"] if market_order["fills"] else "0",
            "origQty":       market_order["origQty"],
            "status":        market_order["status"],
        }])
        gui.add_to_console(
            f"MARKET SELL placed for {pair} qty={quantity}: {market_order['orderId']}"
        )
    except Exception as e:
        gui.add_to_console(f"Error in close_market_trade: {e}")
