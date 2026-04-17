"""
Regime detection engine.

Per-market (15m timeframe):
  TREND   – ADX above threshold AND ATR% in tradable range → trend module
  RANGE   – ADX below range_max AND ATR% sufficient → range module
  NO_TRADE – too volatile, too quiet, or ambiguous

Global (BTC 1h timeframe):
  BULL   – price > EMA-21 AND RSI > 50
  BEAR   – price < EMA-21 AND RSI < 50
  NEUTRAL
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
    trend_direction: int  # +1 long bias, -1 short bias, 0 neutral


@dataclass
class GlobalResult:
    bias: GlobalBias
    ema_fast: float
    ema_slow: float
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

    # ── Global BTC filter ─────────────────────────────────────────────────

    def global_bias(self) -> GlobalResult:
        """Compute global BTC 1h bias. Cached in _last_global."""
        df = self._cache.get(self._cfg.global_symbol, self._cfg.global_tf, n=100)
        if df is None or len(df) < 30:
            log.debug("Not enough BTC 1h data for global bias – returning NEUTRAL")
            result = GlobalResult(
                bias=GlobalBias.NEUTRAL,
                ema_fast=0.0,
                ema_slow=0.0,
                rsi=50.0,
                close=0.0,
            )
            self._last_global = result
            return result

        indicators = ind.compute_all(df, cfg_ema_fast=9, cfg_ema_slow=21)
        vals = ind.last_bar(indicators)
        close = float(df["close"].iloc[-1])
        _rsi = vals["rsi"]
        ema_f = vals["ema_fast"]
        ema_s = vals["ema_slow"]

        if close > ema_f > ema_s and _rsi > 52:
            bias = GlobalBias.BULL
        elif close < ema_f < ema_s and _rsi < 48:
            bias = GlobalBias.BEAR
        else:
            bias = GlobalBias.NEUTRAL

        result = GlobalResult(
            bias=bias,
            ema_fast=ema_f,
            ema_slow=ema_s,
            rsi=_rsi,
            close=close,
        )
        self._last_global = result
        log.debug(
            "Global BTC bias: %s  ema_f=%.2f ema_s=%.2f rsi=%.1f",
            bias.value,
            ema_f,
            ema_s,
            _rsi,
        )
        return result

    # ── Per-market regime ─────────────────────────────────────────────────

    def classify(self, coin: str) -> RegimeResult:
        """
        Classify the regime for `coin` using the 15m candle cache.
        Falls back to NO_TRADE if insufficient data.
        """
        df = self._cache.get(coin, self._cfg.regime_tf, n=100)

        if df is None or len(df) < 30:
            log.debug("%s: insufficient 15m data → NO_TRADE", coin)
            return RegimeResult(
                coin=coin,
                regime=MarketRegime.NO_TRADE,
                adx=0.0,
                atr_pct=0.0,
                plus_di=0.0,
                minus_di=0.0,
                trend_direction=0,
            )

        try:
            indicators = ind.compute_all(df)
            vals = ind.last_bar(indicators)
        except Exception as exc:
            log.warning("%s: indicator error → NO_TRADE (%s)", coin, exc)
            return RegimeResult(
                coin=coin,
                regime=MarketRegime.NO_TRADE,
                adx=0.0,
                atr_pct=0.0,
                plus_di=0.0,
                minus_di=0.0,
                trend_direction=0,
            )

        _adx = vals["adx"]
        _atr_pct = vals["atr_pct"]
        plus_di = vals["plus_di"]
        minus_di = vals["minus_di"]

        # ── Volatility gate ──────────────────────────────────────────────
        if _atr_pct < self._cfg.atr_pct_min or _atr_pct > self._cfg.atr_pct_max:
            return RegimeResult(
                coin=coin,
                regime=MarketRegime.NO_TRADE,
                adx=_adx,
                atr_pct=_atr_pct,
                plus_di=plus_di,
                minus_di=minus_di,
                trend_direction=0,
            )

        # ── Regime classification ────────────────────────────────────────
        if _adx >= self._cfg.adx_trend_threshold:
            regime = MarketRegime.TREND
        elif _adx < self._cfg.range_max_adx:
            regime = MarketRegime.RANGE
        else:
            # ADX in grey zone between range_max_adx and adx_trend_threshold
            regime = MarketRegime.NO_TRADE

        # Directional bias from DMI
        if plus_di > minus_di + 5:
            direction = 1
        elif minus_di > plus_di + 5:
            direction = -1
        else:
            direction = 0

        log.debug(
            "%s regime=%s adx=%.1f atr_pct=%.3f +DI=%.1f -DI=%.1f dir=%+d",
            coin,
            regime.value,
            _adx,
            _atr_pct,
            plus_di,
            minus_di,
            direction,
        )
        return RegimeResult(
            coin=coin,
            regime=regime,
            adx=_adx,
            atr_pct=_atr_pct,
            plus_di=plus_di,
            minus_di=minus_di,
            trend_direction=direction,
        )

    def classify_many(self, coins: list[str]) -> Dict[str, RegimeResult]:
        return {coin: self.classify(coin) for coin in coins}

    # ── Convenience ───────────────────────────────────────────────────────
    @staticmethod
    def is_tradeable(result: RegimeResult) -> bool:
        return result.regime != MarketRegime.NO_TRADE
