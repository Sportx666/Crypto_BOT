import numpy as np

from core.indicator import calculate_indicators
from utilities.binance_call import fetch_data


class ScalpingBreakoutStrategy:
    def __init__(self, config, logger):
        """
        Initialize the scalping breakout strategy.
        Args:
            config (dict): Strategy-specific configuration.
        """
        self.config = config
        self.logger = logger

    def refine_best_pair(self, results):
        """
        Refine the results to find the best pair for scalping.
        Args:
            results (list): List of evaluated pairs with their scores and analysis.
        Returns:
            dict: Refined trade suggestion or None if no pair is suitable.
        """
        best_pair = None
        best_score = -1

        for pair, score, df, analysis in results:
            if df is None or df.empty:
                continue

            latest = df.iloc[-1]
            resistance = analysis['resistance']
            latest_close = latest['close']
            volume_spike = latest['volume'] > df['volume'].mean() * 1.8
            macd_histogram = latest['histogram']
            macd_signal = latest['signal']
            rsi = latest['rsi']

            # Step 1: Breakout Validation
            breakout = latest_close > resistance
            if breakout:
                macd_valid = macd_histogram > macd_signal and macd_histogram > 0
                rsi_valid = self.config["RSI_BOUNDS"][0] < rsi < self.config["RSI_BOUNDS"][1]
                if not (volume_spike and macd_valid and rsi_valid):
                    continue

            # Step 2: Trend Confirmation
            trend_result, trend_data = self.check_trend_confirmation(pair, score)
            if trend_result is None:
                continue
            elif trend_result:
                score *= 1.3
            else:
                score *= 0.7

            # Step 3: Trade Suggestion
            suggestion = self.calculate_trade_suggestion(
                pair,
                df=df
            )

            if suggestion is None:
                continue

            rr = suggestion['rr_ratio']
            min_rr, max_rr = self.config.get("RR_THRESHOLD", (1.2, 2.0))
            if not min_rr <= rr <= max_rr:
                continue

            # Step 4: Scoring Refinement
            support_resistance_range = analysis['resistance'] - analysis['support']
            range_factor = min((support_resistance_range / analysis['atr']), 3)
            normalized_rr = rr / self.config["RR_THRESHOLD"][0]
            normalized_range = range_factor / 3

            refinement_score = score + normalized_rr + normalized_range
            if refinement_score < self.config.get("REFINED_SCORE_THRESHOLD", 5.0):
                continue

            if refinement_score > best_score:
                best_score = refinement_score
                best_pair = (pair, refinement_score, analysis, suggestion, breakout)

        return {
            "pair": best_pair[0],
            "score": best_pair[1],
            "analysis": best_pair[2],
            "trade_suggestion": best_pair[3],
            "breakout": best_pair[4],
        } if best_pair else None
        

    def check_trend_confirmation(self, pair, score):
        """
        Placeholder for trend confirmation logic.
        This function should be implemented based on your specific requirements.
        """
        # Implement or replace this with actual trend confirmation logic
        trend_score = 0
        total_timeframes = len(self.config["ADDITIONAL_TIMEFRAMES"])
        weights = list(map(int, self.config.get("TIMEFRAME_WEIGHTS", [1] * total_timeframes)))
        timeframe_results = []

        for idx, timeframe in enumerate(self.config["ADDITIONAL_TIMEFRAMES"][:3]):  # Focus on the 3 shortest timeframes
            timeframe_score = 0
            df = fetch_data(pair, timeframe=timeframe)
            if df is None or df.empty:
                timeframe_results.append({"timeframe": timeframe, "score": 0, "reason": "No data"})
                continue

            df = calculate_indicators(df)
            latest = df.iloc[-1]

            # Indicator checks
            if latest['ema_short'] > latest['ema_long']:
                timeframe_score += self.config['EMA_SCORE_WEIGHT']

            if self.config["RSI_BOUNDS"][0] < latest['rsi'] < self.config["RSI_BOUNDS"][1]:
                timeframe_score += self.config['RSI_SCORE_WEIGHT']

            if latest['macd'] > latest['signal'] and latest['histogram'] > 0:
                timeframe_score += self.config['MACD_SCORE_WEIGHT']

            if latest['volume'] > df['volume'].mean() * self.config.get("VOLUME_THRESHOLD", 1.5):
                timeframe_score += self.config['VOLUME_SCORE_WEIGHT']

            if self.config["ADX_BOUNDS"][0] < latest['adx'] < self.config["ADX_BOUNDS"][1]:
                timeframe_score += 1

            stoch_rsi_valid = latest['stoch_rsi'] > 0.2
            if not stoch_rsi_valid:
                timeframe_score -= 1

            if timeframe_score >= score:
                trend_score += weights[idx]

            timeframe_results.append({
                "timeframe": timeframe,
                "score": timeframe_score,
                "atr": latest['atr'],
                "volume": latest['volume'],
                "adx": latest['adx'],
                "momentum": latest['stoch_rsi']
            })

        # Aggregate results for `calculate_trade_suggestion`
        aggregated_results = {
            "weighted_trend_score": trend_score,
            "average_atr": np.mean([r['atr'] for r in timeframe_results if 'atr' in r]),
            "average_volume": np.mean([r['volume'] for r in timeframe_results if 'volume' in r]),
            "average_adx": np.mean([r['adx'] for r in timeframe_results if 'adx' in r]),
            "momentum_confirmation": all([r['momentum'] > 0.2 for r in timeframe_results if 'momentum' in r]),
        }

        required_score = sum(weights[:3])  # Adjust for the limited timeframes
        if trend_score >= required_score:
            return True, aggregated_results
        elif self.config["ALLOW_PARTIAL_CONFIRMATION"] and trend_score >= (required_score * 0.75):
            return True, aggregated_results
        else:
            return False, aggregated_results
    
    
    
    def calculate_trade_suggestion(self, pair, df):
        """
        Simplified trade suggestion calculation for scalping.
        """
        
        if df is None or df.empty:
            return None

        latest = df.iloc[-1]
        
        bb_middle = latest["bb_middle"]
        support = latest['support']
        entry = latest['close']
        atr_value = latest['atr']

        # Entry Logic: Use middle band or support as the base
        entry = max(entry, bb_middle, support)

        # Fetch Higher Timeframe (5m) Data for Strong S/R Levels
        htf_data = fetch_data(pair, timeframe="5m", limit=50)
        higher_tf_support = htf_data["low"].min()
        higher_tf_resistance = htf_data["high"].max()

        # Recent Price Action Analysis
        swing_low = df['low'].rolling(window=30).min().iloc[-1]
        swing_high = df['high'].rolling(window=30).max().iloc[-1]

        # ATR-Based Dynamic Multipliers
        sl_multiplier = 1.2 if atr_value < 0.01 else 1.5 if atr_value < 0.03 else 2.0
        tp_multiplier = 1.5 if atr_value < 0.01 else 2.0 if atr_value < 0.03 else 2.5

        # Dynamic Stop-Loss Calculation
        stop_loss_candidates = [
            swing_low,
            higher_tf_support,
            entry - (sl_multiplier * atr_value),
        ]

        stop_loss = min(stop_loss_candidates)
        stop_loss = round(stop_loss, 6)

        # Dynamic Take-Profit Calculation
        take_profit_candidates = [
            swing_high,
            higher_tf_resistance,
            entry + (tp_multiplier * atr_value),
        ]

        take_profit = max(take_profit_candidates)
        take_profit = round(take_profit, 6)


        # Risk-to-Reward Ratio
        risk = max(entry - stop_loss, 0.00001)
        reward = max(take_profit - entry, 0.0)
        rr_ratio = reward / risk if risk > 0 else 0

        # Validate R/R
        if rr_ratio < self.config["RR_THRESHOLD"][0] or rr_ratio > self.config["RR_THRESHOLD"][1]:
            self.logger.info(f"%RR% - Skipping {pair}: R/R {rr_ratio} outside range.")
            return None

        return {
            "entry": round(entry, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profit": round(take_profit, 5),
            "rr_ratio": round(rr_ratio, 2),
        }
