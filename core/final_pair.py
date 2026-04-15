from misc.config import config, client, logging
from core.indicator_new import calculate_indicators
from utilities.binance_call_new import fetch_data

def refine_best_pair(results, pair):
    best_pair = None
    best_score = -1
    score = 0

    #for pair, score, df, analysis  in results: 
    #    if df is None or df.empty:
    #        continue
    df = results
    latest = df.iloc[-1]
    resistance = latest['resistance']
    support = latest['support']
    latest_close = latest['close']
    atr = latest['atr']
    volume_spike = latest['volume'] > df['volume'].mean() * config["VOLUME_SPIKE_THRESHOLD"]

    # Breakout Validation
    breakout_strength = (latest_close - resistance) / atr
    breakout = latest_close > resistance and breakout_strength > config["ROOM_MULTIPLIER"]

    if breakout:
        macd_valid = latest['histogram'] > latest['signal'] and latest['histogram'] > 0
        rsi_valid = config["RSI_BOUNDS"][0] < latest['rsi'] < config["RSI_BOUNDS"][1]

        if not (volume_spike and macd_valid and rsi_valid):
            logging.info(f"Skipping {pair}: Breakout not validated.")
            #continue

    # Trend Confirmation
    trend_result, trend_details = check_trend_confirmation(pair)
    if trend_result is None:
        logging.info(f"Skipping {pair}: No trend confirmation.")
        return None
    elif trend_result:
        score *= 1.3
    else:
        score *= 0.7

    # Trade Suggestion
    suggestion = calculate_trade_suggestion(
        pair=pair,
        entry=latest_close,
        support=support,
        resistance=resistance,
        atr=atr,
        bollinger=latest['bollinger'],
        recent_low=min(df['low'].iloc[-5:]),
    )

    if suggestion is None:
        return None

    # Risk-to-Reward Validation
    rr_ratio = suggestion['rr_ratio']
    if not config["RR_THRESHOLD"][0] <= rr_ratio <= config["RR_THRESHOLD"][1]:
        logging.info(f"Skipping {pair}: R/R ratio not suitable.")
        #continue

    # Final Scoring
    normalized_rr = rr_ratio / config["RR_THRESHOLD"][0]
    refinement_score = score + normalized_rr

    if refinement_score > best_score:
        best_score = refinement_score
        analysis = {
            "atr": atr,
            "support": support,
            "resistance": resistance,
            "breakout_strength": breakout_strength,
            "trend_details": trend_details,
        }
        best_pair = (pair, refinement_score, analysis, suggestion, breakout)

    return {
        "pair": best_pair[0],
        "score": best_pair[1],
        "analysis": best_pair[2],
        "trade_suggestion": best_pair[3],
        "breakout": best_pair[4]
    } if best_pair else None
    
    

def calculate_trade_suggestion(pair, entry, support, resistance, atr, bollinger, recent_low):
    bb_lower = bollinger["bb_lower"]
    bb_middle = bollinger["bb_middle"]
    bb_upper = bollinger["bb_upper"]
    band_range = bb_upper - bb_lower

    # Fetch real-time order book data
    try:
        order_book = client.get_order_book(symbol=pair)
        best_ask = float(order_book['asks'][0][0]) if order_book['asks'] else None
        best_bid = float(order_book['bids'][0][0]) if order_book['bids'] else None
    except Exception as e:
        logging.warning(f"Order book data unavailable for {pair}: {e}")
        best_ask, best_bid = None, None

    # Entry Adjustment
    entry = max(entry, bb_middle, support)
    if best_ask and entry > best_ask:
        entry = best_ask
    if best_bid and entry < best_bid:
        entry = best_bid

    # Stop-Loss Calculation
    sl_multiplier = 1.0 if atr < 0.01 else 1.5  # Tight SL for low ATR
    stop_loss = max(bb_lower, entry - (sl_multiplier * atr), recent_low - (0.5 * band_range))
    stop_loss = round(stop_loss, 6)

    # Take-Profit Calculation
    momentum_multiplier = 2 if atr / band_range > 0.5 else 1.5
    take_profit = max(bb_upper, entry + (band_range * 2), resistance + (1.5 * atr), entry + (momentum_multiplier * atr))
    if entry <= resistance:
        take_profit = min(take_profit, resistance + (momentum_multiplier * atr))
    take_profit = round(take_profit, 6)

    # Risk-to-Reward Ratio
    fee_cost = entry * config["EXCHANGE_FEES"] * 2
    risk = max(entry - stop_loss + fee_cost, 0.00001)
    reward = max(take_profit - entry - fee_cost, 0.0)
    rr_ratio = reward / risk if risk > 0 else 0

    # Room-to-Resistance Validation
    room_to_resistance = resistance - entry
    dynamic_rtm_threshold = max(atr, band_range * 1.2, 0.02 * entry)
    if room_to_resistance < dynamic_rtm_threshold:
        logging.info(f"Skipping {pair}: Room to resistance {room_to_resistance:.6f} is below dynamic threshold {dynamic_rtm_threshold:.6f}")
        return None

    if rr_ratio < config["RR_THRESHOLD"][0]:
        logging.info(f"Skipping {pair}: Risk-to-reward ratio {rr_ratio:.2f} is below the threshold.")
        return None

    return {
        "entry": round(entry, 6),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "rr_ratio": round(rr_ratio, 2)
    }



def check_trend_confirmation(pair):
    trend_score = 0
    total_weights = sum(config.get("TIMEFRAME_WEIGHTS", [1] * len(config["ADDITIONAL_TIMEFRAMES"])))
    timeframe_results = []

    for idx, timeframe in enumerate(config["ADDITIONAL_TIMEFRAMES"]):
        df = fetch_data(pair, timeframe=timeframe)
        if df is None or df.empty:
            timeframe_results.append({"timeframe": timeframe, "score": 0, "reason": "No data"})
            continue

        df = calculate_indicators(df)
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

        trend_score += timeframe_score * config["TIMEFRAME_WEIGHTS"][idx]
        timeframe_results.append({
            "timeframe": timeframe,
            "score": timeframe_score,
            "ema": latest['ema_short'] - latest['ema_long'],
            "rsi": latest['rsi'],
            "volume": latest['volume']
        })

    trend_strength = trend_score / total_weights
    return (trend_strength >= 0.75, timeframe_results) if trend_score > 0 else (None, timeframe_results)
