import os
import pandas as pd
import matplotlib.pyplot as plt
from core.indicator import calculate_indicators  # Ensure this is available
from final_pair import refine_best_pair  # Ensure this is available

# Paths to data and pair table
data_path = r"C:\Users\gcarusi\OneDrive - CTI LOGISTICS LIMITED\Code\Project_new\New_bot\historical_data"
pair_table_path = r"C:\Users\gcarusi\OneDrive - CTI LOGISTICS LIMITED\Documents\backtest.xlsx"  # Update to your table's path

def load_pair_table(pair_table_path):
    """
    Load the table containing pairs and timestamps.
    Args:
        pair_table_path (str): Path to the pair table CSV.
    Returns:
        pd.DataFrame: DataFrame containing pairs and timestamps.
    """
    return pd.read_excel(pair_table_path)

def fetch_data_for_trade(pair, timestamp, data_path):
    """
    Fetch historical data for the given pair and timestamp.
    Args:
        pair (str): Trading pair.
        timestamp (str): Timestamp for the trade.
        data_path (str): Path to the folder containing historical CSV files.
    Returns:
        pd.DataFrame: Historical data for the pair.
    """
    file_name = f"{pair}_historical_data.csv"  # Assuming 1-minute data
    file_path = os.path.join(data_path, file_name)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Data for {pair} not found in {data_path}.")

    df = pd.read_csv(file_path, parse_dates=["timestamp"], index_col="timestamp")
    # Filter data around the timestamp
    trade_time = pd.to_datetime(timestamp)
    return df.loc[trade_time - pd.Timedelta(minutes=60): trade_time + pd.Timedelta(minutes=60)]

def backtest_with_table(pair_table, data_path, interval="15T"):
    """
    Perform backtesting using pairs and timestamps from the table.
    Args:
        pair_table (pd.DataFrame): Table with pairs and timestamps.
        data_path (str): Path to historical data.
        interval (str): Resampling interval (e.g., '15T' for 15 minutes).
    Returns:
        pd.DataFrame: Results of the backtest.
    """
    results = []

    for _, row in pair_table.iterrows():
        pair = row["Pair"].strip()
        timestamp = row["Date"]
        try:
            print(f"Processing {pair} at {timestamp}...")

            # Fetch and process historical data
            historical_data = fetch_data_for_trade(pair, timestamp, data_path)
            historical_data_resampled = historical_data.resample(interval).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()

            # Calculate indicators
            historical_data_indicators = calculate_indicators(historical_data_resampled)

            # Use refine_best_pair to calculate updated Entry, TP, SL
            refined = refine_best_pair(historical_data_indicators, pair)
            if not refined:
                print(f"Skipping {pair} at {timestamp}: no valid trade suggestion")
                continue
            entry_price = refined["trade_suggestion"]["entry"]
            stop_loss = refined["trade_suggestion"]["stop_loss"]
            take_profit = refined["trade_suggestion"]["take_profit"]

            # Simulate trade
            trade_result = {
                "pair": pair,
                "timestamp": timestamp,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "result": None,
                "pnl": None
            }

            for _, row in historical_data_indicators.iterrows():
                if row["low"] <= stop_loss:
                    trade_result["result"] = "loss"
                    trade_result["pnl"] = stop_loss - entry_price
                    break
                if row["high"] >= take_profit:
                    trade_result["result"] = "profit"
                    trade_result["pnl"] = take_profit - entry_price
                    break
            else:
                trade_result["result"] = "neutral"
                trade_result["pnl"] = 0

            results.append(trade_result)
        except Exception as e:
            print(f"Error processing {pair} at {timestamp}: {e}")

    results_df = pd.DataFrame(results)
    results_df["cumulative_pnl"] = results_df["pnl"].cumsum()
    return results_df

if __name__ == "__main__":
    # Load pair table
    pair_table = load_pair_table(pair_table_path)

    # Perform backtest
    backtest_results = backtest_with_table(pair_table, data_path, interval="15T")

    # Visualize results
    plt.figure(figsize=(12, 6))
    plt.plot(backtest_results["cumulative_pnl"], marker="o", label="Cumulative P&L")
    plt.title("Backtest Results - Cumulative P&L")
    plt.xlabel("Trade Number")
    plt.ylabel("Cumulative P&L")
    plt.legend()
    plt.grid(True)
    plt.show()

    # Save results
    backtest_results.to_csv("backtest_results.csv", index=False)
    print("Backtest complete. Results saved to 'backtest_results.csv'.")
