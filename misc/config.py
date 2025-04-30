import json
from threading import Lock
from binance.client import Client
import logging
import os


indicator_cache_lock = Lock()
indicator_cache = {}

# Get the main folder (directory containing the script)
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

icon_file = os.path.join(script_dir, "misc", "Icon_bot.ico")

# Construct the full path to `order_worker.py`
worker_script = os.path.join(script_dir, "trade", "order_worker.py")

gui = None

# LIVE
apiKey = "1OXfkc8fLPBtw2mqrt1HDbhiopoFQd6OSNVp7LEZkaaA1FG95P2bQyUfsPEkAdbp"
secret = "mo6rnlXm0vK89wzRdj1NoIpbzF8U8TYhfLHRN7XEZYU9H3vBt7mkjwIUA4yAl78Q"

# TEST
#apiKey = "53LxLYiJOYn5AGg7v4GybKm1kVlCFdthEHxmGxvztfHA0OLAosbJGrs0IRhqa7vI"
#secret = "FpIuQJ1RAlid8lWuXJXPiGwPEogq4ndvOLnbm4wpBCpRiWoFO5oW2WNAQ8tsELFl"

client = Client(apiKey, secret, {"timeout": 60})
#client.API_URL = 'https://testnet.binance.vision/api'


trade_counter = 0
forced_trade_closure = False
latest_pair = None
placed_order_ids = {}
active_thread = None



# Configuration 
config = {
    "ATR_WINDOW": 14,  # Standard ATR for volatility check
    "BB_WINDOW": 20,  # Bollinger Bands window
    "SR_WINDOW": 10,  # Support/Resistance evaluation
    "MIN_VOLUME": 1000000,  # Increase liquidity requirement
    "EMA_SPANS": [7, 14],  # Short-term EMA crossover
    "RSI_BOUNDS": [50, 70],  # Keep within momentum zone
    "RSI_OVERBOUGHT_THRESHOLD": 75,  # Avoid buying top
    "SCORE_THRESHOLD": 3.5,  # Stricter selection
    "REFINED_SCORE_THRESHOLD": 4.5,  # Final filtering step
    "RR_THRESHOLD": [1.0, 2.5],  # Stricter RR filter
    "ROOM_MULTIPLIER": 1.2,  # Ensures enough room before resistance
    "ALLOW_PARTIAL_CONFIRMATION": True,  # Allows slightly weaker signals
    "TIMEFRAME": "1m",  # Fast scalping window
    "CANDLES_LIMIT": 50,  # 50 candles = good history
    "ADDITIONAL_TIMEFRAMES": ["3m", "5m"],  # Multi-TF confirmation
    "TIMEFRAME_WEIGHTS": [3, 1],  # Prioritize lower timeframe
    "EXCHANGE_FEES": 0.001,  # Binance fees
    "TRADE_MAX_TIME_RUNNING": 600,  # Reduce max holding time (10 min)
    "EMA_SCORE_WEIGHT": 2.5,  # Higher weight for trend-following
    "MACD_SCORE_WEIGHT": 2,  # MACD histogram growth must confirm
    "RSI_SCORE_WEIGHT": 1,  # RSI compliance still matters
    "VOLUME_SCORE_WEIGHT": 2,  # Volume spike must be present
    "VOLUME_SPIKE_THRESHOLD": 2,  # 2x average volume
    "ADX_BOUNDS": [20, 50],  # Avoid weak or overextended trends
    "ATR_BOUNDS": [0.001, 0.05],  # Example: Allow ATR% between 0.1% and 5%
    "SL_BUFFER_MULTIPLIER": 1.1,  # Adaptive SL
    "TP_BUFFER_MULTIPLIER": 1.8,  # TP should be larger to increase RR
    "VOLUME_THRESHOLD": 1.5  # 1.5x rolling avg volume needed
}

