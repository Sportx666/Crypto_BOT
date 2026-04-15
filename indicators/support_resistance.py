"""
Support & Resistance — Pivot-Point Based
=========================================
Replaces the naive rolling-min/max approach with a fractal pivot detector
that finds meaningful swing highs/lows across the full candle history.

A pivot high is a candle whose high is the highest within ±pw candles.
A pivot low  is a candle whose low  is the lowest  within ±pw candles.

We then return:
  resistance = nearest pivot high ABOVE current close
  support    = nearest pivot low  BELOW current close

Fallback to rolling window if there are not enough candles.
"""

import numpy as np
import pandas as pd


def calculate_support_resistance(df: pd.DataFrame, window: int = 20) -> tuple:
    """
    Args:
        df     : OHLCV DataFrame
        window : look-back used for the rolling fallback AND as the
                 half-width for pivot detection (pw = window // 3, min 3)
    Returns:
        (support Series, resistance Series)  — same index as df
    """
    n = len(df)
    pw = max(3, window // 3)          # pivot half-window

    current_close = df['close'].iloc[-1]

    # ── pivot detection ───────────────────────────────────────────────────────
    pivot_highs: list[float] = []
    pivot_lows:  list[float] = []

    for i in range(pw, n - pw):
        hi_slice = df['high'].iloc[i - pw: i + pw + 1]
        lo_slice = df['low'].iloc[i - pw: i + pw + 1]

        if df['high'].iloc[i] >= hi_slice.max():
            pivot_highs.append(df['high'].iloc[i])

        if df['low'].iloc[i] <= lo_slice.min():
            pivot_lows.append(df['low'].iloc[i])

    # ── nearest levels relative to current price ──────────────────────────────
    res_candidates = [p for p in pivot_highs if p > current_close]
    sup_candidates = [p for p in pivot_lows  if p < current_close]

    if res_candidates:
        resistance_val = float(min(res_candidates))       # closest above
    else:
        # Fallback: rolling high over full window
        resistance_val = float(df['high'].rolling(window=window, min_periods=1).max().iloc[-1])

    if sup_candidates:
        support_val = float(max(sup_candidates))          # closest below
    else:
        # Fallback: rolling low over full window
        support_val = float(df['low'].rolling(window=window, min_periods=1).min().iloc[-1])

    # ── safety: resistance must always be > support ───────────────────────────
    if resistance_val <= support_val:
        resistance_val = float(df['high'].iloc[-window:].max())
        support_val    = float(df['low'].iloc[-window:].min())

    # ── minimum S/R spread guard ──────────────────────────────────────────────
    # If the spread is still < 0.2% of close (flat market, no meaningful levels)
    # fall back to a wider window so SL and TP never collapse to the same price.
    if current_close > 0:
        spread_pct = (resistance_val - support_val) / current_close
        if spread_pct < 0.002:
            wider = min(window * 2, len(df))
            resistance_val = float(df['high'].iloc[-wider:].max())
            support_val    = float(df['low'].iloc[-wider:].min())

    # Return as Series aligned to df index
    df['support']    = support_val
    df['resistance'] = resistance_val

    return df['support'], df['resistance']
