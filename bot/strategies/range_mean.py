"""
RANGE module (secondary strategy) – VWAP / mid-band mean reversion.

Entry:
  - Price touches VWAP ± (band_atr_mult × ATR) → fade to VWAP
  - ADX < range_max_adx (confirmed ranging market)
  - RSI < 35 for long fade, RSI > 65 for short fade
  - Stochastic %K turning from extreme

Exit:
  - TP at VWAP (mean-reversion target)
  - SL: entry ± range_atr_sl_mult × ATR
  - Prefer limit/maker entries for favourable fills
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
class RangeSignal:
    coin: str
    direction: int          # +1 long fade, -1 short fade
    entry: float
    sl: float
    tp: float               # VWAP level
    atr: float
    r_distance: float
    score: float
    is_limit: bool = True   # prefer limit order
    reason: str = ""


class RangeStrategy:
    """
    Evaluates mean-reversion signals on 5m closed candles.
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config

    def evaluate(
        self,
        coin: str,
        df: pd.DataFrame,
        regime: RegimeResult,
        global_ctx: GlobalResult,
    ) -> Optional[RangeSignal]:
        """Returns a RangeSignal if conditions are met, else None."""
        if len(df) < 50:
            return None

        try:
            indicators = ind.compute_all(df, self._cfg.ema_fast, self._cfg.ema_slow)
            vals = ind.last_bar(indicators)
        except Exception as exc:
            log.warning("%s RANGE indicator error: %s", coin, exc)
            return None

        close = float(df["close"].iloc[-1])
        atr_val = vals["atr"]
        adx_val = vals["adx"]
        rsi_val = vals["rsi"]
        vwap_val = vals["vwap"]
        stoch_k = vals["stoch_k"]
        stoch_d = vals["stoch_d"]
        bb_upper = vals["bb_upper"]
        bb_lower = vals["bb_lower"]

        # ── Gate: ADX must be low (confirming range) ─────────────────────
        if adx_val > self._cfg.range_max_adx:
            return None

        # ── Gate: VWAP available ──────────────────────────────────────────
        if vwap_val <= 0 or not _is_finite(vwap_val):
            return None

        # ── Band levels ───────────────────────────────────────────────────
        band = atr_val * self._cfg.range_vwap_band_atr_mult
        lower_band = vwap_val - band
        upper_band = vwap_val + band

        # ── Global bias: in BEAR global, only take short fades; BULL → only longs
        if global_ctx.bias == GlobalBias.BEAR:
            allow_long = False
            allow_short = True
        elif global_ctx.bias == GlobalBias.BULL:
            allow_long = True
            allow_short = False
        else:
            allow_long = True
            allow_short = True

        # ── Long fade: price near or below lower band ──────────────────────
        long_touch = close <= lower_band * 1.002 or close <= bb_lower * 1.002
        short_touch = close >= upper_band * 0.998 or close >= bb_upper * 0.998

        signal: Optional[RangeSignal] = None

        if long_touch and allow_long and rsi_val < 38:
            # Stochastic turning up from oversold
            if stoch_k > stoch_d or stoch_k < 25:
                direction = 1
                entry = close
                sl = entry - atr_val * self._cfg.range_atr_sl_mult
                tp = vwap_val  # target: revert to mean
                r_dist = abs(entry - sl)

                # Minimum R:R check
                rr = abs(tp - entry) / r_dist if r_dist > 0 else 0
                if rr < self._cfg.range_tp_r * 0.8:
                    return None  # too close to VWAP

                score = _range_score(rsi_val, stoch_k, adx_val, direction)
                signal = RangeSignal(
                    coin=coin,
                    direction=direction,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    atr=atr_val,
                    r_distance=r_dist,
                    score=score,
                    is_limit=True,
                    reason=f"LONG fade rsi={rsi_val:.1f} stoch_k={stoch_k:.1f} adx={adx_val:.1f}",
                )

        elif short_touch and allow_short and rsi_val > 62:
            # Stochastic turning down from overbought
            if stoch_k < stoch_d or stoch_k > 75:
                direction = -1
                entry = close
                sl = entry + atr_val * self._cfg.range_atr_sl_mult
                tp = vwap_val
                r_dist = abs(entry - sl)

                rr = abs(tp - entry) / r_dist if r_dist > 0 else 0
                if rr < self._cfg.range_tp_r * 0.8:
                    return None

                score = _range_score(rsi_val, stoch_k, adx_val, direction)
                signal = RangeSignal(
                    coin=coin,
                    direction=direction,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    atr=atr_val,
                    r_distance=r_dist,
                    score=score,
                    is_limit=True,
                    reason=f"SHORT fade rsi={rsi_val:.1f} stoch_k={stoch_k:.1f} adx={adx_val:.1f}",
                )

        if signal:
            log.info(
                "RANGE signal %s %s  entry=%.4f sl=%.4f tp=%.4f score=%.2f  %s",
                coin,
                "LONG" if signal.direction == 1 else "SHORT",
                signal.entry,
                signal.sl,
                signal.tp,
                signal.score,
                signal.reason,
            )
        return signal


def _range_score(rsi: float, stoch_k: float, adx: float, direction: int) -> float:
    s = 0.0
    # Lower ADX = better range conditions
    s += max(0.3 - adx / 100, 0.0) * 2

    if direction == 1:
        # More oversold = better
        s += max((35 - rsi) / 35, 0.0) * 0.4
        s += max((25 - stoch_k) / 25, 0.0) * 0.3
    else:
        s += max((rsi - 65) / 35, 0.0) * 0.4
        s += max((stoch_k - 75) / 25, 0.0) * 0.3

    return min(s, 1.0)


def _is_finite(v: float) -> bool:
    import math
    return math.isfinite(v)
