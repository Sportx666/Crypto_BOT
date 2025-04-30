class TrendFollowingStrategy:
    def __init__(self, config):
        """
        Initialize the strategy with a configuration.
        Args:
            config (dict): Strategy-specific settings.
        """
        self.config = config

    def evaluate_pair_with_score(self, pair, df):
        """
        Evaluate a trading pair for trend-following opportunities.
        Args:
            pair (str): Trading pair (e.g., 'BTCUSDT').
            df (pd.DataFrame): Data with indicators calculated.
        Returns:
            tuple: (pair, score, df, analysis) or None if not tradable.
        """
        latest = df.iloc[-1]
        score = 0
        analysis = {}

        # ATR Validation
        atr = latest['atr']
        min_atr, max_atr = self.config.get("ATR_BOUNDS", (0.0002, 0.03))
        if not (min_atr <= atr <= max_atr):
            return None  # Skip if volatility is outside bounds

        # Resistance Breakout and Consolidation Detection
        breakout_strength = (latest['close'] - latest['resistance']) / atr
        consolidation = latest['close'] > latest['support'] and latest['close'] < latest['resistance']
        if latest['close'] > latest['resistance'] and breakout_strength >= self.config["ROOM_MULTIPLIER"]:
            score += self.config["VOLUME_SCORE_WEIGHT"] * 1.5
        elif consolidation:
            score += self.config["CONSOLIDATION_SCORE_WEIGHT"]

        # EMA Crossover
        if latest['ema_short'] > latest['ema_long']:
            score += self.config["EMA_SCORE_WEIGHT"]

        # MACD Histogram
        histogram_growth = latest['histogram'] - df.iloc[-2]['histogram']
        if latest['macd'] > latest['signal'] and histogram_growth > 0:
            score += self.config["MACD_SCORE_WEIGHT"]

        # RSI Validation
        if self.config["RSI_BOUNDS"][0] < latest['rsi'] < self.config["RSI_BOUNDS"][1]:
            score += self.config["RSI_SCORE_WEIGHT"]

        # Volume Spike Detection
        if latest['volume'] > df['volume'].mean() * self.config["VOLUME_SPIKE_THRESHOLD"]:
            score += self.config["VOLUME_SCORE_WEIGHT"]

        # Skip if score is below threshold
        if score < self.config["SCORE_THRESHOLD"]:
            return None

        # Compile Analysis
        analysis.update({
            'atr': atr,
            'breakout_strength': breakout_strength,
            'trend': "Bullish" if latest['ema_short'] > latest['ema_long'] else "Neutral",
            'volume_spike': latest['volume'] > df['volume'].mean() * self.config["VOLUME_SPIKE_THRESHOLD"]
        })

        return (pair, score, df, analysis)
