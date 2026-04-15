"""
Dynamic Trend Breakout Strategy  —  First Screening
=====================================================
Changes vs original:

  BUG FIX 1  ATR bounds now compare atr_percentage (atr/close) — not raw ATR.
             Old code compared absolute ATR (e.g. 500 USD for BTC) against
             bounds like 0.008–0.025 → rejected every pair. Now uses
             df['atr_percentage'] which is already computed by calculate_atr().

  BUG FIX 2  RSI score was completely missing — restored.

  BUG FIX 3  analysis dict now contains all fields expected by scheduler.py:
             breakout, breakout_strength, breakout_probability,
             momentum_confirmed, DMI, HGT — all computed from the DataFrame
             columns that calculate_indicators() now sets.

  NEW        Stochastic %K/%D crossover scoring.
  NEW        Price-above-VWAP filter scoring.
  NEW        HMA trend filter scoring (uses Hull MA).
  NEW        Cache read is now lock-protected.
"""

from misc.config import indicator_cache, indicator_cache_lock


class DynamicTrendBreakoutStrategy:

    def __init__(self, config, indicator_cache_ref):
        self.config          = config
        self.indicator_cache = indicator_cache_ref

    # ──────────────────────────────────────────────────────────────────────────
    def evaluate_pair(self, pair: str):
        """
        First-pass screening.  Returns (pair, score, df, analysis) or None.
        """
        # ── thread-safe cache read ────────────────────────────────────────────
        with indicator_cache_lock:
            df = self.indicator_cache.get(pair)

        if df is None or df.empty:
            return None

        latest     = df.iloc[-1]
        prev       = df.iloc[-2]
        score      = 0.0

        close      = float(latest['close'])
        atr        = float(latest.get('atr', 0))
        atr_pct    = float(latest.get('atr_percentage', atr / close if close else 0))

        # ── HARD FILTER 1: ATR% (volatility) ─────────────────────────────────
        # ATR_BOUNDS are in decimal-percentage form (e.g. 0.008 = 0.8%)
        min_atr_pct, max_atr_pct = self.config.get("ATR_BOUNDS", (0.006, 0.030))
        if not (min_atr_pct <= atr_pct <= max_atr_pct):
            return None

        # ── HARD FILTER 2: RSI overbought ────────────────────────────────────
        rsi = float(latest.get('rsi', 50))
        ob_threshold = min(75 + min((atr_pct * 100), 5), 85)
        if rsi > ob_threshold:
            return None

        # ── HARD FILTER 3: ADX trend strength ────────────────────────────────
        adx = float(latest.get('adx', 0))
        min_adx, max_adx = self.config.get("ADX_BOUNDS", (20, 65))
        if not (min_adx <= adx <= max_adx):
            return None

        # ── HARD FILTER 4: DMI — must be uptrend ─────────────────────────────
        if not bool(latest.get('DMI', True)):
            return None

        # ══════════════════════════════════════════════════════════════════════
        # SCORING
        # ══════════════════════════════════════════════════════════════════════

        # 1. EMA crossover (trend)
        ema_cross = float(latest['ema_short']) > float(latest['ema_long'])
        if ema_cross:
            score += self.config.get("EMA_SCORE_WEIGHT", 2.5)

        # 2. MACD momentum (histogram growing above signal)
        histo_now  = float(latest['histogram'])
        histo_prev = float(prev['histogram'])
        dyn_thresh = self._histogram_threshold(atr)
        if (
            float(latest['macd']) > float(latest['signal'])
            and histo_now > 0
            and (histo_now - histo_prev) >= dyn_thresh
        ):
            score += self.config.get("MACD_SCORE_WEIGHT", 2.0)

        # 3. RSI in momentum zone
        rsi_lo, rsi_hi = self.config.get("RSI_BOUNDS", [50, 70])
        if rsi_lo < rsi < rsi_hi:
            score += self.config.get("RSI_SCORE_WEIGHT", 1.0)

        # 4. Volume spike
        vol_ratio = float(latest['volume']) / float(df['volume'].mean())
        vol_spike = vol_ratio > self.config.get("VOLUME_SPIKE_THRESHOLD", 2.0)
        if vol_spike:
            score += self.config.get("VOLUME_SCORE_WEIGHT", 2.0)

        # 5. Stochastic %K crossed above %D (fresh momentum)
        stoch_k = float(latest.get('%K', 50))
        stoch_d = float(latest.get('%D', 50))
        prev_k  = float(prev.get('%K', 50))
        if stoch_k > stoch_d and prev_k <= float(prev.get('%D', 50)) and stoch_k < 80:
            score += 0.5

        # 6. Price above VWAP (intraday bullish bias)
        vwap = float(latest.get('vwap', 0))
        if vwap and close > vwap:
            score += 0.5

        # 7. HMA trend confirmation
        hma = float(latest.get('hma', 0))
        if hma and close > hma:
            score += 0.5

        # ── score threshold ───────────────────────────────────────────────────
        if score < self.config["SCORE_THRESHOLD"]:
            return None

        # ══════════════════════════════════════════════════════════════════════
        # ANALYSIS DICT — all fields required by scheduler.py logging
        # ══════════════════════════════════════════════════════════════════════
        resistance        = float(latest.get('resistance', close))
        support           = float(latest.get('support',    close))
        bk_strength       = float(latest.get('breakout_strength',    0.0))
        bk_prob           = float(latest.get('breakout_probability',  0.0))
        bk_flag           = bool(latest.get('breakout',   False))
        mom_conf          = bool(latest.get('momentum_confirmed', False))
        dmi_val           = bool(latest.get('DMI',  ema_cross))
        hgt               = self._histogram_threshold(atr)

        analysis = {
            "atr":                  atr,
            "adx":                  adx,
            "support":              support,
            "resistance":           resistance,
            "trend":                "Bullish" if ema_cross else "Neutral",
            "volume_spike":         vol_spike,
            "breakout":             bk_flag,
            "breakout_strength":    round(bk_strength, 4),
            "breakout_probability": round(bk_prob, 4),
            "momentum_confirmed":   mom_conf,
            "DMI":                  dmi_val,
            "HGT":                  round(hgt, 6),
            "bollinger": {
                "bb_upper":  float(latest.get('bb_upper',  close)),
                "bb_middle": float(latest.get('bb_middle', close)),
                "bb_lower":  float(latest.get('bb_lower',  close)),
            },
        }

        return (pair, score, df, analysis)

    # ──────────────────────────────────────────────────────────────────────────
    def _histogram_threshold(self, atr: float) -> float:
        """Dynamic MACD histogram growth threshold based on volatility."""
        if atr < 0.01:
            return 0.000_05
        elif atr < 0.05:
            return 0.000_15
        else:
            return 0.000_30
