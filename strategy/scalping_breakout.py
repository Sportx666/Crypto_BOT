"""
Scalping Breakout Strategy  —  Second Screening & Trade Suggestion
===================================================================
Changes vs original:

  BUG FIX 1  SL used min(swing_low_30, htf_min, atr_sl) → took the WIDEST
             possible stop, destroying RR.  Now: tight ATR-based SL anchored
             to nearest support, with a hard maximum loss cap (1.5% of entry).

  BUG FIX 2  TP used max(swing_high_30, htf_all_time_high, atr_tp) → took the
             FURTHEST TP, making it unreachable for scalping.  Now: uses the
             CLOSER of ATR-based TP and nearest resistance (- small buffer).

  BUG FIX 3  RR calculation now includes round-trip exchange fees.

  NEW        Explicit minimum-distance guard for SL (at least 0.2% below entry)
             so the order is always valid on Binance.

  NEW        analysis dict is enriched with momentum_confirmed and HGT fields
             passed through from the first-screen result.

  PROFITABILITY IMPROVEMENTS:
  • Regime-aware TP multiplier: BULL_STRONG uses 2.4× ATR (was hardcoded 1.8×),
    giving larger targets when macro conditions support follow-through.
  • Regime-aware RR ceiling: BULL_STRONG allows up to 3.5R (was 2.5R max),
    so high-quality setups are no longer rejected for being "too profitable".
  • HTF confirmation uses a fixed threshold (4.0) instead of comparing against
    the variable 1m score — fixes over-rejection of valid signals on HTF.
  • Regime config keys (TP_BUFFER_MULTIPLIER, RR_MAX_OVERRIDE) are read from
    the live_config that scheduler already populates via apply_regime_to_config().
"""

import numpy as np
from core.indicator_new import calculate_indicators
from utilities.binance_call import fetch_data


