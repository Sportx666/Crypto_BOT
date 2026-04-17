"""
TREND module (primary strategy).

Entry logic (5m candle close):
  - EMA fast > EMA slow (bullish baseline) or fast < slow (bearish)
  - Price breaks above recent swing high (long) or below swing low (short)
  - ADX ≥ trend_min_adx on 5m
  - Confirmation: MACD histogram turning positive (long) or negative (short)

Exit:
  - Hard SL: entry ± ATR * sl_mult
  - Partial TP at 1R (50% close)
  - Trailing stop on remainder: trail_atr_mult * ATR
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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
    tp_partial: float       # 1R target (close partial_close_pct of position)
    trail_distance: float   # ATR * trail_mult – updated on each tick
    atr: float
    r_distance: float       # |entry - sl|
    score: float            # signal quality 0-1
    partial_close_pct: float = 0.5
    reason: str = ""


class TrendStrategy:
    """
    Evaluates trend signals on 5m closed candles.

    Usage:
        signal = strategy.evaluate("ETH", df_5m, regime_result, global_result)
    """

    # Number of lookback bars for swing high/low detection
    SWING_LOOKBACK = 10

    def __init__(self, config: Config) -> None:
        self._cfg = config

    def evaluate(
        self,
        coin: str,
        df: pd.DataFrame,
        regime: RegimeResult,
        global_ctx: GlobalResult,
    ) -> Optional[TrendSignal]:
        """
        Returns a TrendSignal if conditions are met, else None.
        df must be 5m closed bars, most recent last.
        """
        if len(df) < 50:
            return None

        try:
            indicators = ind.compute_all(df, self._cfg.ema_fast, self._cfg.ema_slow)
            vals = ind.last_bar(indicators)
        except Exception as exc:
            log.warning("%s TREND indicator error: %s", coin, exc)
            return None

        close = float(df["close"].iloc[-1])
        adx_val = vals["adx"]
        atr_val = vals["atr"]
        ema_f = vals["ema_fast"]
        ema_s = vals["ema_slow"]
        macd_hist = vals["macd_hist"]
        rsi_val = vals["rsi"]
        plus_di = vals["plus_di"]
        minus_di = vals["minus_di"]

        # ── Gate: ADX ────────────────────────────────────────────────────
        if adx_val < self._cfg.trend_min_adx:
            return None

        # ── Determine direction from regime DMI + EMA alignment ──────────
        long_ema = ema_f > ema_s
        short_ema = ema_f < ema_s

        # Regime direction override
        if regime.trend_direction == 1:
            long_bias = True
            short_bias = False
        elif regime.trend_direction == -1:
            long_bias = False
            short_bias = True
        else:
            long_bias = long_ema
            short_bias = short_ema

        # ── Global bias filter (avoid trading against strong BTC trend) ──
        if global_ctx.bias == GlobalBias.BEAR and long_bias and not short_bias:
            log.debug("%s TREND: skipping long – BTC global BEAR", coin)
            return None
        if global_ctx.bias == GlobalBias.BULL and short_bias and not long_bias:
            log.debug("%s TREND: skipping short – BTC global BULL", coin)
            return None

        # ── Swing breakout detection ──────────────────────────────────────
        highs = df["high"].iloc[-self.SWING_LOOKBACK - 1 : -1]
        lows = df["low"].iloc[-self.SWING_LOOKBACK - 1 : -1]
        swing_high = float(highs.max())
        swing_low = float(lows.min())

        long_breakout = long_bias and close > swing_high
        short_breakout = short_bias and close < swing_low

        if not long_breakout and not short_breakout:
            return None

        direction = 1 if long_breakout else -1

        # ── MACD confirmation ─────────────────────────────────────────────
        if direction == 1 and macd_hist <= 0:
            return None
        if direction == -1 and macd_hist >= 0:
            return None

        # ── RSI filter (avoid extreme overbought/sold on entry) ───────────
        if direction == 1 and rsi_val > 80:
            return None
        if direction == -1 and rsi_val < 20:
            return None

        # ── Compute levels ────────────────────────────────────────────────
        sl_dist = atr_val * self._cfg.trend_atr_sl_mult
        sl = close - direction * sl_dist
        r_dist = abs(close - sl)
        tp_partial = close + direction * r_dist * self._cfg.trend_partial_tp_r
        trail_dist = atr_val * self._cfg.trend_trail_atr_mult

        # ── Score ─────────────────────────────────────────────────────────
        score = _trend_score(adx_val, rsi_val, macd_hist, plus_di, minus_di, direction)

        reason_parts = [
            f"{'LONG' if direction==1 else 'SHORT'} breakout",
            f"adx={adx_val:.1f}",
            f"rsi={rsi_val:.1f}",
            f"macd_hist={macd_hist:.4f}",
        ]

        signal = TrendSignal(
            coin=coin,
            direction=direction,
            entry=close,
            sl=sl,
            tp_partial=tp_partial,
            trail_distance=trail_dist,
            atr=atr_val,
            r_distance=r_dist,
            score=score,
            partial_close_pct=self._cfg.trend_partial_close_pct,
            reason=" | ".join(reason_parts),
        )
        log.info(
            "TREND signal %s %s  entry=%.4f sl=%.4f tp1=%.4f score=%.2f  %s",
            coin,
            "LONG" if direction == 1 else "SHORT",
            close,
            sl,
            tp_partial,
            score,
            signal.reason,
        )
        return signal


def _trend_score(
    adx: float,
    rsi: float,
    macd_hist: float,
    plus_di: float,
    minus_di: float,
    direction: int,
) -> float:
    """Composite 0–1 score for signal quality."""
    s = 0.0

    # ADX strength (0-40 → 0-0.4)
    s += min(adx / 100, 0.4)

    # RSI momentum in sweet spot
    if direction == 1:
        s += 0.2 if 50 < rsi < 70 else 0.05
    else:
        s += 0.2 if 30 < rsi < 50 else 0.05

    # MACD histogram magnitude
    if abs(macd_hist) > 0:
        s += min(abs(macd_hist) * 100, 0.2)

    # DMI separation
    di_sep = abs(plus_di - minus_di)
    s += min(di_sep / 100, 0.2)

    return min(s, 1.0)
