import json
from pathlib import Path
from threading import Lock
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
import logging
import os
import requests
import time
from typing import Optional
from env_loader import load_env

load_env() # Load environment variables from .env file


indicator_cache_lock = Lock()
indicator_cache = {}

# Get the main folder (directory containing the script)
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

icon_file = os.path.join(script_dir, "misc", "Icon_bot.ico")

# Construct the full path to `order_worker.py`
worker_script = os.path.join(script_dir, "trade", "order_worker.py")

gui = None

# LIVE
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

_client_lock = Lock()
_client_instance: Optional[Client] = None


def get_client() -> Client:
    """
    Lazily initialize Binance client so importing this module never requires
    immediate network connectivity.
    """
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = Client(
                    BINANCE_API_KEY,
                    BINANCE_API_SECRET,
                    {"timeout": 60},
                    ping=False,
                )
    return _client_instance


class _LazyBinanceClient:
    """
    Proxy that defers Binance Client construction until first attribute access.
    """
    def __getattr__(self, item):
        return getattr(get_client(), item)


client = _LazyBinanceClient()

def safe_binance_call(func, *args, retries=3, delay=5, default=None, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, BinanceRequestException) as e:
            print(f"[WARN] Binance connection issue {attempt}/{retries}: {e}")

            if attempt < retries:
                time.sleep(delay)

        except BinanceAPIException as e:
            print(f"[ERROR] Binance API error: {e}")
            return default

        except Exception as e:
            print(f"[ERROR] Unexpected Binance error: {e}")
            return default

    print("[ERROR] Binance unreachable. Skipping this cycle.")
    return default


trade_counter = 0
forced_trade_closure = False
latest_pair = None
placed_order_ids = {}
active_thread = None



# Configuration 
config = {
    # ── Indicator windows ────────────────────────────────────────────────────
    "ATR_WINDOW":            14,
    "BB_WINDOW":             20,
    "SR_WINDOW":             15,    # ← was 20; 15 min S/R is more responsive on 1m
    "CANDLES_LIMIT":         100,   # ← was 50 (needed for Ichimoku + warmup)
    "STOCH_WINDOW":          14,
    "HULL_LENGTH":           55,
    "SMA_FILTER_LENGTH":     110,   # ← was 130; aligned to HULL_LENGTH*2 (55*2)

    # ── Pair selection ────────────────────────────────────────────────────────
    "MIN_VOLUME":            50_000_000,   # ← was 20M; blocks meme coins
    "MIN_TRADES_24H":        50_000,       # NEW: minimum real trades (anti-washtrading)
    "MAX_24H_CHANGE_PCT":    20.0,         # NEW: skip pump/dump already in progress
    "MAX_SPREAD_PCT":        0.0006,
    "BLACKLIST_AUTO":        True,         # NEW: auto-add pairs with >3 consec losses

    # ── Trade management ─────────────────────────────────────────────────────
    "TIMEFRAME":             "1m",
    "ADDITIONAL_TIMEFRAMES": ["3m", "5m"],
    "TIMEFRAME_WEIGHTS":     [3, 1],
    "EXCHANGE_FEES":         0.001,
    "TRADE_MAX_TIME_RUNNING":600,
    "PAIR_COOLDOWN_MINUTES": 30,           # NEW: 30-min cooldown after any trade

    # ── Signal scoring ────────────────────────────────────────────────────────
    "EMA_SPANS":             [9, 21],   # ← was [7,14]; 9/21 give cleaner signals
                                        #   with fewer false crosses on 1m noise
    "EMA_SCORE_WEIGHT":      1.5,    # ← was 2.5 (EMA lag reduced; freed weight to VWAP)
    "MACD_SCORE_WEIGHT":     2.0,
    "RSI_SCORE_WEIGHT":      1.0,
    "VOLUME_SCORE_WEIGHT":   2.0,
    "VWAP_SCORE_WEIGHT":     1.5,    # ← was 0.5 (VWAP = key intraday institutional level)
    "ICHIMOKU_SCORE_WEIGHT": 1.0,    # NEW: price above cloud = very strong bull bias
    "ADX_SLOPE_SCORE_WEIGHT":0.5,    # NEW: rising ADX = trend gaining strength
    "VOLUME_ACCEL_SCORE_WEIGHT": 0.5, # NEW: volume building = participation expanding
    "RSI_SLOPE_SCORE_WEIGHT":0.3,    # NEW: RSI trending up = momentum growing
    "CANDLEBODY_SCORE_WEIGHT":0.3,   # NEW: large bullish candle = conviction bar

    "SCORE_THRESHOLD":       6.5,    # ← was 5.0; max possible ~11.6, so 6.5 = ~56%
                                     #   of max — forces genuine conviction signals
    "REFINED_SCORE_THRESHOLD":7.5,   # ← was 6.0; second-screen bar raised in step

    # ── Indicator bounds ─────────────────────────────────────────────────────
    "RSI_BOUNDS":            [45, 75],   # ← was [50,70]; 45-75 captures full
                                         #   momentum build-up phase; overbought
                                         #   still capped by OVERBOUGHT_THRESHOLD
    "RSI_OVERBOUGHT_THRESHOLD": 78,      # ← was 75; 78 lets strong breakouts score
    "ADX_BOUNDS":            [25, 65],   # ← was [25,55]; allow strongly trending
                                         #   markets (crypto rallies often hit 60+)
    "ATR_BOUNDS":            [0.008, 0.025],  # ATR% bounds (atr/price)
    "VOLUME_SPIKE_THRESHOLD":2.2,           # ← was 2.0
    "VOLUME_THRESHOLD":      1.5,

    # ── Trade suggestion ─────────────────────────────────────────────────────
    "RR_THRESHOLD":          [1.3, 3.0],   # ← was [1.0, 2.5]; reject marginal 1.0R trades;
                                           #   allow high-quality 3.0R setups
    "ROOM_MULTIPLIER":       1.2,
    "ALLOW_PARTIAL_CONFIRMATION": True,
    "SL_BUFFER_MULTIPLIER":  1.1,
    "TP_BUFFER_MULTIPLIER":  2.0,          # ← was 1.8 (slightly more ambitious TP)
    "TRAILING_STOP_PCT":     0.05,

    # ── Multi-timeframe confirmation ──────────────────────────────────────────
    # Fixed score threshold that a higher timeframe (3m/5m) must reach to count
    # as "confirmed".  Previously compared against the variable 1m score, which
    # over-rejected valid signals because HTF candles look weaker in sub-bar metrics.
    "HTF_CONFIRM_THRESHOLD": 4.0,

    # ── Market regime ─────────────────────────────────────────────────────────
    "ENABLE_REGIME_FILTER":  True,    # NEW: apply regime-based score multipliers
    "REGIME_CACHE_MINUTES":  15,      # NEW: how often to re-check regime
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

# --- make sure logs/ exists ---
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)  # Create logs directory if it doesn't exist

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
