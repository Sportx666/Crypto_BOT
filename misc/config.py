"""
Updated config.py
=================
Apply these changes to misc/config.py:

KEY CHANGES (based on backtest analysis):
  1. CANDLES_LIMIT: 50 → 100   (Ichimoku needs 78+; ADX/BB need warmup)
  2. SR_WINDOW: 10 → 20        (10-candle S/R = meaningless for 1m)
  3. MIN_VOLUME: 20M → 50M     (blocks GIGGLE, 币安人生, micro-caps)
  4. MIN_TRADES_24H: NEW        (filters wash-trading / fake volume)
  5. MAX_24H_CHANGE_PCT: NEW    (skips pump/dumps already in progress)
  6. PAIR_COOLDOWN_MINUTES: NEW (prevents immediate same-pair re-entry)
  7. SCORE_THRESHOLD: 3.5 → 5.0
  8. REFINED_SCORE_THRESHOLD: 4.5 → 6.0
  9. ADX_BOUNDS: [20,50] → [25,55]  (stronger trend required)
  10. VOLUME_SPIKE_THRESHOLD: 2 → 2.2

All other values unchanged.
"""

# Paste this as the new config dict in misc/config.py
config = {
    # ── Indicator windows ────────────────────────────────────────────────────
    "ATR_WINDOW":            14,
    "BB_WINDOW":             20,
    "SR_WINDOW":             15,    # ← was 20; 15 min S/R is more responsive on 1m
    "CANDLES_LIMIT":         100,   # ← was 50 (needed for Ichimoku + warmup)
    "STOCH_WINDOW":          14,
    "HULL_LENGTH":           55,
    "SMA_FILTER_LENGTH":     110,   # ← was 130; aligned to HULL_LENGTH*2 (55*2)

    # ── Pair selection ────────────────────────────────────────────────────────
    "MIN_VOLUME":            50_000_000,   # ← was 20M; blocks meme coins
    "MIN_TRADES_24H":        50_000,       # NEW: minimum real trades (anti-washtrading)
    "MAX_24H_CHANGE_PCT":    20.0,         # NEW: skip pump/dump already in progress
    "MAX_SPREAD_PCT":        0.0006,
    "BLACKLIST_AUTO":        True,         # NEW: auto-add pairs with >3 consec losses

    # ── Trade management ─────────────────────────────────────────────────────
    "TIMEFRAME":             "1m",
    "ADDITIONAL_TIMEFRAMES": ["3m", "5m"],
    "TIMEFRAME_WEIGHTS":     [3, 1],
    "EXCHANGE_FEES":         0.001,
    "TRADE_MAX_TIME_RUNNING":600,
    "PAIR_COOLDOWN_MINUTES": 30,           # NEW: 30-min cooldown after any trade

    # ── Signal scoring ────────────────────────────────────────────────────────
    "EMA_SPANS":             [9, 21],   # ← was [7,14]; 9/21 give cleaner signals
                                        #   with fewer false crosses on 1m noise
    "EMA_SCORE_WEIGHT":      1.5,    # ← was 2.5 (EMA lag reduced; freed weight to VWAP)
    "MACD_SCORE_WEIGHT":     2.0,
    "RSI_SCORE_WEIGHT":      1.0,
    "VOLUME_SCORE_WEIGHT":   2.0,
    "VWAP_SCORE_WEIGHT":     1.5,    # ← was 0.5 (VWAP = key intraday institutional level)
    "ICHIMOKU_SCORE_WEIGHT": 1.0,    # NEW: price above cloud = very strong bull bias
    "ADX_SLOPE_SCORE_WEIGHT":0.5,    # NEW: rising ADX = trend gaining strength
    "VOLUME_ACCEL_SCORE_WEIGHT": 0.5, # NEW: volume building = participation expanding
    "RSI_SLOPE_SCORE_WEIGHT":0.3,    # NEW: RSI trending up = momentum growing
    "CANDLEBODY_SCORE_WEIGHT":0.3,   # NEW: large bullish candle = conviction bar

    "SCORE_THRESHOLD":       6.5,    # ← was 5.0; max possible ~11.6, so 6.5 = ~56%
                                     #   of max — forces genuine conviction signals
    "REFINED_SCORE_THRESHOLD":7.5,   # ← was 6.0; second-screen bar raised in step

    # ── Indicator bounds ─────────────────────────────────────────────────────
    "RSI_BOUNDS":            [45, 75],   # ← was [50,70]; 45-75 captures full
                                         #   momentum build-up phase; overbought
                                         #   still capped by OVERBOUGHT_THRESHOLD
    "RSI_OVERBOUGHT_THRESHOLD": 78,      # ← was 75; 78 lets strong breakouts score
    "ADX_BOUNDS":            [25, 65],   # ← was [25,55]; allow strongly trending
                                         #   markets (crypto rallies often hit 60+)
    "ATR_BOUNDS":            [0.008, 0.025],  # ATR% bounds (atr/price)
    "VOLUME_SPIKE_THRESHOLD":2.2,           # ← was 2.0
    "VOLUME_THRESHOLD":      1.5,

    # ── Trade suggestion ─────────────────────────────────────────────────────
    "RR_THRESHOLD":          [1.3, 3.0],   # ← was [1.0, 2.5]; reject marginal 1.0R trades;
                                           #   allow high-quality 3.0R setups
    "ROOM_MULTIPLIER":       1.2,
    "ALLOW_PARTIAL_CONFIRMATION": True,
    "SL_BUFFER_MULTIPLIER":  1.1,
    "TP_BUFFER_MULTIPLIER":  2.0,          # ← was 1.8 (slightly more ambitious TP)
    "TRAILING_STOP_PCT":     0.05,

    # ── Multi-timeframe confirmation ──────────────────────────────────────────
    # Fixed score threshold that a higher timeframe (3m/5m) must reach to count
    # as "confirmed".  Previously compared against the variable 1m score, which
    # over-rejected valid signals because HTF candles look weaker in sub-bar metrics.
    "HTF_CONFIRM_THRESHOLD": 4.0,

    # ── Market regime ─────────────────────────────────────────────────────────
    "ENABLE_REGIME_FILTER":  True,    # NEW: apply regime-based score multipliers
    "REGIME_CACHE_MINUTES":  15,      # NEW: how often to re-check regime
}
