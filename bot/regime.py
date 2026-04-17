"""
Regime detection engine.

Per-market (15m timeframe):
  TREND   – ADX ≥ threshold AND ADX rising AND ATR% in range
  RANGE   – ADX ≤ range_max AND ADX flat/declining AND ATR% sufficient
  NO_TRADE – too volatile, too quiet, ADX ambiguous, or trend fading

Global (BTC 1h timeframe, triple-EMA stack):
  BULL   – price > EMA9 > EMA21 > EMA50 AND RSI > 52
  BEAR   – price < EMA9 < EMA21 < EMA50 AND RSI < 48
  NEUTRAL – anything else (allow both directions, reduced size optional)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from .candles import CandleCache
from .config import Config
from . import indicators as ind

log = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    NO_TRADE = "NO_TRADE"


class GlobalBias(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


@dataclass
class RegimeResult:
    coin: str
    regime: MarketRegime
    adx: float
    atr_pct: float
    plus_di: float
    minus_di: float
    trend_direction: int   # +1 long, -1 short, 0 neutral
    adx_rising: bool = False


@dataclass
class GlobalResult:
    bias: GlobalBias
    ema_fast: float
    ema_slow: float
    ema_mid: float
    rsi: float
    close: float


class RegimeEngine:
    """
    Classifies each market's regime from the 15m candle cache.

    Usage:
        engine = RegimeEngine(config, candle_cache)
        result = engine.classify("ETH")
    """

    def __init__(self, config: Config, cache: CandleCache) -> None:
        self._cfg = config
        self._cache = cache
        self._last_global: Optional[GlobalResult] = None

    # ── Global BTC filter (triple-EMA stack) ─────────────────────────────

    def global_bias(self) -> GlobalResult:
        """
        FIX: Use triple-EMA (9/21/50) stack for stronger global filter.
        Two-EMA crossover is too sensitive; triple stack requires real trend.
        """
        df = self._cache.get(self._cfg.global_symbol, self._cfg.global_tf, n=150)
        if df is None or len(df) < 55:
            log.debug("Not enough BTC 1h data → NEUTRAL bias")
            result = GlobalResult(
                bias=GlobalBias.NEUTRAL,
                ema_fast=0.0, ema_slow=0.0, ema_mid=0.0,
                rsi=50.0, close=0.0,
            )
            self._last_global = result
            return result

        close_s = df["close"]
        ema9 = float(ind.ema(close_s, 9).iloc[-1])
        ema21 = float(ind.ema(close_s, 21).iloc[-1])
        ema50 = float(ind.ema(close_s, 50).iloc[-1])
        rsi_s = ind.rsi(close_s)
        _rsi = float(rsi_s.iloc[-1])
        close = float(close_s.iloc[-1])

        # Strict triple-stack: all 3 EMAs must be aligned
        if close > ema9 > ema21 > ema50 and _rsi > 52:
            bias = GlobalBias.BULL
        elif close < ema9 < ema21 < ema50 and _rsi < 48:
            bias = GlobalBias.BEAR
        else:
            bias = GlobalBias.NEUTRAL

        result = GlobalResult(
            bias=bias,
            ema_fast=ema9, ema_slow=ema21, ema_mid=ema50,
            rsi=_rsi, close=close,
        )
        self._last_global = result
        log.debug(
            "Global BTC bias: %s  ema9=%.0f ema21=%.0f ema50=%.0f rsi=%.1f",
            bias.value, ema9, ema21, ema50, _rsi,
        )
        return result

    # ── Per-market regime ─────────────────────────────────────────────────

    def classify(self, coin: str) -> RegimeResult:
        """
        Classify the regime using 15m data.
        Returns NO_TRADE on any ambiguity.
        """
        df = self._cache.get(coin, self._cfg.regime_tf, n=150)

        if df is None or len(df) < 35:
            log.debug("%s: insufficient 15m data → NO_TRADE", coin)
            return _no_trade(coin)

        try:
            indicators = ind.compute_all(df)
            vals = ind.last_bar(indicators)
        except Exception as exc:
            log.warning("%s: indicator error → NO_TRADE (%s)", coin, exc)
            return _no_trade(coin)

        _adx = vals["adx"]
        _atr_pct = vals["atr_pct"]
        plus_di = vals["plus_di"]
        minus_di = vals["minus_di"]

        # ── Volatility gate ──────────────────────────────────────────────
        if _atr_pct < self._cfg.atr_pct_min or _atr_pct > self._cfg.atr_pct_max:
            return RegimeResult(
                coin=coin, regime=MarketRegime.NO_TRADE,
                adx=_adx, atr_pct=_atr_pct,
                plus_di=plus_di, minus_di=minus_di,
                trend_direction=0, adx_rising=False,
            )

        # FIX #5: check ADX slope
        adx_series = indicators["adx"]
        lb = self._cfg.adx_slope_lookback
        adx_rising = (
            len(adx_series) > lb + 1
            and float(adx_series.iloc[-1]) > float(adx_series.iloc[-lb - 1])
        )

        # ── Regime classification ────────────────────────────────────────
        if _adx >= self._cfg.adx_trend_threshold:
            # Only classify as TREND if ADX is still rising
            regime = MarketRegime.TREND if adx_rising else MarketRegime.NO_TRADE
        elif _adx < self._cfg.range_max_adx:
            # For RANGE: ADX must NOT be sharply rising (would become breakout)
            adx_prev = float(adx_series.iloc[-lb - 1]) if len(adx_series) > lb + 1 else _adx
            regime = MarketRegime.RANGE if (_adx - adx_prev) < 3 else MarketRegime.NO_TRADE
        else:
            regime = MarketRegime.NO_TRADE

        # ── Directional bias ─────────────────────────────────────────────
        if plus_di > minus_di + 5:
            direction = 1
        elif minus_di > plus_di + 5:
            direction = -1
        else:
            direction = 0

        log.debug(
            "%s regime=%s adx=%.1f(rising=%s) atr_pct=%.3f +DI=%.1f -DI=%.1f dir=%+d",
            coin, regime.value, _adx, "Y" if adx_rising else "N",
            _atr_pct, plus_di, minus_di, direction,
        )
        return RegimeResult(
            coin=coin, regime=regime, adx=_adx, atr_pct=_atr_pct,
            plus_di=plus_di, minus_di=minus_di,
            trend_direction=direction, adx_rising=adx_rising,
        )

    def classify_many(self, coins: list[str]) -> Dict[str, RegimeResult]:
        return {coin: self.classify(coin) for coin in coins}

    @staticmethod
    def is_tradeable(result: RegimeResult) -> bool:
        return result.regime != MarketRegime.NO_TRADE


# ── Helpers ───────────────────────────────────────────────────────────────

def _no_trade(coin: str) -> RegimeResult:
    return RegimeResult(
        coin=coin, regime=MarketRegime.NO_TRADE,
        adx=0.0, atr_pct=0.0, plus_di=0.0, minus_di=0.0,
        trend_direction=0, adx_rising=False,
    )
