def calculate_stochastic(df, k_period=14, d_period=3):
    """
    Calculate Stochastic Oscillator (%K and %D).
    Args:
        df (pd.DataFrame): Market data with 'high', 'low', and 'close' columns.
        k_period (int): Lookback period for %K calculation.
        d_period (int): Moving average period for %D calculation.
    Returns:
        pd.DataFrame: Updated DataFrame with %K and %D.
    """
    df['low_k'] = df['low'].rolling(window=k_period).min()
    df['high_k'] = df['high'].rolling(window=k_period).max()
    df['%K'] = (df['close'] - df['low_k']) / (df['high_k'] - df['low_k']) * 100
    df['%D'] = df['%K'].rolling(window=d_period).mean()
    return df
