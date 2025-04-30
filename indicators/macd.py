def calculate_macd(df):
    """
    Calculate the MACD and Signal Line.
    Args:
        df (pd.DataFrame): The market data (must contain a 'close' column).
    Returns:
        pd.Series, pd.Series: MACD line, Signal line.
    """
    short_ema = df['close'].ewm(span=12, adjust=False).mean()
    long_ema = df['close'].ewm(span=26, adjust=False).mean()
    macd = short_ema - long_ema
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal
