"""
Market Regime Detector
======================
Analyses BTC (macro) and ETH (confirmation) on 15-minute and 1-hour
timeframes to classify the current market condition.

Five regimes:
  BULL_STRONG   — Strong uptrend, trade freely at normal thresholds
  BULL_WEAK     — Moderate uptrend, slightly tighter thresholds
  SIDEWAYS      — No clear direction, raise thresholds significantly
  BEAR_WEAK     — Early downtrend, very strict criteria only
  BEAR_STRONG   — Clear downtrend, skip all trades

Regime is re-evaluated every CACHE_MINUTES (default 15 min) to avoid
hammering the Binance API on every bot loop.

Usage
-----
    from core.market_regime import get_regime, REGIME_CONFIG_OVERRIDES

    regime = get_regime()
    overrides = REGIME_CONFIG_OVERRIDES[regime]
    # Apply overrides to your config dict before scanning
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("market_regime")

# ── cache ─────────────────────────────────────────────────────────────────────
_cache_regime:   Optional[str] = None
_cache_time:     float         = 0.0
CACHE_MINUTES:   int           = 15

# ── regime names ─────────────────────────────────────────────────────────────
BULL_STRONG  = "BULL_STRONG"
BULL_WEAK    = "BULL_WEAK"
SIDEWAYS     = "SIDEWAYS"
BEAR_WEAK    = "BEAR_WEAK"
BEAR_STRONG  = "BEAR_STRONG"

ALL_REGIMES = [BULL_STRONG, BULL_WEAK, SIDEWAYS, BEAR_WEAK, BEAR_STRONG]

# ── config multipliers per regime ─────────────────────────────────────────────
# These are MULTIPLIED onto / ADDED to the live config values.
# A value of 1.0 means "use the config as-is".
REGIME_CONFIG_OVERRIDES: dict[str, dict] = {
    BULL_STRONG: {
        "score_mult":          1.0,   # normal scoring
        "refined_score_mult":  1.0,
        "rr_min_mult":         1.0,
        "adx_min_add":         0,
        "volume_spike_mult":   1.0,
        "allow_partial":       True,
        "description": "Strong bull market — trade normally",
    },
    BULL_WEAK: {
        "score_mult":          1.15,  # need 15% higher score
        "refined_score_mult":  1.15,
        "rr_min_mult":         1.0,
        "adx_min_add":         5,     # ADX must be 5 points higher
        "volume_spike_mult":   1.1,
        "allow_partial":       True,
        "description": "Moderate bull — slightly tighter criteria",
    },
    SIDEWAYS: {
        "score_mult":          1.35,  # need 35% higher score
        "refined_score_mult":  1.35,
        "rr_min_mult":         1.2,   # need better R:R
        "adx_min_add":         10,
        "volume_spike_mult":   1.3,
        "allow_partial":       False, # require full confirmation
        "description": "Sideways market — raise bar significantly",
    },
    BEAR_WEAK: {
        "score_mult":          1.60,
        "refined_score_mult":  1.60,
        "rr_min_mult":         1.4,
        "adx_min_add":         15,
        "volume_spike_mult":   1.5,
        "allow_partial":       False,
        "description": "Early downtrend — only very strong setups",
    },
    BEAR_STRONG: {
        "score_mult":          999,   # effectively blocks all trades
        "refined_score_mult":  999,
        "rr_min_mult":         1.0,
        "adx_min_add":         0,
        "volume_spike_mult":   1.0,
        "allow_partial":       False,
        "description": "Strong downtrend — NO TRADES",
    },
}


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Core regime calculation
# ╚══════════════════════════════════════════════════════════════════════════════
def _score_asset(df: pd.DataFrame) -> float:
    """
    Return a regime score for a single asset DataFrame.
    Positive = bullish, negative = bearish.
    Scale: roughly -10 to +10.
    """
    if df is None or len(df) < 50:
        return 0.0

    close = df['close']
    score = 0.0

    # 1. EMA trend structure  (+3 / -3)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    price = close.iloc[-1]

    if price > ema20.iloc[-1] > ema50.iloc[-1]:
        score += 3.0
    elif price < ema20.iloc[-1] < ema50.iloc[-1]:
        score -= 3.0
    elif price > ema20.iloc[-1]:
        score += 1.0
    elif price < ema20.iloc[-1]:
        score -= 1.0

    # 2. RSI  (+2 / -2)
    delta = close.diff()
    gain  = delta.where(delta > 0, 0).rolling(14).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = (100 - 100 / (1 + rs)).iloc[-1]

    if rsi > 55:
        score += 2.0
    elif rsi > 50:
        score += 1.0
    elif rsi < 45:
        score -= 2.0
    elif rsi < 50:
        score -= 1.0

    # 3. MACD histogram direction  (+2 / -2)
    macd_fast = close.ewm(span=12, adjust=False).mean()
    macd_slow = close.ewm(span=26, adjust=False).mean()
    macd      = macd_fast - macd_slow
    signal    = macd.ewm(span=9, adjust=False).mean()
    hist      = macd - signal

    if hist.iloc[-1] > 0 and hist.iloc[-1] > hist.iloc[-2]:
        score += 2.0
    elif hist.iloc[-1] > 0:
        score += 1.0
    elif hist.iloc[-1] < 0 and hist.iloc[-1] < hist.iloc[-2]:
        score -= 2.0
    elif hist.iloc[-1] < 0:
        score -= 1.0

    # 4. Recent candle body direction (+1 / -1)
    last_5_pct = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] * 100
    if last_5_pct > 0.5:
        score += 1.0
    elif last_5_pct < -0.5:
        score -= 1.0

    # 5. Higher high / lower low structure  (+2 / -2)
    highs = df['high']
    lows  = df['low']
    hh = highs.iloc[-1] > highs.iloc[-5:-1].max()   # recent high > prev highs
    ll = lows.iloc[-1]  < lows.iloc[-5:-1].min()    # recent low  < prev lows

    if hh and not ll:
        score += 2.0
    elif ll and not hh:
        score -= 2.0

    return round(score, 2)


def _classify(combined_score: float) -> str:
    """Map a numeric score to a regime name."""
    if combined_score >= 6.0:
        return BULL_STRONG
    elif combined_score >= 2.0:
        return BULL_WEAK
    elif combined_score >= -2.0:
        return SIDEWAYS
    elif combined_score >= -5.0:
        return BEAR_WEAK
    else:
        return BEAR_STRONG


def calculate_regime(
    btc_1h:   pd.DataFrame,
    btc_15m:  pd.DataFrame,
    eth_1h:   Optional[pd.DataFrame] = None,
) -> str:
    """
    Combine BTC hourly + BTC 15-minute scores (+ optional ETH confirmation)
    and return a regime string.
    """
    score_btc_1h  = _score_asset(btc_1h)
    score_btc_15m = _score_asset(btc_15m)
    score_eth     = _score_asset(eth_1h) if eth_1h is not None else 0.0

    # Weight: BTC 1h = 40%, BTC 15m = 40%, ETH 1h = 20%
    if eth_1h is not None:
        combined = (score_btc_1h * 0.40
                  + score_btc_15m * 0.40
                  + score_eth     * 0.20)
    else:
        combined = (score_btc_1h * 0.50
                  + score_btc_15m * 0.50)

    regime = _classify(combined)

    log.info(
        f"Market regime: {regime}  "
        f"(BTC1h={score_btc_1h:+.1f}, BTC15m={score_btc_15m:+.1f}, "
        f"ETH1h={score_eth:+.1f}, combined={combined:+.1f})"
    )
    return regime


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Cached public API
# ╚══════════════════════════════════════════════════════════════════════════════
def get_regime(force_refresh: bool = False) -> str:
    """
    Return the current market regime, using a 15-minute cache.
    Falls back to SIDEWAYS if the Binance API is unavailable.
    """
    global _cache_regime, _cache_time

    now = time.time()
    if (not force_refresh
            and _cache_regime is not None
            and (now - _cache_time) < CACHE_MINUTES * 60):
        return _cache_regime

    try:
        from utilities.binance_call_new import fetch_data

        btc_1h  = fetch_data("BTCUSDT", timeframe="1h",  limit=60)
        btc_15m = fetch_data("BTCUSDT", timeframe="15m", limit=60)
        eth_1h  = fetch_data("ETHUSDT", timeframe="1h",  limit=60)

        regime = calculate_regime(btc_1h, btc_15m, eth_1h)

    except Exception as exc:
        log.warning(f"get_regime failed, defaulting to SIDEWAYS: {exc}")
        regime = SIDEWAYS

    _cache_regime = regime
    _cache_time   = now
    return regime


def apply_regime_to_config(base_config: dict, regime: str) -> dict:
    """
    Return a NEW config dict with regime-based overrides applied.
    The original base_config is NOT mutated.
    """
    overrides = REGIME_CONFIG_OVERRIDES.get(regime, REGIME_CONFIG_OVERRIDES[SIDEWAYS])
    cfg = dict(base_config)  # shallow copy

    sm  = overrides["score_mult"]
    rsm = overrides["refined_score_mult"]
    rrm = overrides["rr_min_mult"]
    adx_add = overrides["adx_min_add"]
    vsm = overrides["volume_spike_mult"]

    cfg["SCORE_THRESHOLD"]          = cfg.get("SCORE_THRESHOLD",          3.5) * sm
    cfg["REFINED_SCORE_THRESHOLD"]  = cfg.get("REFINED_SCORE_THRESHOLD",  4.5) * rsm
    cfg["VOLUME_SPIKE_THRESHOLD"]   = cfg.get("VOLUME_SPIKE_THRESHOLD",   2.0) * vsm
    cfg["ALLOW_PARTIAL_CONFIRMATION"] = overrides["allow_partial"]

    rr_min, rr_max = cfg.get("RR_THRESHOLD", [1.0, 2.5])
    cfg["RR_THRESHOLD"] = [rr_min * rrm, rr_max]

    adx_min, adx_max = cfg.get("ADX_BOUNDS", [20, 65])
    cfg["ADX_BOUNDS"] = [adx_min + adx_add, adx_max]

    return cfg


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Regime description helper (used by UI)
# ╚══════════════════════════════════════════════════════════════════════════════
REGIME_DISPLAY = {
    BULL_STRONG: ("🟢  BULL STRONG",  "#06d6a0"),
    BULL_WEAK:   ("🟡  BULL WEAK",    "#ffd60a"),
    SIDEWAYS:    ("⬜  SIDEWAYS",     "#8892a4"),
    BEAR_WEAK:   ("🟠  BEAR WEAK",    "#ff8c42"),
    BEAR_STRONG: ("🔴  BEAR STRONG",  "#ef233c"),
}


def regime_label(regime: str) -> tuple[str, str]:
    """Return (display_text, hex_colour) for a regime string."""
    return REGIME_DISPLAY.get(regime, ("❓  UNKNOWN", "#8892a4"))
