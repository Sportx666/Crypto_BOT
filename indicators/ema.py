def calculate_ema(df, span):
    """
    Calculate Exponential Moving Average (EMA) for the given data.
    Args:
        df (pd.DataFrame): The market data (must contain a 'close' column).
        span (int): The EMA period.
    Returns:
        pd.Series: EMA values.
    """
    return df['close'].ewm(span=span, adjust=False).mean()
