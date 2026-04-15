from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from misc.config import *
from utilities.binance_call_new import fetch_data, filter_active_pairs
from core.indicator_new import calculate_indicators


def scan_pairs():
    try:
        logging.info("Scanning pairs for opportunities.")
        active_pairs = filter_active_pairs()
        results_queue = Queue()

        def safe_calculate(pair):
            try:
                df = fetch_data(pair)
                if df is not None:
                    indicators = calculate_indicators(df)
                    if indicators is not None:
                        with indicator_cache_lock:
                            indicator_cache[pair] = indicators
                            results_queue.put(pair)
            except Exception as e:
                logging.error(f"Error processing {pair}: {e}")

        with ThreadPoolExecutor(max_workers=10) as executor:
            for pair in active_pairs:
                executor.submit(safe_calculate, pair)


        results = []
        while not results_queue.empty():
            results.append(results_queue.get())

        return results

    except Exception as e:
        logging.error(f"Error scanning pairs: {e}")
        return []


def evaluate_pair_with_score(pair):
    """
    Evaluate a trading pair for intraday breakout trading with enhanced accuracy.
    """
    df = indicator_cache.get(pair)
    if df is None or df.empty:
        logging.warning(f"No indicator data available for pair {pair}")
        return None

    latest = df.iloc[-1]
    score = 0
    analysis = {}

    # ATR Validation (Optimal Volatility)
    atr = latest['atr']
    min_atr, max_atr = config.get("ATR_BOUNDS", (0.0002, 0.03))
    if not (min_atr <= atr <= max_atr):
        return None  # Skip if volatility is too low or high

    # Resistance Breakout and Consolidation Detection
    breakout_strength = (latest['close'] - latest['resistance']) / atr
    consolidation = latest['close'] > latest['support'] and latest['close'] < latest['resistance']
    if latest['close'] > latest['resistance'] and breakout_strength >= config["ROOM_MULTIPLIER"]:
        score += config["VOLUME_SCORE_WEIGHT"] * 1.5
    elif consolidation:
        score += config["EMA_SCORE_WEIGHT"]

    # EMA Crossover (Trend Confirmation)
    if latest['ema_short'] > latest['ema_long']:
        score += config["EMA_SCORE_WEIGHT"]

    # MACD Histogram (Momentum Strength)
    histogram_growth = latest['histogram'] - df.iloc[-2]['histogram']
    if latest['macd'] > latest['signal'] and histogram_growth > 0:
        score += config["MACD_SCORE_WEIGHT"]

    # RSI Validation (Momentum Within Bounds)
    if config["RSI_BOUNDS"][0] < latest['rsi'] < config["RSI_BOUNDS"][1]:
        score += config["RSI_SCORE_WEIGHT"]

    # Volume Spike Detection (Breakout Confirmation)
    if latest['volume'] > df['volume'].mean() * config["VOLUME_SPIKE_THRESHOLD"]:
        score += config["VOLUME_SCORE_WEIGHT"]

    # Skip if score is below threshold
    if score < config["SCORE_THRESHOLD"]:
        return None

    # Compile Analysis
    analysis.update({
        'atr': atr,
        'breakout_strength': breakout_strength,
        'trend': "Bullish" if latest['ema_short'] > latest['ema_long'] else "Neutral",
        'volume_spike': latest['volume'] > df['volume'].mean() * config["VOLUME_SPIKE_THRESHOLD"]
    })

    return (pair, score, df, analysis)