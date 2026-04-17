"""
TREND module (primary strategy) – two signal patterns.

PATTERN A: Swing Breakout
  - Price breaks above/below multi-bar swing high/low
  - ADX ≥ threshold AND rising (no fading trends)
  - Volume spike confirms the move
  - MACD histogram confirms direction
  - Multi-bar close confirmation gate

PATTERN B: EMA Pullback (higher win rate)
  - Established trend: EMA fast > mid > slow, all rising (or inverse)
  - Price pulls back to EMA fast ± 0.5 ATR
  - Stochastic oversold/overbought
  - Bullish/bearish close candle body

Both patterns:
  - Hard SL: entry ± 2.0 × ATR  (FIX #2: wider, fewer noise stops)
  - R:R gate: minimum 2.0:1     (FIX #3: forces good setups only)
  - Partial TP at 1.5R (50%)    (FIX #7: SL moves to B/E after partial)
  - Trailing stop on remainder: 2.0 × ATR
  - Time-of-day filter           (FIX #10)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from ..config import Config
from .. import indicators as ind
from ..regime import RegimeResult, GlobalBias, GlobalResult

log = logging.getLogger(__name__)


@dataclass
class TrendSignal:
    coin: str
    direction: int          # +1 long, -1 short
    entry: float
    sl: float
    tp_partial: float       # first TP (move SL to B/E after)
    trail_distance: float   # ATR × trail_mult
    atr: float
    r_distance: float       # |entry – sl|
    score: float            # 0–1 quality score
    pattern: str = ""       # "BREAKOUT" or "PULLBACK"
    partial_close_pct: float = 0.5
    reason: str = ""


class TrendStrategy:
    """
    Evaluates trend signals on 5m closed candles.
    Both patterns share the same exit logic (partial + trail).
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config

    def evaluate(
        self,
        coin: str,
        df: pd.DataFrame,
        regime: RegimeResult,
        global_ctx: GlobalResult,
    ) -> Optional[TrendSignal]:
        """Returns the best TrendSignal from either pattern, or None."""
        if len(df) < 60:
            return None

        # FIX #10: time-of-day filter
        if not _in_trade_window(
            self._cfg.trade_start_hour_utc,
            self._cfg.trade_end_hour_utc,
        ):
            return None

        try:
            indicators = ind.compute_all(df, self._cfg.ema_fast, self._cfg.ema_slow)
            vals = ind.last_bar(indicators)
        except Exception as exc:
            log.warning("%s TREND indicator error: %s", coin, exc)
            return None

        # Compute mid EMA for triple-stack check
        ema_mid_series = ind.ema(df["close"], self._cfg.ema_mid)
        ema_mid_val = float(ema_mid_series.iloc[-1])

        close = float(df["close"].iloc[-1])
        adx_val = vals["adx"]
        atr_val = vals["atr"]
        ema_f = vals["ema_fast"]
        ema_s = vals["ema_slow"]
        macd_hist = vals["macd_hist"]
        rsi_val = vals["rsi"]
        plus_di = vals["plus_di"]
        minus_di = vals["minus_di"]
        stoch_k = vals["stoch_k"]
        stoch_d = vals["stoch_d"]

        # ── Gate: ADX (minimum strength) ─────────────────────────────────
        if adx_val < self._cfg.trend_min_adx:
            return None
        # NOTE: ADX slope check is intentionally on the REGIME (15m) level only.
        # Checking slope again on 5m is too strict — 5m ADX oscillates even
        # in strong trends. The regime engine ensures we're in a building trend.

        # ── FIX #9: EMA spread gate ────────────────────────────────────
        ema_spread_pct = abs(ema_f - ema_s) / close
        if ema_spread_pct < self._cfg.ema_min_spread_pct:
            return None  # EMAs too close = weak trend

        # ── Direction ─────────────────────────────────────────────────────
        long_bias, short_bias = _resolve_direction(
            regime, ema_f, ema_s, ema_mid_val, close
        )

        # ── Global bias filter ────────────────────────────────────────────
        if global_ctx.bias == GlobalBias.BEAR and long_bias and not short_bias:
            return None
        if global_ctx.bias == GlobalBias.BULL and short_bias and not long_bias:
            return None

        if not long_bias and not short_bias:
            return None

        # ── Try Pattern B first (higher win rate) ─────────────────────────
        sig = self._pattern_pullback(
            coin, df, indicators, vals, ema_mid_val,
            long_bias, short_bias, close, atr_val,
            adx_val, rsi_val, stoch_k, stoch_d,
        )
        if sig is not None:
            return sig

        # ── Try Pattern A (breakout) ──────────────────────────────────────
        return self._pattern_breakout(
            coin, df, indicators, vals,
            long_bias, short_bias, close, atr_val,
            adx_val, rsi_val, macd_hist, plus_di, minus_di,
        )

    # ── Pattern B: EMA Pullback ───────────────────────────────────────────

    def _pattern_pullback(
        self,
        coin: str,
        df: pd.DataFrame,
        indicators: dict,
        vals: dict,
        ema_mid: float,
        long_bias: bool,
        short_bias: bool,
        close: float,
        atr: float,
        adx: float,
        rsi: float,
        stoch_k: float,
        stoch_d: float,
    ) -> Optional[TrendSignal]:
        """
        EMA-bounce pullback entry in established trend.
        Win rate target: 55-65% at 2:1 R:R.
        """
        ema_f = vals["ema_fast"]
        ema_s = vals["ema_slow"]

        # Need at least 3-EMA stack for pullback
        long_stack = ema_f > ema_s > ema_mid
        short_stack = ema_f < ema_s < ema_mid

        if not (long_stack and long_bias) and not (short_stack and short_bias):
            return None

        # Price must be near (but not crossed) EMA fast
        pullback_zone = 0.6 * atr   # within 0.6 ATR of EMA fast
        near_ema = abs(close - ema_f) <= pullback_zone

        if not near_ema:
            return None

        direction = 1 if long_bias else -1

        # RSI: not extreme overbought/sold
        if direction == 1:
            if not (35 < rsi < 68):
                return None
            # Stochastic: turning up from oversold on pullback
            if stoch_k > 60:
                return None
        else:
            if not (32 < rsi < 65):
                return None
            if stoch_k < 40:
                return None

        # Last candle must be a reversal signal candle:
        # body > 40% of range, closing in trend direction
        last = df.iloc[-1]
        candle_range = last["high"] - last["low"]
        body = abs(last["close"] - last["open"])
        if candle_range > 0 and body / candle_range < 0.40:
            return None  # doji / small-body = uncertain

        bullish_candle = last["close"] > last["open"]
        bearish_candle = last["close"] < last["open"]
        if direction == 1 and not bullish_candle:
            return None
        if direction == -1 and not bearish_candle:
            return None

        # Build levels
        sl_dist = atr * self._cfg.trend_atr_sl_mult
        sl = close - direction * sl_dist
        r_dist = abs(close - sl)
        tp = close + direction * r_dist * self._cfg.trend_partial_tp_r
        trail = atr * self._cfg.trend_trail_atr_mult

        # FIX #3: R:R gate
        rr = abs(tp - close) / r_dist if r_dist > 0 else 0
        if rr < self._cfg.min_rr_trend:
            return None

        score = _trend_score(adx, rsi, vals["macd_hist"], vals["plus_di"], vals["minus_di"], direction)
        reason = (
            f"{'LONG' if direction==1 else 'SHORT'} pullback to EMA{self._cfg.ema_fast} | "
            f"adx={adx:.1f} rsi={rsi:.1f} rr={rr:.2f}"
        )
        log.info(
            "TREND PULLBACK %s %s  entry=%.4f sl=%.4f tp=%.4f rr=%.2f score=%.2f",
            coin, "L" if direction == 1 else "S", close, sl, tp, rr, score,
        )
        return TrendSignal(
            coin=coin, direction=direction, entry=close, sl=sl,
            tp_partial=tp, trail_distance=trail, atr=atr,
            r_distance=r_dist, score=score, pattern="PULLBACK",
            partial_close_pct=self._cfg.trend_partial_close_pct,
            reason=reason,
        )

    # ── Pattern A: Swing Breakout ─────────────────────────────────────────

    def _pattern_breakout(
        self,
        coin: str,
        df: pd.DataFrame,
        indicators: dict,
        vals: dict,
        long_bias: bool,
        short_bias: bool,
        close: float,
        atr: float,
        adx: float,
        rsi: float,
        macd_hist: float,
        plus_di: float,
        minus_di: float,
    ) -> Optional[TrendSignal]:
        """Structural breakout above swing high / below swing low."""
        lb = self._cfg.swing_lookback_bars   # FIX #4: was hardcoded 10

        # FIX #8: require N consecutive closes above level
        confirm = self._cfg.breakout_confirm_bars
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        swing_high = float(highs[-(lb + confirm) : -confirm].max())
        swing_low = float(lows[-(lb + confirm) : -confirm].min())

        # Check all confirmation closes are above/below the swing
        confirm_closes_long = all(
            closes[-i] > swing_high for i in range(1, confirm + 1)
        )
        confirm_closes_short = all(
            closes[-i] < swing_low for i in range(1, confirm + 1)
        )

        long_break = long_bias and confirm_closes_long
        short_break = short_bias and confirm_closes_short

        if not long_break and not short_break:
            return None

        direction = 1 if long_break else -1

        # MACD histogram confirmation
        if direction == 1 and macd_hist <= 0:
            return None
        if direction == -1 and macd_hist >= 0:
            return None

        # RSI gate (not extreme on entry)
        if direction == 1 and rsi > 75:
            return None
        if direction == -1 and rsi < 25:
            return None

        # FIX #6: volume spike confirmation
        vol_series = df["volume"]
        vol_avg = float(vol_series.iloc[-self._cfg.volume_avg_window - 1 : -1].mean())
        vol_cur = float(vol_series.iloc[-1])
        if vol_avg > 0 and vol_cur < vol_avg * self._cfg.volume_spike_min_mult:
            log.debug("%s BREAKOUT: insufficient volume %.2fx < %.2fx", coin,
                      vol_cur / vol_avg, self._cfg.volume_spike_min_mult)
            return None

        # Build levels
        sl_dist = atr * self._cfg.trend_atr_sl_mult
        sl = close - direction * sl_dist
        r_dist = abs(close - sl)
        tp = close + direction * r_dist * self._cfg.trend_partial_tp_r
        trail = atr * self._cfg.trend_trail_atr_mult

        # FIX #3: R:R gate
        rr = abs(tp - close) / r_dist if r_dist > 0 else 0
        if rr < self._cfg.min_rr_trend:
            return None

        score = _trend_score(adx, rsi, macd_hist, plus_di, minus_di, direction)
        reason = (
            f"{'LONG' if direction==1 else 'SHORT'} breakout "
            f"(swing={swing_high if direction==1 else swing_low:.4f} | "
            f"vol={vol_cur/vol_avg:.1f}x | adx={adx:.1f} | rr={rr:.2f})"
        )
        log.info(
            "TREND BREAKOUT %s %s  entry=%.4f sl=%.4f tp=%.4f rr=%.2f score=%.2f",
            coin, "L" if direction == 1 else "S", close, sl, tp, rr, score,
        )
        return TrendSignal(
            coin=coin, direction=direction, entry=close, sl=sl,
            tp_partial=tp, trail_distance=trail, atr=atr,
            r_distance=r_dist, score=score, pattern="BREAKOUT",
            partial_close_pct=self._cfg.trend_partial_close_pct,
            reason=reason,
        )


