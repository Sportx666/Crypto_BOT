"""
Indicator Orchestration
========================
Calculates all technical indicators and adds derived signal columns used
by the strategy layer.

New in this version:
  • HMA (Hull Moving Average) + SMA_130 — now actually computed
  • breakout_strength  — (close - resistance) / atr, per-candle
  • breakout           — bool: close > resistance
  • breakout_probability — normalised breakout_strength capped at 1.0
  • momentum_confirmed — StochRSI > 0.5  AND  %K > %D
  • DMI (is_uptrend)  — stored as bool column from ADX calculation
"""

import numpy as np
from misc.config import config
from indicators.adx               import calculate_adx
from indicators.atr               import calculate_atr
from indicators.bollinger_bands   import calculate_bollinger_bands
from indicators.ema               import calculate_ema
from indicators.hma               import calculate_hma
from indicators.ichimoku          import calculate_ichimoku
from indicators.macd              import calculate_macd
from indicators.rsi               import calculate_rsi
from indicators.stoch_rsi         import calculate_stoch_rsi
from indicators.stochastic        import calculate_stochastic
from indicators.support_resistance import calculate_support_resistance
from indicators.vwap              import calculate_vwap


def calculate_indicators(df):
    """
    Calculate and attach all indicators to the OHLCV DataFrame.
    Returns the enriched DataFrame.  Raises on unrecoverable errors.
    """
    try:
        # ── configuration ──────────────────────────────────────────────────────
        short_ema, long_ema = config["EMA_SPANS"]
        atr_window    = config.get("ATR_WINDOW",    14)
        bb_window     = config.get("BB_WINDOW",     20)
        sr_window     = config.get("SR_WINDOW",     20)   # enlarged default
        stoch_window  = config.get("STOCH_WINDOW",  14)
        hull_length   = config.get("HULL_LENGTH",   55)
        sma_filter    = config.get("SMA_FILTER_LENGTH", 130)

        # ── trend ──────────────────────────────────────────────────────────────
        df['ema_short'] = calculate_ema(df, short_ema)
        df['ema_long']  = calculate_ema(df, long_ema)

        # ── momentum ───────────────────────────────────────────────────────────
        df['rsi']                  = calculate_rsi(df)
        df['macd'], df['signal']   = calculate_macd(df)
        df['histogram']            = df['macd'] - df['signal']
        df['stoch_rsi']            = calculate_stoch_rsi(df, window=stoch_window)
        df                         = calculate_stochastic(df, k_period=stoch_window)

        # ── volatility ─────────────────────────────────────────────────────────
        df = calculate_atr(df, period=atr_window)
        # atr_percentage already set by calculate_atr as df['atr'] / df['close']

        # ── Bollinger Bands ────────────────────────────────────────────────────
        df['bb_upper'], df['bb_middle'], df['bb_lower'] = \
            calculate_bollinger_bands(df, period=bb_window)
        df['bollinger'] = df.apply(
            lambda r: {
                'bb_upper':  r['bb_upper'],
                'bb_middle': r['bb_middle'],
                'bb_lower':  r['bb_lower'],
            }, axis=1
        )

        # ── support / resistance (pivot-based) ─────────────────────────────────
        df['support'], df['resistance'] = \
            calculate_support_resistance(df, window=sr_window)

        # ── volume-weighted average price ──────────────────────────────────────
        df['vwap'] = calculate_vwap(df)

        # ── trend strength (ADX + DMI) ─────────────────────────────────────────
        adx_result = calculate_adx(df)
        if isinstance(adx_result, tuple):
            df['adx'], df['is_uptrend'] = adx_result
        else:
            df['adx']        = adx_result
            df['is_uptrend'] = df['ema_short'] > df['ema_long']   # fallback

        # ── Hull Moving Average + SMA filter ───────────────────────────────────
        df['hma']     = calculate_hma(df, length=hull_length)
        df['sma_130'] = df['close'].rolling(window=sma_filter, min_periods=1).mean()

        # ── Ichimoku (only if enough candles) ──────────────────────────────────
        if config.get("ENABLE_ICHIMOKU", True) and len(df) >= 100:
            df = calculate_ichimoku(df)

        # ── derived signal columns ─────────────────────────────────────────────
        #  breakout_strength: how far above resistance (in ATR units) the close is
        #  Positive  = above resistance (breakout)
        #  Negative  = below resistance (consolidation)
        safe_atr = df['atr'].replace(0, np.nan)
        df['breakout_strength']  = (df['close'] - df['resistance']) / safe_atr
        df['breakout']           = df['close'] > df['resistance']

        # breakout_probability: 0→1 normalised by ROOM_MULTIPLIER threshold
        room_mult = config.get("ROOM_MULTIPLIER", 1.2)
        df['breakout_probability'] = (
            df['breakout_strength'].clip(lower=0) / room_mult
        ).clip(upper=1.0)

        # momentum_confirmed: strong momentum, not overbought, trending
        df['momentum_confirmed'] = (
            (df['stoch_rsi'] > 0.5) &
            (df['%K'] > df['%D']) &
            (df['rsi'] < config.get("RSI_OVERBOUGHT_THRESHOLD", 75))
        )

        # DMI: is the dominant directional move upward?  (already in is_uptrend)
        df['DMI'] = df['is_uptrend'].astype(bool)

        # ── fill any remaining NaN (must be LAST step) ─────────────────────────
        df.bfill(inplace=True)
        df.ffill(inplace=True)

    except Exception as e:
        print(f"Error in calculate_indicators: {e}")
        raise

    return df
