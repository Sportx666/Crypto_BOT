from misc.config import config
from indicators.adx import calculate_adx
from indicators.atr import calculate_atr
from indicators.bollinger_bands import calculate_bollinger_bands
from indicators.ema import calculate_ema
from indicators.ichimoku import calculate_ichimoku
from indicators.macd import calculate_macd
from indicators.rsi import calculate_rsi
from indicators.stoch_rsi import calculate_stoch_rsi
from indicators.stochastic import calculate_stochastic
from indicators.support_resistance import calculate_support_resistance
from indicators.vwap import calculate_vwap


def calculate_indicators(df):
    """Calculate and enhance indicators for scalping bot."""
    try:
        # Fetch configuration
        short_ema, long_ema = config["EMA_SPANS"]
        atr_window = config.get("ATR_WINDOW", 14)
        bb_window = config.get("BB_WINDOW", 20)
        sr_window = config.get("SR_WINDOW", 5)
        stoch_rsi_window = config.get("STOCH_WINDOW", 14)  # Default window for Stochastic RSI

        # EMA: Trend detection
        df['ema_short'] = calculate_ema(df, short_ema)
        df['ema_long'] = calculate_ema(df, long_ema)

        # RSI: Overbought/oversold zones
        df['rsi'] = calculate_rsi(df)

        # MACD: Trend and momentum indicator
        df['macd'], df['signal'] = calculate_macd(df)
        df['histogram'] = df['macd'] - df['signal']

        # ATR: Volatility measurement
        df = calculate_atr(df, period=atr_window)

        # Bollinger Bands: Volatility and range
        df['bb_upper'], df['bb_middle'], df['bb_lower'] = calculate_bollinger_bands(df, period=bb_window)

        # Support/Resistance Levels
        df['support'], df['resistance'] = calculate_support_resistance(df, window=sr_window)

        # VWAP: Price-volume trend
        df['vwap'] = calculate_vwap(df)

        # Stochastic RSI: Momentum strength
        df['stoch_rsi'] = calculate_stoch_rsi(df, window=stoch_rsi_window)

        # Stochastic Oscillator
        df = calculate_stochastic(df)

        # ADX and Trend Direction Calculation
        adx_result = calculate_adx(df)
        if isinstance(adx_result, tuple):  # Handle dual return case
            df['adx'], df['is_uptrend'] = adx_result
        else:
            df['adx'] = adx_result

        # Ichimoku Cloud indicators
        if config.get("ENABLE_ICHIMOKU", True):  # Check if Ichimoku is enabled
            df = calculate_ichimoku(df)

        # Validation check
        df.bfill(inplace=True), "Indicator calculations resulted in NaN values!"

    except Exception as e:
        print(f"Error in calculate_indicators: {e}")
        raise

    return df

