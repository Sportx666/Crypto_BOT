import sys
print("PYTHONPATH:", sys.path)

from config import *
from final_pair import calculate_trade_suggestion
from utils import clear_cache
from utilities.binance_call import fetch_recent_data
from core.indicator import calculate_indicators


def backtest_trade(pair, start_date):
    """
    Backtest a single trade by applying the latest bot logic.
    Args:
        pair (str): Trading pair to backtest (e.g., 'BTCUSDT').
        start_date (str): Start date in 'YYYY-MM-DD' format.
    Returns:
        dict: Result of the backtest containing profitability and trade details.
    """
    try:
        print(f"Fetching historical data for {pair} starting {start_date}...")

        # Fetch data for multiple timeframes
        timeframes = [config["TIMEFRAME"]] + config["ADDITIONAL_TIMEFRAMES"]
        data_by_timeframe = {}
        for tf in timeframes:
            data_by_timeframe[tf] = fetch_recent_data(pair, timeframe=tf, limit=config["CANDLES_LIMIT"])
            if data_by_timeframe[tf] is None or data_by_timeframe[tf].empty:
                return {"status": "error", "message": f"No data available for {pair} on timeframe {tf}."}

            # Calculate indicators for each timeframe
            data_by_timeframe[tf] = calculate_indicators(data_by_timeframe[tf])

        # Clear cache after processing
        clear_cache()

        # Evaluate the pair with backtest logic
        main_df = data_by_timeframe[config["TIMEFRAME"]]
        refined_trade = refine_best_pair_backtest(main_df, pair, data_by_timeframe)
        if not refined_trade:
            return {"pair": pair, "start_date": start_date, "tradable": False}

        # Generate results
        result = {
            "pair": pair,
            "start_date": start_date,
            "tradable": True,
            "score": refined_trade['score'],
            "entry_price": refined_trade['trade_suggestion']['entry'],
            "exit_price": refined_trade['trade_suggestion']['take_profit'],
            "profit_or_loss": refined_trade['trade_suggestion']['rr_ratio'],
            "breakout": refined_trade['breakout'],
            "analysis": refined_trade.get("analysis", {})
        }
        return result

    except Exception as e:
        return {"status": "error", "message": str(e)}



def refine_best_pair_backtest(df, pair, data_by_timeframe):
    """
    Refine the best trading pair based on backtest-specific logic.
    """
    best_pair = None
    best_score = -1

    latest = df.iloc[-1]
    resistance = latest['resistance']
    support = latest['support']
    latest_close = latest['close']
    atr = latest['atr']

    # Breakout Validation
    breakout_strength = (latest_close - resistance) / atr
    breakout = latest_close > resistance and breakout_strength > config["ROOM_MULTIPLIER"]

    if breakout:
        macd_valid = latest['histogram'] > latest['signal'] and latest['histogram'] > 0
        rsi_valid = config["RSI_BOUNDS"][0] < latest['rsi'] < config["RSI_BOUNDS"][1]
        if not (macd_valid and rsi_valid):
            return None

    # Trend Confirmation
    trend_result, trend_details = check_trend_confirmation_backtest(data_by_timeframe)
    if trend_result is None:
        return None

    # Scoring Adjustments
    score = trend_details * (1.3 if trend_result else 0.7)
    
    bollinger = {
        
        "bb_lower" : latest['bb_lower'],
        "bb_middle" : latest["bb_middle"],
        "bb_upper" : latest["bb_upper"]
        
    }
    # Trade Suggestion
    suggestion = calculate_trade_suggestion(
        pair=pair,
        entry=latest_close,
        support=support,
        resistance=resistance,
        atr=atr,
        bollinger=bollinger,
        recent_low=min(df['low'].iloc[-5:]),
    )
    
    
    rr_ratio = suggestion["rr_ratio"]
    if not config["RR_THRESHOLD"][0] <= rr_ratio <= config["RR_THRESHOLD"][1]:
        return None

    # Final Scoring
    normalized_rr = rr_ratio / config["RR_THRESHOLD"][0]
    refinement_score = score + normalized_rr

    if refinement_score > best_score:
        best_score = refinement_score
        best_pair = {
            "pair": pair,
            "score": refinement_score,
            "trade_suggestion": suggestion,
            "breakout": breakout
        }

    return best_pair if best_pair else None


def check_trend_confirmation_backtest(data_by_timeframe):
    """
    Check trend confirmation across multiple timeframes for backtesting.
    """
    score = 0
    trend_details = {}

    for idx, (tf, df) in enumerate(data_by_timeframe.items()):
        if idx == 0:  # Skip the first item
            continue
        latest = df.iloc[-1]
        timeframe_score = 0

        if latest['ema_short'] > latest['ema_long']:
            timeframe_score += config['EMA_SCORE_WEIGHT']
        if config["RSI_BOUNDS"][0] < latest['rsi'] < config["RSI_BOUNDS"][1]:
            timeframe_score += config['RSI_SCORE_WEIGHT']
        if latest['macd'] > latest['signal'] and latest['histogram'] > 0:
            timeframe_score += config['MACD_SCORE_WEIGHT']
        if latest['volume'] > df['volume'].mean() * config["VOLUME_SPIKE_THRESHOLD"]:
            timeframe_score += config['VOLUME_SCORE_WEIGHT']

        score += timeframe_score * config["TIMEFRAME_WEIGHTS"][idx-1]
        trend_details[tf] = {
            "score": timeframe_score,
            "ema_diff": latest['ema_short'] - latest['ema_long'],
            "rsi": latest['rsi'],
            "volume": latest['volume']
        }

    total_weight = sum(config["TIMEFRAME_WEIGHTS"])
    normalized_score = score / total_weight
    return (normalized_score >= 0.75, score) if score > 0 else (None, trend_details)



if __name__ == "__main__":
    # User Input
    pair = 'PONDUSDT' #input("Enter the trading pair (e.g., BTCUSDT): ")
    start_date =''  #input("Enter the start date (YYYY-MM-DD): ")

    # Backtesting
    result = backtest_trade(pair, start_date)
    print("\nBacktest Result:")
    print(result)