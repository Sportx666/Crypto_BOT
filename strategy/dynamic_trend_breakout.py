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
  NEW        Price-above-VWAP filter scoring (weight raised 0.5→1.5).
  NEW        HMA trend filter scoring (uses Hull MA).
  NEW        Cache read is now lock-protected.

  PROFITABILITY IMPROVEMENTS:
  • EMA weight reduced 2.5→1.5 (lagging indicator over-weighted before).
  • VWAP weight raised 0.5→1.5 (strongest intraday institutional level).
  • Ichimoku cloud position (+1.0): price above both Senkou spans = very strong.
  • ADX slope bonus (+0.5): rising ADX means trend is strengthening, not peaking.
  • Volume acceleration bonus (+0.5): growing volume = expanding participation.
  • RSI slope bonus (+0.3): RSI trending up = momentum building, not exhausted.
  • Bullish candle body bonus (+0.3): large close>open body = conviction bar.
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

        # ── HARD FILTER 5: bearish close — entry bar must not be red ─────────
        # A bearish candle at signal time means sellers dominated that bar;
        # entering here increases the chance of buying a local top.
        open_price_filter = float(latest.get('open', close))
        if close < open_price_filter:
            return None

        # ══════════════════════════════════════════════════════════════════════
        # SCORING
        # ══════════════════════════════════════════════════════════════════════

        # 1. EMA crossover (trend) — weight reduced 2.5→1.5; EMA is a lagging
        #    indicator and was previously over-represented in the score.
        ema_cross = float(latest['ema_short']) > float(latest['ema_long'])
        if ema_cross:
            score += self.config.get("EMA_SCORE_WEIGHT", 1.5)

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

        # 6. Price above VWAP (intraday bullish bias) — weight raised 0.5→1.5
        vwap      = float(latest.get('vwap', 0))
        vwap_prev = float(prev.get('vwap',  0))
        if vwap and close > vwap:
            score += self.config.get("VWAP_SCORE_WEIGHT", 1.5)
            # Bonus: VWAP rising AND price accelerating away from it
            # (expanding distance = buyers gaining control vs. VWAP anchor)
            if vwap_prev and vwap > vwap_prev and (close - vwap) > (float(prev['close']) - vwap_prev):
                score += 0.3

        # 7. HMA trend confirmation
        hma = float(latest.get('hma', 0))
        if hma and close > hma:
            score += 0.5

        # ── NEW 8. Ichimoku cloud position ────────────────────────────────────
        # Price above both Senkou spans = above cloud = strong bull confirmation.
        # Columns are 'senkou_span_a' / 'senkou_span_b' (set by calculate_ichimoku).
        senkou_a = float(latest.get('senkou_span_a', 0))
        senkou_b = float(latest.get('senkou_span_b', 0))
        if senkou_a and senkou_b and close > max(senkou_a, senkou_b):
            score += self.config.get("ICHIMOKU_SCORE_WEIGHT", 1.0)

        # ── NEW 9. ADX slope — trend gaining strength ─────────────────────────
        # Rising ADX means the trend is still accelerating; flat/falling ADX
        # means it may be peaking.  Compare last 3 bars.
        prev2 = df.iloc[-3] if len(df) >= 3 else prev
        adx_prev  = float(prev.get('adx',  0))
        adx_prev2 = float(prev2.get('adx', 0))
        if adx > adx_prev > adx_prev2 and adx > 0:
            score += self.config.get("ADX_SLOPE_SCORE_WEIGHT", 0.5)

        # ── NEW 10. Volume acceleration ───────────────────────────────────────
        # Volume increasing over the last 3 candles = participation expanding.
        vol_now   = float(latest['volume'])
        vol_prev  = float(prev['volume'])
        vol_prev2 = float(prev2['volume'])
        if vol_now > vol_prev > vol_prev2:
            score += self.config.get("VOLUME_ACCEL_SCORE_WEIGHT", 0.5)

        # ── NEW 11. RSI slope — momentum growing ──────────────────────────────
        rsi_prev  = float(prev.get('rsi',  50))
        rsi_prev2 = float(prev2.get('rsi', 50))
        if rsi > rsi_prev > rsi_prev2 and rsi < 75:
            score += self.config.get("RSI_SLOPE_SCORE_WEIGHT", 0.3)

        # ── NEW 12. Bullish candle body — conviction bar ───────────────────────
        # Bullish close>open body relative to ATR shows buyers in control.
        # Threshold 0.2 (was 0.3) includes moderate conviction bars, not just
        # giant candles; upper bound of 1.0 avoids reward for exhaustion spikes.
        open_price = float(latest.get('open', close))
        candle_body = close - open_price
        if atr > 0 and 0.2 <= candle_body / atr <= 1.0:
            score += self.config.get("CANDLEBODY_SCORE_WEIGHT", 0.3)

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
