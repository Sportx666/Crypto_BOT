"""
Pure-pandas technical indicators used by regime and strategy modules.

All functions accept a pd.DataFrame with columns:
  open, high, low, close, volume

and return either a pd.Series or a scalar (last-bar value).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


# ── EMA ──────────────────────────────────────────────────────────────────

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ── ATR ──────────────────────────────────────────────────────────────────

def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=window, adjust=False).mean()


def atr_pct(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """ATR as a fraction of close price."""
    _atr = atr(df, window)
    return _atr / df["close"]


# ── RSI ──────────────────────────────────────────────────────────────────

def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── MACD ─────────────────────────────────────────────────────────────────

def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── ADX ──────────────────────────────────────────────────────────────────

def adx(df: pd.DataFrame, window: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Returns (adx, plus_di, minus_di).
    ADX measures trend strength (0-100), independent of direction.
    """
    h, l, c = df["high"], df["low"], df["close"]
    prev_h = h.shift(1)
    prev_l = l.shift(1)
    prev_c = c.shift(1)

    plus_dm = (h - prev_h).clip(lower=0)
    minus_dm = (prev_l - l).clip(lower=0)
    # When both positive, keep only the larger
    mask = plus_dm >= minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)

    tr = pd.concat(
        [h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)

    atr14 = tr.ewm(span=window, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=window, adjust=False).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=window, adjust=False).mean() / atr14.replace(0, np.nan)

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_line = dx.ewm(span=window, adjust=False).mean()

    return adx_line, plus_di, minus_di


# ── Bollinger Bands ───────────────────────────────────────────────────────

def bollinger_bands(
    series: pd.Series, window: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower)."""
    middle = series.rolling(window).mean()
    std = series.rolling(window).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


# ── VWAP (session/rolling) ────────────────────────────────────────────────

def vwap(df: pd.DataFrame, window: int = 0) -> pd.Series:
    """
    Rolling VWAP.
    If window=0, computes cumulative VWAP from the start of the DataFrame.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    if window == 0:
        cum_tp_vol = (typical * vol).cumsum()
        cum_vol = vol.cumsum()
    else:
        cum_tp_vol = (typical * vol).rolling(window).sum()
        cum_vol = vol.rolling(window).sum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


# ── Stochastic ────────────────────────────────────────────────────────────

def stochastic(
    df: pd.DataFrame, k_window: int = 14, d_window: int = 3
) -> Tuple[pd.Series, pd.Series]:
    """Returns (%K, %D)."""
    low_min = df["low"].rolling(k_window).min()
    high_max = df["high"].rolling(k_window).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_window).mean()
    return k, d


# ── HMA (Hull Moving Average) ─────────────────────────────────────────────

def hma(series: pd.Series, length: int = 20) -> pd.Series:
    half = length // 2
    sqrt_n = int(np.floor(np.sqrt(length)))
    wma_half = series.rolling(half).mean()
    wma_full = series.rolling(length).mean()
    raw = 2 * wma_half - wma_full
    return raw.rolling(sqrt_n).mean()


# ══════════════════════════════════════════════════════════════════════════
#  Convenience: compute all indicators at once
# ══════════════════════════════════════════════════════════════════════════

def compute_all(df: pd.DataFrame, cfg_ema_fast: int = 9, cfg_ema_slow: int = 21) -> dict:
    """
    Compute all indicators on df and return a dict of named Series/scalar.

    Only the LAST row values are relevant for signal generation;
    callers can do out["adx"].iloc[-1] or use .last_bar() helper.
    """
    close = df["close"]

    ema_f = ema(close, cfg_ema_fast)
    ema_s = ema(close, cfg_ema_slow)
    _atr = atr(df)
    _atr_pct = _atr / close
    _rsi = rsi(close)
    macd_l, macd_sig, macd_hist = macd(close)
    adx_l, plus_di, minus_di = adx(df)
    bb_up, bb_mid, bb_low = bollinger_bands(close)
    _vwap = vwap(df)
    stoch_k, stoch_d = stochastic(df)
    _hma = hma(close)

    return {
        "ema_fast": ema_f,
        "ema_slow": ema_s,
        "atr": _atr,
        "atr_pct": _atr_pct,
        "rsi": _rsi,
        "macd": macd_l,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "adx": adx_l,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "bb_upper": bb_up,
        "bb_mid": bb_mid,
        "bb_lower": bb_low,
        "vwap": _vwap,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "hma": _hma,
    }


def last_bar(ind: dict) -> dict:
    """Extract scalar values for the last bar from a compute_all() result dict."""
    return {k: (float(v.iloc[-1]) if isinstance(v, pd.Series) else v) for k, v in ind.items()}
