"""
Configuration dataclass – all tunables in one place.
Loaded from environment variables (via .env) with sane defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


def _float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


@dataclass
class Config:
    # ── Exchange ──────────────────────────────────────────────────────────
    testnet: bool = field(default_factory=lambda: _bool("TESTNET", True))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))

    hl_wallet_address: str = field(
        default_factory=lambda: os.getenv("HL_WALLET_ADDRESS", "")
    )
    hl_private_key: str = field(
        default_factory=lambda: os.getenv("HL_PRIVATE_KEY", "")
    )

    # ── URLs (resolved in __post_init__) ─────────────────────────────────
    hl_api_url: str = ""
    hl_ws_url: str = ""

    # ── Timeframes ────────────────────────────────────────────────────────
    exec_tf: str = "5m"        # signal evaluation timeframe
    regime_tf: str = "15m"     # per-market regime timeframe
    global_tf: str = "1h"      # global BTC filter timeframe
    global_symbol: str = "BTC"

    # ── Universe & active set ─────────────────────────────────────────────
    universe_scan_interval_min: int = field(
        default_factory=lambda: _int("UNIVERSE_SCAN_INTERVAL_MIN", 15)
    )
    max_active_symbols: int = field(
        default_factory=lambda: _int("MAX_ACTIVE_SYMBOLS", 8)
    )
    min_volume_24h_usd: float = field(
        default_factory=lambda: _float("MIN_VOLUME_24H_USD", 5_000_000.0)
    )
    always_subscribed: List[str] = field(default_factory=lambda: ["BTC"])

    # ── Positions ────────────────────────────────────────────────────────
    max_concurrent_positions: int = field(
        default_factory=lambda: _int("MAX_CONCURRENT_POSITIONS", 3)
    )
    default_leverage: int = field(
        default_factory=lambda: _int("DEFAULT_LEVERAGE", 3)
    )

    # ── Risk per trade ────────────────────────────────────────────────────
    risk_pct: float = field(
        default_factory=lambda: _float("RISK_PCT", 0.0025)   # 0.25%
    )
    max_risk_pct: float = field(
        default_factory=lambda: _float("MAX_RISK_PCT", 0.005)  # hard cap 0.5%
    )

    # ── Loss limits ───────────────────────────────────────────────────────
    daily_loss_pct: float = field(
        default_factory=lambda: _float("DAILY_LOSS_PCT", 0.01)   # 1%
    )
    weekly_loss_pct: float = field(
        default_factory=lambda: _float("WEEKLY_LOSS_PCT", 0.03)  # 3%
    )

    # ── Cooldowns ────────────────────────────────────────────────────────
    cooldown_after_stop_min: int = field(
        default_factory=lambda: _int("COOLDOWN_AFTER_STOP_MIN", 60)
    )

    # ── Exposure caps ────────────────────────────────────────────────────
    max_long_notional_pct: float = field(
        default_factory=lambda: _float("MAX_LONG_NOTIONAL_PCT", 0.15)
    )
    max_short_notional_pct: float = field(
        default_factory=lambda: _float("MAX_SHORT_NOTIONAL_PCT", 0.15)
    )

    # ── Daily trade cap ───────────────────────────────────────────────────
    max_trades_per_day: int = field(
        default_factory=lambda: _int("MAX_TRADES_PER_DAY", 8)
    )

    # ── Regime thresholds (15m) ───────────────────────────────────────────
    adx_trend_threshold: float = field(
        default_factory=lambda: _float("ADX_TREND_THRESHOLD", 27.0)  # was 25
    )
    adx_strong_threshold: float = field(
        default_factory=lambda: _float("ADX_STRONG_THRESHOLD", 40.0)
    )
    atr_pct_min: float = field(
        default_factory=lambda: _float("ATR_PCT_MIN", 0.003)   # 0.3%
    )
    atr_pct_max: float = field(
        default_factory=lambda: _float("ATR_PCT_MAX", 0.05)    # 5%
    )
    # ADX must be rising (current vs N bars ago) to confirm trend is building
    adx_slope_lookback: int = field(
        default_factory=lambda: _int("ADX_SLOPE_LOOKBACK", 4)
    )

    # ── TREND module (5m) ────────────────────────────────────────────────
    ema_fast: int = field(default_factory=lambda: _int("EMA_FAST", 9))
    ema_slow: int = field(default_factory=lambda: _int("EMA_SLOW", 21))
    ema_mid: int = field(default_factory=lambda: _int("EMA_MID", 50))    # triple stack

    # FIX #2: widen SL to reduce noise stops (was 1.5)
    trend_atr_sl_mult: float = field(
        default_factory=lambda: _float("TREND_ATR_SL_MULT", 2.0)
    )
    trend_partial_tp_r: float = field(
        default_factory=lambda: _float("TREND_PARTIAL_TP_R", 1.5)  # partial at 1.5R
    )
    trend_partial_close_pct: float = field(
        default_factory=lambda: _float("TREND_PARTIAL_CLOSE_PCT", 0.5)
    )
    trend_trail_atr_mult: float = field(
        default_factory=lambda: _float("TREND_TRAIL_ATR_MULT", 2.0)
    )
    # FIX #5: raise ADX minimum (was 22)
    trend_min_adx: float = field(
        default_factory=lambda: _float("TREND_MIN_ADX", 27.0)
    )

    # FIX #3: minimum R:R gate — without this you can't have positive expectancy
    min_rr_trend: float = field(
        default_factory=lambda: _float("MIN_RR_TREND", 2.0)
    )

    # FIX #4: swing lookback (was hardcoded 10 = 50 min noise)
    swing_lookback_bars: int = field(
        default_factory=lambda: _int("SWING_LOOKBACK_BARS", 20)
    )

    # FIX #8: require N consecutive closes above breakout level
    breakout_confirm_bars: int = field(
        default_factory=lambda: _int("BREAKOUT_CONFIRM_BARS", 1)
    )

    # FIX #6: volume must be > mult × rolling average on breakout candle
    volume_spike_min_mult: float = field(
        default_factory=lambda: _float("VOLUME_SPIKE_MIN_MULT", 1.4)
    )
    volume_avg_window: int = field(
        default_factory=lambda: _int("VOLUME_AVG_WINDOW", 20)
    )

    # FIX #9: EMA spread gate (fast-slow gap must be ≥ X% of price)
    ema_min_spread_pct: float = field(
        default_factory=lambda: _float("EMA_MIN_SPREAD_PCT", 0.002)  # 0.2%
    )

    # FIX #7: move SL to break-even + small buffer after partial TP
    move_sl_to_be_after_partial: bool = field(
        default_factory=lambda: _bool("MOVE_SL_TO_BE_AFTER_PARTIAL", True)
    )
    be_buffer_atr_mult: float = field(
        default_factory=lambda: _float("BE_BUFFER_ATR_MULT", 0.15)
    )

    # FIX #10: time-of-day filter (UTC hours)
    trade_start_hour_utc: int = field(
        default_factory=lambda: _int("TRADE_START_HOUR_UTC", 6)   # skip low-liq Asia
    )
    trade_end_hour_utc: int = field(
        default_factory=lambda: _int("TRADE_END_HOUR_UTC", 22)
    )

    # FIX #12: stale-trade timeout → close at break-even
    trade_max_duration_bars: int = field(
        default_factory=lambda: _int("TRADE_MAX_DURATION_BARS", 12)  # 12×5m = 60 min
    )
    # If unrealised P&L < this fraction of risk_usd, exit (avoid slow bleed)
    trade_exit_fraction_of_risk: float = field(
        default_factory=lambda: _float("TRADE_EXIT_FRACTION_OF_RISK", -0.3)
    )

    # ── RANGE module (5m) ────────────────────────────────────────────────
    range_vwap_band_atr_mult: float = field(
        default_factory=lambda: _float("RANGE_VWAP_BAND_ATR_MULT", 1.5)
    )
    range_atr_sl_mult: float = field(
        default_factory=lambda: _float("RANGE_ATR_SL_MULT", 1.2)  # slightly wider
    )
    range_tp_r: float = field(
        default_factory=lambda: _float("RANGE_TP_R", 1.5)
    )
    range_max_adx: float = field(
        default_factory=lambda: _float("RANGE_MAX_ADX", 22.0)  # tighter: ADX<22 = real range
    )
    min_rr_range: float = field(
        default_factory=lambda: _float("MIN_RR_RANGE", 1.5)
    )

    # ── Candle history on startup ─────────────────────────────────────────
    candle_history_bars: int = field(
        default_factory=lambda: _int("CANDLE_HISTORY_BARS", 300)
    )

    # ── Logging / persistence ─────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    journal_path: str = field(
        default_factory=lambda: os.getenv("JOURNAL_PATH", "data/journal.jsonl")
    )
    state_path: str = field(
        default_factory=lambda: os.getenv("STATE_PATH", "data/state.json")
    )
    daily_report_hour_utc: int = field(
        default_factory=lambda: _int("DAILY_REPORT_HOUR_UTC", 0)
    )

    def __post_init__(self) -> None:
        if not self.hl_api_url:
            if self.testnet:
                self.hl_api_url = "https://api.hyperliquid-testnet.xyz"
                self.hl_ws_url = "wss://api.hyperliquid-testnet.xyz/ws"
            else:
                self.hl_api_url = "https://api.hyperliquid.xyz"
                self.hl_ws_url = "wss://api.hyperliquid.xyz/ws"

        self.risk_pct = min(self.risk_pct, self.max_risk_pct)

    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        net = "TESTNET" if self.testnet else "MAINNET"
        return (
            f"[Config] {mode} | {net} | risk={self.risk_pct*100:.2f}% | "
            f"max_pos={self.max_concurrent_positions} | "
            f"daily_loss_cap={self.daily_loss_pct*100:.1f}% | "
            f"min_rr_trend={self.min_rr_trend} | adx_min={self.trend_min_adx}"
        )
