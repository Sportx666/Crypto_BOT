def calculate_ichimoku(df):
    """
    Calculate Ichimoku Cloud indicators: Tenkan-sen, Kijun-sen, Senkou Span A & B, and Chikou Span.
    Args:
        df (pd.DataFrame): Market data with 'high', 'low', and 'close' columns.
    Returns:
        pd.DataFrame: Updated DataFrame with Ichimoku components.
    """
    high_9 = df['high'].rolling(window=9).max()
    low_9 = df['low'].rolling(window=9).min()
    df['tenkan_sen'] = (high_9 + low_9) / 2  # Conversion Line

    high_26 = df['high'].rolling(window=26).max()
    low_26 = df['low'].rolling(window=26).min()
    df['kijun_sen'] = (high_26 + low_26) / 2  # Base Line

    df['senkou_span_a'] = ((df['tenkan_sen'] + df['kijun_sen']) / 2).shift(26)  # Leading Span A
    high_52 = df['high'].rolling(window=52).max()
    low_52 = df['low'].rolling(window=52).min()
    df['senkou_span_b'] = ((high_52 + low_52) / 2).shift(26)  # Leading Span B

    df['chikou_span'] = df['close'].shift(-26)  # Lagging Span
    return df
