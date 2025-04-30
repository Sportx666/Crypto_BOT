def calculate_vwap(df):
    """
    Calculate Volume Weighted Average Price (VWAP).
    Args:
        df (pd.DataFrame): Market data with 'close', 'volume' columns.
    Returns:
        pd.Series: VWAP values.
    """
    df['cum_volume'] = df['volume'].cumsum()
    df['cum_vwap'] = (df['close'] * df['volume']).cumsum()
    return df['cum_vwap'] / df['cum_volume']
