import pandas as pd
import numpy as np

import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.indicator import calculate_indicators


def create_mock_data():
    """
    Create mock market data for testing.
    Returns:
        pd.DataFrame: Mock DataFrame with 'high', 'low', 'close', and 'volume'.
    """
    data = {
        'high': np.random.randint(100, 200, size=50),
        'low': np.random.randint(90, 180, size=50),
        'close': np.random.randint(95, 190, size=50),
        'volume': np.random.randint(1000, 5000, size=50)
    }
    return pd.DataFrame(data)

def test_calculate_indicators():
    """
    Test the calculate_indicators function to verify its outputs.
    """
    # Step 1: Create mock data
    df = create_mock_data()

    # Step 2: Calculate indicators
    try:
        result_df = calculate_indicators(df)
    except Exception as e:
        print(f"Error during indicator calculation: {e}")
        assert False, "calculate_indicators raised an exception unexpectedly!"

    # Step 3: Validate outputs
    # Ensure no NaN values are present
    assert not result_df.isnull().values.any(), "Resulting DataFrame contains NaN values!"

    # Check for expected columns
    expected_columns = [
        'ema_short', 'ema_long', 'rsi', 'macd', 'signal', 'histogram',
        'atr', 'bb_upper', 'bb_middle', 'bb_lower', 'support', 'resistance',
        'vwap', 'stoch_rsi', 'adx'
    ]
    missing_columns = [col for col in expected_columns if col not in result_df.columns]
    assert not missing_columns, f"Missing columns in result: {missing_columns}"

    # Verify some calculated values (spot-checking)
    assert result_df['ema_short'].iloc[-1] > 0, "EMA short should be greater than 0."
    assert result_df['rsi'].iloc[-1] > 0, "RSI should be greater than 0."
    assert result_df['adx'].iloc[-1] > 0, "ADX should be greater than 0."
    assert result_df['macd'].iloc[-1] != result_df['signal'].iloc[-1], "MACD and Signal values should differ."

    print("All tests passed!")

if __name__ == "__main__":
    test_calculate_indicators()