descriptions = {
    "MIN_VOLUME": "Minimum 24-hour trading volume in USD for a pair to qualify. Ensures liquidity.",
    "EMA_SPANS": "Short and long spans for EMA crossover to identify trends.",
    "ATR_WINDOW": "Rolling window size (in periods) for ATR calculation, reflecting recent volatility.",
    "BB_WINDOW": "Rolling window size (in periods) for Bollinger Bands to determine price ranges.",
    "SR_WINDOW": "Rolling window size (in periods) for Support/Resistance levels.",
    "RSI_BOUNDS": "Lower and upper bounds for the RSI indicator to avoid overbought/oversold zones.",
    "SCORE_THRESHOLD": "Minimum score a pair needs to achieve to be considered for trading.",
    "REFINED_SCORE_THRESHOLD": "Minimum score a pair needs to achieve 2nd round to be considered for trading.",
    "RR_THRESHOLD": "Minimum risk-to-reward ratio for placing a trade.",
    "ROOM_MULTIPLIER": "Ensures adequate room between the entry price and resistance level.",
    "ALLOW_PARTIAL_CONFIRMATION": "Allows trades with partial trend confirmation across timeframes.",
    "TIMEFRAME": "The primary candlestick timeframe for analysis, ideal for scalping.",
    "CANDLES_LIMIT": "Number of historical candles to fetch for analysis.",
    "ADDITIONAL_TIMEFRAMES": "Extra timeframes used for confirming trends.",
    "VOLUME_SCORE_WEIGHT": "Weight assigned to volume spikes in scoring logic.",
    "EXCHANGE_FEES": "Binance trading fees as a fraction of trade volume (0.1% per trade).",
    "TRADE_MAX_TIME_RUNNING": "Maximum duration (in seconds) for a trade to run (e.g., 30 minutes).",
    "EMA_SCORE_WEIGHT": "Weight assigned to EMA crossover in scoring logic.",
    "MACD_SCORE_WEIGHT": "Weight assigned to MACD confirmation in scoring logic.",
    "RSI_SCORE_WEIGHT": "Weight assigned to RSI compliance in scoring logic.",
    "VOLUME_SPIKE_THRESHOLD": "Minimum multiplier of average volume to qualify as a spike.",
    "TIMEFRAME_WEIGHTS": "Weights for additional timeframes (higher weight for longer timeframes).",
    "ADX_BOUNDS": "Min and Max ADX value to confirm strong trend strength.",
    "ATR_BOUNDS": "Min and Max ATR value to confirm pair selection.",
    "SL_BUFFER_MULTIPLIER": "Adjusts the buffer added to the stop-loss calculation",
    "SL_DYNAMIC_MULTIPLIER_BASE": "Dynamic multiplier in ATR-based stop-loss",
    "VOLUME_THRESHOLD": "Minimum volume multiplier for confirming trends across timeframes.",
}

skip_reason_descriptions = {
    "ADX": "The ADX value was not within the acceptable bounds, indicating insufficient trend strength.",
    "ATR": "The ATR value was outside the configured range, indicating extreme or insufficient volatility.",
    "TT": "The trading pair failed to confirm the trend across multiple timeframes.",
    "OB": "The trading pair was skipped because the RSI indicated an overbought condition.",
    "SRSI": "The Stochastic RSI value was too low, suggesting weak momentum.",
    "FB": "The price movement did not exhibit a confirmed breakout from resistance.",
    "RR": "The risk-to-reward ratio was below the configured threshold.",
    "RS": "The refined score of the trading pair did not meet the minimum threshold.",
    "RTM": "There was insufficient room to resistance, limiting profit potential.",
    "EV": "The entry price did not meet validation criteria, such as being too close to resistance."
}


logging.basicConfig(
    filename=f'{script_dir}\\logs\\trading_bot.log',  # Log file name
    level=logging.INFO,         # Minimum log level
    format='%(asctime)s - %(levelname)s - %(message)s',  # Log format
    datefmt='%Y-%m-%d %H:%M:%S'  # Timestamp format
)

# region TRADE_LOGGER
trade_logger = logging.getLogger('trade_logger')
trade_logger.setLevel(logging.INFO)
trade_file_handler = logging.FileHandler(f'{script_dir}\\logs\\trade_logs.log')  # Separate file for trade logs
trade_file_handler.setLevel(logging.INFO)
trade_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
trade_file_handler.setFormatter(trade_formatter)
trade_logger.addHandler(trade_file_handler)
# endregion

# region DETAILED_TRADE_LOGGER
detailed_trade_logger = logging.getLogger('detailed_trade_logger')
detailed_trade_logger.setLevel(logging.INFO)
detailed_trade_file_handler = logging.FileHandler(f'{script_dir}\\logs\\detailed_trade_logs.log')
detailed_trade_file_handler.setLevel(logging.INFO)
detailed_trade_formatter = logging.Formatter('%(message)s')  # Logs only the message (no timestamp, level)
detailed_trade_file_handler.setFormatter(detailed_trade_formatter)
detailed_trade_logger.addHandler(detailed_trade_file_handler)
# endregion

# region TRADE_SKIP_LOGGER
skip_trade_logger = logging.getLogger('skip_trade_logger')
skip_trade_logger.setLevel(logging.INFO)
skip_trade_file_handler = logging.FileHandler(f'{script_dir}\\logs\\skip_trade_logs.log')
skip_trade_file_handler.setLevel(logging.INFO)
skip_trade_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')  # Logs only the message 
skip_trade_file_handler.setFormatter(skip_trade_formatter)
skip_trade_logger.addHandler(skip_trade_file_handler)
# endregion
       

def load_trade_counter(filename="trade_counter.json"):
    global script_dir
    filename = os.path.join(script_dir, "misc", filename)
    try:
        with open(filename, "r") as file:
            data = json.load(file)
            return data.get("trade_count", 0)
    except FileNotFoundError:
        return 0  # Start at 0 if the file doesn't exist
    
    
# Load blacklist from file
def load_blacklist(filename="blacklist.json"):
    global blacklist
    global script_dir
    filename = os.path.join(script_dir, "misc", filename)
    try:
        with open(filename, "r") as file:
            blacklist = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        blacklist = []  # Initialize with an empty list if the file is missing or invalid
    
    return blacklist
        
        
trade_counter = load_trade_counter()
blacklist = load_blacklist()