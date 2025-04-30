class DynamicTrendBreakoutStrategy:
    def __init__(self, config, indicator_cache):
        """
        Initialize the strategy with configuration and indicator cache.
        Args:
            config (dict): Configuration settings.
            indicator_cache (dict): Cached indicators for pairs.
        """
        self.config = config
        self.indicator_cache = indicator_cache

    def evaluate_pair(self, pair):
        """
        Evaluate a trading pair using precomputed indicators from the cache.
        Args:
            pair (str): Trading pair (e.g., 'BTCUSDT').
        Returns:
            tuple or None: (pair, score, df, analysis) or None if not tradable.
        """
        df = self.indicator_cache.get(pair)
        if df is None or df.empty:
            return None

        latest = df.iloc[-1]
        score = 0
        analysis = {}

        # ATR Filter
        atr = latest.get('atr', 0)
        pair_price = latest['close']
        min_atr, max_atr = self.config.get("ATR_BOUNDS", (0.0001, 0.04))
        if not (min_atr <= atr <= max_atr):
            return None

        # Dynamic RSI Overbought Threshold
        overbought_threshold = min(75 + min((atr / pair_price) * 100, 5), 85)
        if latest['rsi'] > overbought_threshold:
            return None

        # ADX Filter
        adx = latest.get('adx', 0)
        min_adx, max_adx = self.config.get("ADX_BOUNDS", (30, 65))
        if not (min_adx <= adx <= max_adx):
            return None

        # EMA Crossover
        if latest['ema_short'] > latest['ema_long']:
            score += self.config.get("EMA_SCORE_WEIGHT", 1)
            analysis['trend'] = "Bullish"
        else:
            analysis['trend'] = "Neutral"

        # MACD Histogram Growth
        dynamic_threshold = self.get_dynamic_histogram_threshold(atr)
        if (
            latest['macd'] > latest['signal']
            and latest['histogram'] > 0
            and (latest['histogram'] - df.iloc[-2]['histogram']) >= dynamic_threshold
        ):
            score += self.config.get("MACD_SCORE_WEIGHT", 1)

        # Volume Spike
        volume_ratio = latest['volume'] / df['volume'].mean()
        if volume_ratio > self.config.get("VOLUME_SPIKE_THRESHOLD", 1.5):
            score += self.config.get("VOLUME_SCORE_WEIGHT", 1)

        # Compile Analysis
        analysis.update({
            "atr": atr,
            "adx": adx,
            "resistance" : latest['resistance'],
            "support" : latest['support'],
            "trend": "Bullish" if latest['ema_short'] > latest['ema_long'] else "Neutral",
            "volume_spike": volume_ratio > self.config.get("VOLUME_SPIKE_THRESHOLD", 1.5),
            "breakout": latest.get('breakout'),
            "breakout_strength": latest.get('breakout_strength'),
            "breakout_probability": latest.get('breakout_probability'),
            "bollinger" : {
                "bb_lower": latest['bb_lower'],
                "bb_middle": latest['bb_middle'],
                "bb_upper": latest['bb_upper']
            }
        })

        # Return result if score meets the threshold
        return (pair, score, df, analysis) if score >= self.config["SCORE_THRESHOLD"] else None


    def get_dynamic_histogram_threshold(self, atr):
        """
        Calculate a dynamic threshold for MACD histogram growth.
        Args:
            atr (float): Average True Range value.
        Returns:
            float: Dynamic threshold for MACD histogram.
        """
        if atr < 0.01:
            return 0.00005  # Low volatility: small threshold
        elif atr < 0.05:
            return 0.00015  # Moderate volatility
        else:
            return 0.0003  # High volatility: stricter threshold
