def calculate_rsi(df, period=14):
    """
    Calculate the Relative Strength Index (RSI).
    Args:
        df (pd.DataFrame): The market data (must contain a 'close' column).
        period (int): The RSI period.
    Returns:
        pd.Series: RSI values.
    """
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period, min_periods=1).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df['rsi'].fillna(50)  # Neutral value for RSI