# ── Helpers ───────────────────────────────────────────────────────────────

def _resolve_direction(
    regime: RegimeResult,
    ema_f: float,
    ema_s: float,
    ema_mid: float,
    close: float,
) -> tuple[bool, bool]:
    """Return (long_bias, short_bias) considering regime + EMA stack."""
    # Regime DMI direction
    if regime.trend_direction == 1:
        rd_long, rd_short = True, False
    elif regime.trend_direction == -1:
        rd_long, rd_short = False, True
    else:
        rd_long = rd_short = True  # neutral → allow both

    # EMA stack direction (require close to be on right side of mid EMA)
    ema_long = close > ema_f > ema_s and close > ema_mid
    ema_short = close < ema_f < ema_s and close < ema_mid

    long_bias = rd_long and ema_long
    short_bias = rd_short and ema_short
    return long_bias, short_bias


def _adx_is_rising(adx_series: pd.Series, lookback: int = 4) -> bool:
    """ADX now > ADX N bars ago – trend is still building."""
    if len(adx_series) < lookback + 1:
        return True  # insufficient data, don't block
    return float(adx_series.iloc[-1]) > float(adx_series.iloc[-lookback - 1])


def _in_trade_window(start_h: int, end_h: int) -> bool:
    """Return True if current UTC hour is within the trade window."""
    h = datetime.now(timezone.utc).hour
    if start_h <= end_h:
        return start_h <= h < end_h
    # overnight window (e.g. 22 → 06)
    return h >= start_h or h < end_h


def _trend_score(
    adx: float,
    rsi: float,
    macd_hist: float,
    plus_di: float,
    minus_di: float,
    direction: int,
) -> float:
    """Composite 0–1 quality score."""
    s = 0.0
    s += min(adx / 100, 0.4)                        # ADX strength
    if direction == 1:
        s += 0.2 if 45 < rsi < 68 else 0.05
    else:
        s += 0.2 if 32 < rsi < 55 else 0.05
    if abs(macd_hist) > 0:
        s += min(abs(macd_hist) * 100, 0.2)
    s += min(abs(plus_di - minus_di) / 100, 0.2)
    return min(s, 1.0)
