"""
RANGE module (secondary strategy) – VWAP / BB band mean reversion.

Entry conditions (5m candle close):
  - ADX < range_max_adx  AND  ADX is declining or flat (not breaking out)
  - Price PIERCES the band (closes outside), then snaps back OR
    closes at the extreme band with reversal candle
  - RSI extreme: < 32 for long, > 68 for short
  - Stochastic confirming turn
  - Volume LOW on approach (contraction = range health)
  - Minimum R:R 1.5:1 to VWAP

Exit:
  - TP at VWAP (mean-reversion target)
  - SL: ATR × 1.2 beyond entry
  - Prefer limit/maker entry for better fill
  - Time-of-day filter applied
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from ..config import Config
from .. import indicators as ind
from ..regime import RegimeResult, GlobalBias, GlobalResult
from .trend import _in_trade_window

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
    is_limit: bool = True
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

        # ── Gate: ADX must be below range_max AND declining ─────────────
        if adx_val > self._cfg.range_max_adx:
            return None

        adx_series = indicators["adx"]
        if len(adx_series) >= 4:
            adx_prev = float(adx_series.iloc[-4])
            # ADX still rising sharply = breakout risk, skip range
            if adx_val > adx_prev + 3:
                return None

        # ── Gate: VWAP must be valid and inside the band ─────────────────
        if not _is_finite(vwap_val) or vwap_val <= 0:
            return None

        # Band levels
        band = atr_val * self._cfg.range_vwap_band_atr_mult
        lower_band = vwap_val - band
        upper_band = vwap_val + band

        # VWAP must be between the two BB bands (range-healthy)
        if not (bb_lower < vwap_val < bb_upper):
            return None

        # ── Global bias ────────────────────────────────────────────────
        if global_ctx.bias == GlobalBias.BEAR:
            allow_long, allow_short = False, True
        elif global_ctx.bias == GlobalBias.BULL:
            allow_long, allow_short = True, False
        else:
            allow_long, allow_short = True, True

        # ── Volume check: low volume confirms range (not breakout) ────────
        vol_series = df["volume"]
        vol_avg = float(vol_series.iloc[-self._cfg.volume_avg_window - 1 : -1].mean())
        vol_cur = float(vol_series.iloc[-1])
        # High volume on this candle → possible breakout, skip
        if vol_avg > 0 and vol_cur > vol_avg * 2.0:
            log.debug("%s RANGE: high volume (%.1fx) – possible breakout, skipping", coin,
                      vol_cur / vol_avg)
            return None

        signal: Optional[RangeSignal] = None

        # ── Long fade: price has pierced lower band ───────────────────────
        if allow_long and rsi_val < 32 and close <= lower_band:
            # FIX: require reversal body (close > open = bullish engulf at extreme)
            last = df.iloc[-1]
            prev = df.iloc[-2]
            bullish_close = last["close"] > last["open"]
            oversold_bounce = stoch_k < 30 or stoch_k > stoch_d  # %K turning up

            if bullish_close and oversold_bounce:
                direction = 1
                entry = close
                sl = entry - atr_val * self._cfg.range_atr_sl_mult
                tp = vwap_val
                r_dist = abs(entry - sl)
                rr = abs(tp - entry) / r_dist if r_dist > 0 else 0

                # FIX #3: R:R gate
                if rr < self._cfg.min_rr_range:
                    return None

                score = _range_score(rsi_val, stoch_k, adx_val, direction)
                signal = RangeSignal(
                    coin=coin, direction=direction, entry=entry,
                    sl=sl, tp=tp, atr=atr_val, r_distance=r_dist,
                    score=score, is_limit=True,
                    reason=f"LONG fade rsi={rsi_val:.1f} stoch={stoch_k:.0f} adx={adx_val:.1f} rr={rr:.2f}",
                )

        # ── Short fade: price has pierced upper band ──────────────────────
        elif allow_short and rsi_val > 68 and close >= upper_band:
            last = df.iloc[-1]
            bearish_close = last["close"] < last["open"]
            overbought_fade = stoch_k > 70 or stoch_k < stoch_d  # %K turning down

            if bearish_close and overbought_fade:
                direction = -1
                entry = close
                sl = entry + atr_val * self._cfg.range_atr_sl_mult
                tp = vwap_val
                r_dist = abs(entry - sl)
                rr = abs(tp - entry) / r_dist if r_dist > 0 else 0

                if rr < self._cfg.min_rr_range:
                    return None

                score = _range_score(rsi_val, stoch_k, adx_val, direction)
                signal = RangeSignal(
                    coin=coin, direction=direction, entry=entry,
                    sl=sl, tp=tp, atr=atr_val, r_distance=r_dist,
                    score=score, is_limit=True,
                    reason=f"SHORT fade rsi={rsi_val:.1f} stoch={stoch_k:.0f} adx={adx_val:.1f} rr={rr:.2f}",
                )

        if signal:
            log.info(
                "RANGE signal %s %s  entry=%.4f sl=%.4f tp=%.4f score=%.2f  %s",
                coin, "LONG" if signal.direction == 1 else "SHORT",
                signal.entry, signal.sl, signal.tp, signal.score, signal.reason,
            )
        return signal


def _range_score(rsi: float, stoch_k: float, adx: float, direction: int) -> float:
    s = 0.0
    s += max(0.3 - adx / 100, 0.0) * 2  # lower ADX = better ranging
    if direction == 1:
        s += max((32 - rsi) / 32, 0.0) * 0.4
        s += max((30 - stoch_k) / 30, 0.0) * 0.3
    else:
        s += max((rsi - 68) / 32, 0.0) * 0.4
        s += max((stoch_k - 70) / 30, 0.0) * 0.3
    return min(s, 1.0)


def _is_finite(v: float) -> bool:
    return math.isfinite(v)
