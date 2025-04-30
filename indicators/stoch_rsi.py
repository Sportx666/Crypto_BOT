import numpy as np

def calculate_stoch_rsi(df, window=14):
    """
    Calculate Stochastic RSI (Stoch RSI).
    Args:
        df (pd.DataFrame): DataFrame containing an 'rsi' column.
        stoch_rsi_window (int): Lookback period for the Stochastic RSI calculation.
    Returns:
        pd.Series: Stochastic RSI values.
    """
    min_rsi = df['rsi'].rolling(window=window).min()
    max_rsi = df['rsi'].rolling(window=window).max()

    # Avoid division by zero
    stoch_rsi = (df['rsi'] - min_rsi) / (max_rsi - min_rsi)
    stoch_rsi = stoch_rsi.replace([np.inf, -np.inf], 0)  # Replace infinities with 0
    stoch_rsi = stoch_rsi.fillna(0)  # Replace NaN values with 0

    return stoch_rsi
