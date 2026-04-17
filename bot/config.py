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
    exec_tf: str = "5m"       # signal evaluation timeframe
    regime_tf: str = "15m"    # per-market regime timeframe
    global_tf: str = "1h"     # global BTC filter timeframe
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
    # Extra symbols always subscribed (global filter + held positions added dynamically)
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

    # ── Regime thresholds (15m) ───────────────────────────────────────────
    adx_trend_threshold: float = field(
        default_factory=lambda: _float("ADX_TREND_THRESHOLD", 25.0)
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

    # ── TREND module (5m) ────────────────────────────────────────────────
    ema_fast: int = field(default_factory=lambda: _int("EMA_FAST", 9))
    ema_slow: int = field(default_factory=lambda: _int("EMA_SLOW", 21))
    trend_atr_sl_mult: float = field(
        default_factory=lambda: _float("TREND_ATR_SL_MULT", 1.5)
    )
    trend_partial_tp_r: float = field(
        default_factory=lambda: _float("TREND_PARTIAL_TP_R", 1.0)   # 1R partial
    )
    trend_partial_close_pct: float = field(
        default_factory=lambda: _float("TREND_PARTIAL_CLOSE_PCT", 0.5)
    )
    trend_trail_atr_mult: float = field(
        default_factory=lambda: _float("TREND_TRAIL_ATR_MULT", 2.0)
    )
    trend_min_adx: float = field(
        default_factory=lambda: _float("TREND_MIN_ADX", 22.0)
    )

    # ── RANGE module (5m) ────────────────────────────────────────────────
    range_vwap_band_atr_mult: float = field(
        default_factory=lambda: _float("RANGE_VWAP_BAND_ATR_MULT", 1.5)
    )
    range_atr_sl_mult: float = field(
        default_factory=lambda: _float("RANGE_ATR_SL_MULT", 1.0)
    )
    range_tp_r: float = field(
        default_factory=lambda: _float("RANGE_TP_R", 1.5)
    )
    range_max_adx: float = field(
        default_factory=lambda: _float("RANGE_MAX_ADX", 28.0)  # don't range-trade strong trends
    )

    # ── Candle history on startup ─────────────────────────────────────────
    candle_history_bars: int = field(
        default_factory=lambda: _int("CANDLE_HISTORY_BARS", 200)
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

        # Clamp risk
        self.risk_pct = min(self.risk_pct, self.max_risk_pct)

    # ── Convenience ───────────────────────────────────────────────────────
    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        net = "TESTNET" if self.testnet else "MAINNET"
        return (
            f"[Config] {mode} | {net} | risk={self.risk_pct*100:.2f}% | "
            f"max_pos={self.max_concurrent_positions} | "
            f"daily_loss_cap={self.daily_loss_pct*100:.1f}% | "
            f"weekly_loss_cap={self.weekly_loss_pct*100:.1f}%"
        )
