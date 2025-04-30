class BreakoutValidationStrategy:
    def __init__(self, config):
        """
        Initialize the breakout validation strategy.
        Args:
            config (dict): Strategy-specific settings.
        """
        self.config = config

    def refine_best_pair(self, pair, df, score):
        """
        Refine the best trading pair based on breakout validation.
        Args:
            pair (str): Trading pair.
            df (pd.DataFrame): Data with indicators.
            score (float): Initial score from evaluation.
        Returns:
            dict: Refined trade suggestion or None if not valid.
        """
        latest = df.iloc[-1]
        resistance = latest['resistance']
        support = latest['support']
        latest_close = latest['close']
        atr = latest['atr']

        # Breakout Validation
        breakout_strength = (latest_close - resistance) / atr
        breakout = latest_close > resistance and breakout_strength > self.config["ROOM_MULTIPLIER"]

        if breakout:
            macd_valid = latest['histogram'] > latest['signal'] and latest['histogram'] > 0
            rsi_valid = self.config["RSI_BOUNDS"][0] < latest['rsi'] < self.config["RSI_BOUNDS"][1]
            if not (macd_valid and rsi_valid):
                return None

        # Risk-to-Reward Validation
        trade_suggestion = {
            "entry": latest_close,
            "stop_loss": support,
            "take_profit": resistance + (breakout_strength * atr),
            "rr_ratio": (resistance + (breakout_strength * atr) - latest_close) / (latest_close - support)
        }
        rr_ratio = trade_suggestion["rr_ratio"]
        if not self.config["RR_THRESHOLD"][0] <= rr_ratio <= self.config["RR_THRESHOLD"][1]:
            return None

        # Final Scoring
        normalized_rr = rr_ratio / self.config["RR_THRESHOLD"][0]
        refinement_score = score + normalized_rr

        return {
            "pair": pair,
            "score": refinement_score,
            "trade_suggestion": trade_suggestion,
            "breakout": breakout
        }
