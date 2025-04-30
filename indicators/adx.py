import numpy as np

def calculate_adx(df, period=14):
    """
    Calculate the Average Directional Index (ADX) for trend strength.
    Args:
        df (pd.DataFrame): Market data with 'high', 'low', 'close' columns.
        period (int): Period for ADX calculation.
    Returns:
        pd.Series: ADX values.
    """
    df['dm_plus'] = np.where(
        (df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']),
        np.maximum(df['high'] - df['high'].shift(1), 0),
        0
    )
    df['dm_minus'] = np.where(
        (df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)),
        np.maximum(df['low'].shift(1) - df['low'], 0),
        0
    )
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['di_plus'] = 100 * (df['dm_plus'].rolling(window=period).mean() / df['tr'].rolling(window=period).mean())
    df['di_minus'] = 100 * (df['dm_minus'].rolling(window=period).mean() / df['tr'].rolling(window=period).mean())
    df['dx'] = abs(df['di_plus'] - df['di_minus']) / (df['di_plus'] + df['di_minus']) * 100
    return df['dx'].rolling(window=period).mean(), df['di_plus'] > df['di_minus']
