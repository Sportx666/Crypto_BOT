import numpy as np
import pandas as pd

def calculate_hma(df: pd.DataFrame, length: int = 55) -> pd.Series:
    """
    Hull Moving Average (WAP Hull) – fast and smooth trend indicator.
    """
    half = df['close'].rolling(window=int(length / 2)).mean()
    full = df['close'].rolling(window=length).mean()
    raw = 2 * half - full
    hma = raw.rolling(window=int(np.sqrt(length))).mean()
    return hma