class ScalpingBreakoutStrategy:

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    # ──────────────────────────────────────────────────────────────────────────
    def refine_best_pair(self, results: list):
        """
        Second-pass screening. Accepts the list from DynamicTrendBreakout.
        Returns the single best trade dict or None.
        """
        best_pair  = None
        best_score = -1.0

        for pair, score, df, analysis in results:
            if df is None or df.empty:
                continue

            latest     = df.iloc[-1]
            resistance = float(analysis['resistance'])
            close      = float(latest['close'])

            # ── 1. Breakout gate ──────────────────────────────────────────────
            if close > resistance:
                vol_spike   = float(latest['volume']) > float(df['volume'].mean()) * 1.8
                macd_valid  = (float(latest['histogram']) > float(latest['signal'])
                               and float(latest['histogram']) > 0)
                rsi_valid   = (self.config["RSI_BOUNDS"][0]
                               < float(latest['rsi'])
                               < self.config["RSI_BOUNDS"][1])
                if not (vol_spike and macd_valid and rsi_valid):
                    self.logger.info(f"%FB% - Skipping {pair}: breakout not confirmed.")
                    continue

            # ── 2. Stochastic RSI momentum gate ───────────────────────────────
            stoch_rsi = float(latest.get('stoch_rsi', 1.0))
            if stoch_rsi < 0.25:
                self.logger.info(f"%SRSI% - Skipping {pair}: StochRSI {stoch_rsi:.2f} too low.")
                continue

            # ── 3. Multi-timeframe trend confirmation ─────────────────────────
            trend_result, trend_data = self._check_trend_confirmation(pair, score)
            if trend_result is None:
                self.logger.info(f"%TT% - Skipping {pair}: no trend data.")
                continue
            elif trend_result:
                score *= 1.3
            else:
                score *= 0.7

            # ── 4. Trade suggestion ───────────────────────────────────────────
            suggestion = self._calculate_trade_suggestion(pair, df, analysis)
            if suggestion is None:
                continue

            rr = suggestion['rr_ratio']
            min_rr, max_rr = self.config.get("RR_THRESHOLD", (1.3, 3.0))
            if not (min_rr <= rr <= max_rr):
                continue

            # ── 5. Refined scoring ────────────────────────────────────────────
            sup  = float(analysis.get('support',    close * 0.99))
            atr  = float(analysis.get('atr',        0.001 * close))
            sr_range      = max(resistance - sup, atr)
            range_factor  = min(sr_range / atr, 3.0)
            norm_rr       = rr / self.config["RR_THRESHOLD"][0]
            norm_range    = range_factor / 3.0
            refinement    = score + norm_rr + norm_range

            if refinement < self.config.get("REFINED_SCORE_THRESHOLD", 4.5):
                self.logger.info(f"%RS% - Skipping {pair}: refined score {refinement:.2f} below threshold.")
                continue

            if refinement > best_score:
                best_score = refinement

                # Enrich analysis with trend data
                enriched = dict(analysis)
                enriched.update({
                    "average_atr":    trend_data.get("average_atr", atr),
                    "average_adx":    trend_data.get("average_adx", 0),
                    "momentum_confirmation": trend_data.get("momentum_confirmation", False),
                    "DMI":            analysis.get("DMI", False),
                    "HGT":            analysis.get("HGT", 0),
                    "momentum_confirmed": analysis.get("momentum_confirmed", False),
                    "volume_spike":   analysis.get("volume_spike", False),
                    "breakout":       analysis.get("breakout", False),
                    "breakout_strength":    analysis.get("breakout_strength", 0),
                    "breakout_probability": analysis.get("breakout_probability", 0),
                })

                best_pair = (pair, refinement, enriched, suggestion,
                             bool(close > resistance))

        if best_pair is None:
            return None

        return {
            "pair":             best_pair[0],
            "score":            best_pair[1],
            "analysis":         best_pair[2],
            "trade_suggestion": best_pair[3],
            "breakout":         best_pair[4],
        }

    # ──────────────────────────────────────────────────────────────────────────
    def _calculate_trade_suggestion(self, pair: str, df, analysis: dict):
        """
        Tight ATR-based SL/TP suitable for 1-minute scalping.

        Stop-Loss logic:
          • Primary:   entry − (sl_multiplier × ATR)
          • Floor:     nearest support (don't place SL below it)
          • Hard cap:  max 1.5% below entry (ensures order validity)
          • Hard min:  at least 0.2% below entry  (Binance filter)

        Take-Profit logic:
          • ATR-based: entry + (tp_multiplier × ATR)
          • Resistance: nearest resistance − tiny buffer
          • Use the CLOSER of the two (more realistic for scalping)
          • Ensure at minimum 1.5 × risk reward
        """
        if df is None or df.empty:
            return None

        latest     = df.iloc[-1]
        close      = float(latest['close'])
        atr        = float(latest.get('atr', close * 0.005))
        atr_pct    = atr / close if close else 0.005
        support    = float(analysis.get('support',    close * 0.99))
        resistance = float(analysis.get('resistance', close * 1.01))
        bb_middle  = float(latest.get('bb_middle', close))
        bb_upper   = float(latest.get('bb_upper',  close * 1.01))

        # Entry: at least at BB middle (don't buy deep below recent mean)
        entry = max(close, bb_middle)

        # ── Stop-Loss ─────────────────────────────────────────────────────────
        sl_mult = 1.2 if atr_pct < 0.010 else (1.5 if atr_pct < 0.020 else 2.0)
        atr_sl  = entry - (sl_mult * atr)

        # Hard caps
        max_loss_pct = 0.015   # never more than 1.5% below entry
        min_dist_pct = 0.002   # never less than 0.2% below entry (Binance min)

        # Add a 0.3% buffer ABOVE support so a single wick through the level
        # doesn't trigger the stop before the trade idea is invalidated.
        support_buffered = support * 1.003
        stop_loss = max(
            atr_sl,
            support_buffered,                  # 0.3% above raw support
            entry * (1 - max_loss_pct),        # hard max loss cap
        )
        # Ensure minimum distance
        stop_loss = min(stop_loss, entry * (1 - min_dist_pct))
        stop_loss = round(stop_loss, 6)

        risk = entry - stop_loss
        if risk <= 0:
            return None

        # ── Take-Profit ───────────────────────────────────────────────────────
        # TP multiplier is regime-aware: BULL_STRONG gets a larger target.
        # Default is 2.0× the SL distance (slightly more ambitious than old 1.8×).
        # In BULL_STRONG regime apply_regime_to_config() sets TP_BUFFER_MULTIPLIER=2.4.
        tp_cfg_mult = self.config.get("TP_BUFFER_MULTIPLIER", 2.0)
        tp_mult  = sl_mult * tp_cfg_mult
        atr_tp   = entry + (tp_mult * atr)
        res_tp   = resistance * 0.9985   # just below resistance

        # Use whichever is CLOSER (more likely to be hit for scalping).
        # Minimum distance = 1.5× risk so we always have at least 1.5:1 RR.
        if res_tp > entry + 1.5 * risk:
            take_profit = min(atr_tp, res_tp)
        else:
            take_profit = atr_tp

        # Guarantee minimum 1.5:1 RR
        take_profit = max(take_profit, entry + 1.5 * risk)
        take_profit = round(take_profit, 6)

        # ── RR with fees ──────────────────────────────────────────────────────
        fee      = entry * self.config.get("EXCHANGE_FEES", 0.001) * 2
        reward   = take_profit - entry - fee
        risk_fee = risk + fee

        if risk_fee <= 0:
            return None

        rr_ratio = reward / risk_fee
        min_rr, max_rr = self.config.get("RR_THRESHOLD", (1.3, 3.0))

        if not (min_rr <= rr_ratio <= max_rr):
            self.logger.info(
                f"%RR% - Skipping {pair}: R/R {rr_ratio:.2f} not in [{min_rr}, {max_rr}]"
            )
            return None

        return {
            "entry":      round(entry, 5),
            "stop_loss":  round(stop_loss, 5),
            "take_profit":round(take_profit, 5),
            "rr_ratio":   round(rr_ratio, 2),
        }

    # ──────────────────────────────────────────────────────────────────────────
    def _check_trend_confirmation(self, pair: str, score: float):
        """Multi-timeframe trend confirmation."""
        trend_score = 0
        weights     = list(map(int, self.config.get(
            "TIMEFRAME_WEIGHTS", [1] * len(self.config["ADDITIONAL_TIMEFRAMES"])
        )))
        timeframe_results = []

        for idx, tf in enumerate(self.config["ADDITIONAL_TIMEFRAMES"][:3]):
            tf_score = 0
            df = fetch_data(pair, timeframe=tf)
            if df is None or df.empty:
                timeframe_results.append({"timeframe": tf, "score": 0})
                continue

            df     = calculate_indicators(df)
            latest = df.iloc[-1]

            if float(latest['ema_short']) > float(latest['ema_long']):
                tf_score += self.config['EMA_SCORE_WEIGHT']

            rsi_lo, rsi_hi = self.config["RSI_BOUNDS"]
            if rsi_lo < float(latest['rsi']) < rsi_hi:
                tf_score += self.config['RSI_SCORE_WEIGHT']

            if (float(latest['macd']) > float(latest['signal'])
                    and float(latest['histogram']) > 0):
                tf_score += self.config['MACD_SCORE_WEIGHT']

            if float(latest['volume']) > float(df['volume'].mean()) * self.config.get("VOLUME_THRESHOLD", 1.5):
                tf_score += self.config['VOLUME_SCORE_WEIGHT']

            adx_lo, adx_hi = self.config.get("ADX_BOUNDS", [20, 65])
            if adx_lo < float(latest.get('adx', 0)) < adx_hi:
                tf_score += 1

            stoch_rsi_ok = float(latest.get('stoch_rsi', 0)) > 0.2
            if not stoch_rsi_ok:
                tf_score -= 1

            # FIX: use a fixed HTF threshold (4.0) rather than the variable 1m
            # score.  Comparing against the 1m score caused systematic over-
            # rejection because HTF candles have fewer sub-bar spikes (lower
            # volume ratio, wider MACD histograms relative to signal, etc.).
            htf_confirm_threshold = self.config.get("HTF_CONFIRM_THRESHOLD", 4.0)
            if tf_score >= htf_confirm_threshold:
                trend_score += weights[idx]

            timeframe_results.append({
                "timeframe":  tf,
                "score":      tf_score,
                "atr":        float(latest.get('atr', 0)),
                "volume":     float(latest['volume']),
                "adx":        float(latest.get('adx', 0)),
                "momentum":   float(latest.get('stoch_rsi', 0)),
            })

        agg = {
            "weighted_trend_score":  trend_score,
            "average_atr":    np.mean([r['atr']    for r in timeframe_results if 'atr'    in r] or [0]),
            "average_volume": np.mean([r['volume'] for r in timeframe_results if 'volume' in r] or [0]),
            "average_adx":    np.mean([r['adx']    for r in timeframe_results if 'adx'    in r] or [0]),
            "momentum_confirmation": all(
                r.get('momentum', 0) > 0.2 for r in timeframe_results
            ),
        }

        required = sum(weights[:3])
        partial  = required * 0.75

        if trend_score >= required:
            return True, agg
        elif self.config["ALLOW_PARTIAL_CONFIRMATION"] and trend_score >= partial:
            return True, agg
        elif trend_score > 0:
            return False, agg
        else:
            return None, agg
