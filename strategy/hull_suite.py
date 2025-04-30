def evaluate_hull_signal(df):
    if len(df) < 130 or df['hma'].isnull().any():
        return {"long": False, "exit": False}
    
    hma_1 = df['hma'].iloc[-2]
    hma_2 = df['hma'].iloc[-3]
    hma_3 = df['hma'].iloc[-4]
    sma_1 = df['sma_130'].iloc[-2]
    close_1 = df['close'].iloc[-2]

    long_signal = hma_1 > hma_3 and hma_2 <= hma_3 and close_1 > sma_1
    exit_signal = hma_1 < hma_3 and hma_2 >= hma_3
    return {"long": long_signal, "exit": exit_signal}
