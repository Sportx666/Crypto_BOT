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
    "SR_WINDOW":             20,    # ← was 10 (10-min S/R is meaningless)
    "CANDLES_LIMIT":         100,   # ← was 50 (needed for Ichimoku + warmup)
    "STOCH_WINDOW":          14,
    "HULL_LENGTH":           55,
    "SMA_FILTER_LENGTH":     130,

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
    "EMA_SPANS":             [7, 14],
    "EMA_SCORE_WEIGHT":      2.5,
    "MACD_SCORE_WEIGHT":     2.0,
    "RSI_SCORE_WEIGHT":      1.0,
    "VOLUME_SCORE_WEIGHT":   2.0,

    "SCORE_THRESHOLD":       5.0,    # ← was 3.5 (need higher quality signals)
    "REFINED_SCORE_THRESHOLD":6.0,   # ← was 4.5

    # ── Indicator bounds ─────────────────────────────────────────────────────
    "RSI_BOUNDS":            [50, 70],
    "RSI_OVERBOUGHT_THRESHOLD": 75,
    "ADX_BOUNDS":            [25, 55],     # ← was [20, 50] (stronger trend)
    "ATR_BOUNDS":            [0.008, 0.025],  # ATR% bounds (atr/price)
    "VOLUME_SPIKE_THRESHOLD":2.2,           # ← was 2.0
    "VOLUME_THRESHOLD":      1.5,

    # ── Trade suggestion ─────────────────────────────────────────────────────
    "RR_THRESHOLD":          [1.0, 2.5],
    "ROOM_MULTIPLIER":       1.2,
    "ALLOW_PARTIAL_CONFIRMATION": True,
    "SL_BUFFER_MULTIPLIER":  1.1,
    "TP_BUFFER_MULTIPLIER":  1.8,
    "TRAILING_STOP_PCT":     0.05,

    # ── Market regime ─────────────────────────────────────────────────────────
    "ENABLE_REGIME_FILTER":  True,    # NEW: apply regime-based score multipliers
    "REGIME_CACHE_MINUTES":  15,      # NEW: how often to re-check regime
}
