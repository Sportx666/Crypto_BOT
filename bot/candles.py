"""
Rolling OHLCV candle cache.

  CandleCache  – stores per-(coin, interval) DataFrames, thread-safe
  raw_to_row   – convert HL WS/REST candle dict → (ts, o, h, l, c, v, n)
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .hl_client import HLRestClient, _interval_to_ms

log = logging.getLogger(__name__)

# Column names
COLS = ["ts", "open", "high", "low", "close", "volume", "n_trades"]


def raw_to_row(bar: dict) -> Tuple:
    """Convert raw HL candle dict to a tuple matching COLS."""
    return (
        int(bar["t"]),                  # open-time ms
        float(bar["o"]),
        float(bar["h"]),
        float(bar["l"]),
        float(bar["c"]),
        float(bar["v"]),
        int(bar.get("n", 0)),
    )


class CandleCache:
    """
    In-memory OHLCV cache.

    Stores up to `max_bars` closed candles per (coin, interval).
    The most recent bar in the DataFrame is ALWAYS a closed bar
    (the in-flight WS bar is held in a separate slot and NOT included
    until the next open_time arrives, signalling the bar is complete).

    Thread-safe: all public methods can be called from callbacks or coroutines.
    """

    def __init__(self, max_bars: int = 500) -> None:
        self._max_bars = max_bars
        # (coin, interval) → pd.DataFrame  [closed bars only]
        self._data: Dict[Tuple[str, str], pd.DataFrame] = {}
        # (coin, interval) → last open_time seen (to detect new bar)
        self._last_open: Dict[Tuple[str, str], int] = {}
        # (coin, interval) → in-flight bar (not yet closed)
        self._inflight: Dict[Tuple[str, str], tuple] = {}
        self._lock = threading.Lock()

    # ── Seeding from REST ─────────────────────────────────────────────────

    def seed(self, coin: str, interval: str, bars: List[dict]) -> None:
        """Bulk-insert historical bars (from REST). Oldest → newest order."""
        if not bars:
            return
        rows = [raw_to_row(b) for b in bars]
        df = pd.DataFrame(rows, columns=COLS).drop_duplicates("ts").sort_values("ts")
        df = df.tail(self._max_bars).reset_index(drop=True)
        with self._lock:
            key = (coin, interval)
            self._data[key] = df
            if not df.empty:
                self._last_open[key] = int(df["ts"].iloc[-1])
        log.debug("Seeded %s %s with %d bars", coin, interval, len(df))

    # ── WS update ────────────────────────────────────────────────────────

    def on_ws_candle(self, coin: str, interval: str, bar: dict) -> bool:
        """
        Process a WS candle update.

        Returns True if a NEW closed bar was just added (i.e. candle close event).
        """
        key = (coin, interval)
        open_time = int(bar["t"])
        row = raw_to_row(bar)

        with self._lock:
            prev_open = self._last_open.get(key, 0)

            if open_time > prev_open:
                # A new bar has opened → the PREVIOUS in-flight bar is now closed
                prev_inflight = self._inflight.get(key)
                if prev_inflight is not None and prev_open != 0:
                    self._append_closed(key, prev_inflight)
                # Track new in-flight bar
                self._inflight[key] = row
                self._last_open[key] = open_time
                return prev_inflight is not None  # True only if we just closed a bar
            else:
                # Same bar updating in real-time
                self._inflight[key] = row
                return False

    def _append_closed(self, key: Tuple[str, str], row: tuple) -> None:
        df = self._data.get(key)
        new_row = pd.DataFrame([row], columns=COLS)
        if df is None or df.empty:
            self._data[key] = new_row
        else:
            df = pd.concat([df, new_row], ignore_index=True)
            if len(df) > self._max_bars:
                df = df.iloc[-self._max_bars :]
            self._data[key] = df

    # ── Read ─────────────────────────────────────────────────────────────

    def get(
        self,
        coin: str,
        interval: str,
        n: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Return closed-bar DataFrame for (coin, interval).
        Most recent bar is last row. Returns None if not seeded.
        """
        with self._lock:
            df = self._data.get((coin, interval))
            if df is None or df.empty:
                return None
            if n is not None:
                df = df.tail(n).reset_index(drop=True)
            return df.copy()

    def latest_close(self, coin: str, interval: str) -> Optional[float]:
        with self._lock:
            df = self._data.get((coin, interval))
            if df is None or df.empty:
                return None
            return float(df["close"].iloc[-1])

    def is_seeded(self, coin: str, interval: str, min_bars: int = 50) -> bool:
        with self._lock:
            df = self._data.get((coin, interval))
            return df is not None and len(df) >= min_bars

    def available_symbols(self, interval: str) -> List[str]:
        with self._lock:
            return [coin for (coin, iv) in self._data if iv == interval]


# ══════════════════════════════════════════════════════════════════════════
#  Async seeder
# ══════════════════════════════════════════════════════════════════════════

async def seed_candles(
    cache: CandleCache,
    rest: HLRestClient,
    symbols: List[str],
    intervals: List[str],
    n_bars: int = 200,
    concurrency: int = 5,
) -> None:
    """
    Fetch historical candles for (symbols × intervals) concurrently.
    Fills the cache; skips if already seeded.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _fetch(coin: str, interval: str) -> None:
        if cache.is_seeded(coin, interval, min_bars=n_bars // 2):
            return
        async with sem:
            try:
                bars = await rest.get_candles_latest(coin, interval, n_bars)
                # Drop the last (potentially in-flight) bar
                closed_bars = bars[:-1] if bars else bars
                cache.seed(coin, interval, closed_bars)
            except Exception as exc:
                log.warning("Failed to seed %s %s: %s", coin, interval, exc)

    tasks = [_fetch(coin, iv) for coin in symbols for iv in intervals]
    await asyncio.gather(*tasks)
    log.info(
        "Seeded %d symbol × %d interval combinations",
        len(symbols),
        len(intervals),
    )
