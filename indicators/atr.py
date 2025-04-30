import numpy as np

def calculate_atr(df, period=14):
    """
    Calculate the Average True Range (ATR) for volatility measurement.
    Args:
        df (pd.DataFrame): Market data with 'high', 'low', 'close' columns.
        period (int): ATR calculation period.
    Returns:
        pd.Series: ATR values.
    """
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr'] = df['tr'].rolling(window=period).mean()
    df['atr_percentage'] = df['atr'] / df['close']
    return df
