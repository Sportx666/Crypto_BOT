def calculate_bollinger_bands(df, period=20):
    """
    Calculate Bollinger Bands for volatility range detection.
    Args:
        df (pd.DataFrame): Market data with 'close' column.
        period (int): Rolling window for Bollinger Bands.
    Returns:
        pd.Series, pd.Series, pd.Series: Upper band, middle band, lower band.
    """
    df['bb_middle'] = df['close'].rolling(window=period).mean()
    df['bb_stddev'] = df['close'].rolling(window=period).std()
    df['bb_upper'] = df['bb_middle'] + (2 * df['bb_stddev'])
    df['bb_lower'] = df['bb_middle'] - (2 * df['bb_stddev'])
    return df['bb_upper'], df['bb_middle'], df['bb_lower']
