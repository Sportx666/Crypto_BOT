def calculate_support_resistance(df, window=10):
    """
    Calculate support and resistance levels.
    Args:
        df (pd.DataFrame): Market data with 'high', 'low' columns.
        window (int): Rolling window for calculation.
    Returns:
        pd.Series, pd.Series: Support, Resistance levels.
    """
    df['support'] = df['low'].rolling(window=window).min()
    df['resistance'] = df['high'].rolling(window=window).max()
    return df['support'], df['resistance']
