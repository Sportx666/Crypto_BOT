"""
CryptoBOT – Hyperliquid perps engine.
Main async orchestration loop.

Architecture:
  - WS receives candle updates → CandleCache
  - On 5m candle close: regime classify → strategy evaluate → risk check → execute
  - Every universe_scan_interval_min: rescan universe, diff active set
  - Every daily_report_hour_utc: emit daily summary
  - State saved every 60s; restored on restart
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from .config import Config
from .hl_client import HLRestClient, HLWebSocket, HLExchange
from .candles import CandleCache, seed_candles
from .indicators import compute_all, last_bar
from .regime import RegimeEngine, MarketRegime
from .universe import UniverseScanner
from .strategies.trend import TrendStrategy
from .strategies.range_mean import RangeStrategy
from .risk import RiskManager
from .execution import ExecutionEngine
from .journal import Journal
from .state import StateManager

log = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    fmt = "%(asctime)s %(levelname)-8s %(name)-20s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("data/bot.log", encoding="utf-8"),
        ],
    )
    # Quiet noisy libraries
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════
#  Bot
# ══════════════════════════════════════════════════════════════════════════

class CryptoBot:

    TIMEFRAMES = ("5m", "15m", "1h")   # all intervals we need
    STATE_SAVE_INTERVAL = 60            # seconds
    EQUITY_REFRESH_INTERVAL = 120       # seconds

    def __init__(self, config: Config) -> None:
        self._cfg = config

        # Clients
        self._rest = HLRestClient(config.hl_api_url)
        self._ws = HLWebSocket(config.hl_ws_url)
        self._ex = HLExchange(
            config.hl_api_url,
            config.hl_wallet_address,
            config.hl_private_key,
        )

        # Core components
        self._cache = CandleCache(max_bars=500)
        self._regime = RegimeEngine(config, self._cache)
        self._universe = UniverseScanner(config, self._rest)
        self._trend = TrendStrategy(config)
        self._range = RangeStrategy(config)
        self._risk = RiskManager(config)
        self._journal = Journal(config.journal_path)
        self._state = StateManager(config.state_path)
        self._exec = ExecutionEngine(config, self._ex, self._risk, self._journal)

        # Runtime state
        self._active_set: Set[str] = set()
        self._mid_prices: Dict[str, float] = {}
        self._last_universe_scan: float = 0.0
        self._last_state_save: float = 0.0
        self._last_equity_refresh: float = 0.0
        self._last_daily_report_date: str = ""
        self._trade_count_today: int = 0
        self._stopping = False

    # ── Startup ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        log.info("=" * 60)
        log.info("CryptoBOT starting  %s", self._cfg.summary())
        log.info("=" * 60)

        os.makedirs("data", exist_ok=True)

        # Restore state
        self._state.load(self._risk)

        # Initial equity fetch
        await self._refresh_equity()

        # Universe scan (first pass)
        await self._do_universe_scan()

        # Seed candle history for active set + BTC
        seed_symbols = list(self._active_set) + [self._cfg.global_symbol]
        log.info("Seeding candles for %d symbols…", len(seed_symbols))
        await seed_candles(
            self._cache,
            self._rest,
            seed_symbols,
            list(self.TIMEFRAMES),
            n_bars=self._cfg.candle_history_bars,
        )

        # Wire WS callback
        self._ws.add_candle_callback(self._on_ws_candle)

        # Subscribe
        await self._subscribe_active_set()

        # Subscribe to user events if we have a wallet
        if self._cfg.hl_wallet_address:
            await self._ws.subscribe_user_events(self._cfg.hl_wallet_address)

        log.info("Startup complete. Active set: %s", sorted(self._active_set))

    async def run(self) -> None:
        """Run the bot. Blocks until stop() is called."""
        await self.start()

        # Start WS in background
        ws_task = asyncio.create_task(self._ws.run(), name="ws-loop")
        # Start housekeeping loop
        hk_task = asyncio.create_task(self._housekeeping_loop(), name="housekeeping")

        try:
            await asyncio.gather(ws_task, hk_task)
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        self._stopping = True
        await self._ws.stop()

    # ── WS candle callback ────────────────────────────────────────────────

    def _on_ws_candle(self, coin: str, interval: str, bar: dict) -> None:
        """
        Called from WS loop (may be a different thread in some implementations).
        Writes to CandleCache; schedules evaluation on close via asyncio.
        """
        closed = self._cache.on_ws_candle(coin, interval, bar)

        # Update mid price from latest close
        close = bar.get("c")
        if close:
            self._mid_prices[coin] = float(close)

        if closed and interval == self._cfg.exec_tf:
            # Schedule evaluation on the event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda c=coin: asyncio.ensure_future(self._on_candle_close(c))
                )

    # ── Candle close handler ──────────────────────────────────────────────

    async def _on_candle_close(self, coin: str) -> None:
        """
        Core signal evaluation pipeline on every 5m candle close.
        """
        if self._stopping or self._risk.is_halted:
            return

        # Skip BTC (global filter only, not traded here)
        if coin == self._cfg.global_symbol:
            return

        # Need 5m data
        df_5m = self._cache.get(coin, self._cfg.exec_tf, n=100)
        if df_5m is None or len(df_5m) < 50:
            return

        # ── Global bias ────────────────────────────────────────────────
        global_ctx = self._regime.global_bias()

        # ── Per-market regime ──────────────────────────────────────────
        regime_result = self._regime.classify(coin)

        if not RegimeEngine.is_tradeable(regime_result):
            return

        signal = None
        if regime_result.regime == MarketRegime.TREND:
            signal = self._trend.evaluate(coin, df_5m, regime_result, global_ctx)
        elif regime_result.regime == MarketRegime.RANGE:
            signal = self._range.evaluate(coin, df_5m, regime_result, global_ctx)

        if signal is None:
            return

        # ── Risk gate ──────────────────────────────────────────────────
        verdict = self._risk.check_entry(
            coin, signal.direction, signal.r_distance
        )

        if not verdict.allowed:
            log.debug(
                "%s signal blocked by risk: %s", coin, verdict.reason
            )
            self._journal.log_signal(
                coin,
                regime_result.regime.value,
                "TREND" if regime_result.regime == MarketRegime.TREND else "RANGE",
                f"BLOCKED: {verdict.reason}",
            )
            return

        # ── Get market precision ────────────────────────────────────────
        mi = self._universe.get_market_info(coin)
        sz_decimals = mi.sz_decimals if mi else 3

        # ── Execute ────────────────────────────────────────────────────
        pos = await self._exec.open_position(signal, verdict, sz_decimals)
        if pos:
            self._trade_count_today += 1
            # Ensure this coin stays subscribed while we hold the position
            self._risk_manager_update_held()

    # ── Housekeeping loop ─────────────────────────────────────────────────

    async def _housekeeping_loop(self) -> None:
        """Periodic tasks: universe scan, equity refresh, state save, daily report."""
        while not self._stopping:
            now = time.time()

            # Universe rescan
            if now - self._last_universe_scan >= self._cfg.universe_scan_interval_min * 60:
                await self._do_universe_scan_safe()

            # Monitor open positions (trailing / SL / TP)
            if self._mid_prices:
                await self._exec.monitor_positions(self._mid_prices)

            # Equity refresh
            if now - self._last_equity_refresh >= self.EQUITY_REFRESH_INTERVAL:
                await self._refresh_equity_safe()

            # State persistence
            if now - self._last_state_save >= self.STATE_SAVE_INTERVAL:
                self._state.save(self._risk)
                self._last_state_save = now

            # Daily summary
            await self._maybe_daily_report()

            await asyncio.sleep(10)  # poll every 10s

    async def _do_universe_scan(self) -> None:
        self._universe.update_held_positions(self._risk.held_coins())
        to_sub, to_unsub = await self._universe.scan()
        self._active_set = set(self._universe.current_active())

        for coin in to_sub:
            for tf in self.TIMEFRAMES:
                await self._ws.subscribe_candles(coin, tf)

        for coin in to_unsub:
            # Only unsubscribe if we don't hold a position
            if coin not in self._risk.held_coins():
                for tf in self.TIMEFRAMES:
                    await self._ws.unsubscribe_candles(coin, tf)

        # Seed any new symbols we just subscribed
        if to_sub:
            await seed_candles(
                self._cache,
                self._rest,
                to_sub,
                list(self.TIMEFRAMES),
                n_bars=self._cfg.candle_history_bars,
                concurrency=3,
            )

        self._last_universe_scan = time.time()

    async def _do_universe_scan_safe(self) -> None:
        try:
            await self._do_universe_scan()
        except Exception as exc:
            log.error("Universe scan error: %s", exc)

    async def _subscribe_active_set(self) -> None:
        for coin in self._active_set:
            for tf in self.TIMEFRAMES:
                await self._ws.subscribe_candles(coin, tf)

    async def _refresh_equity(self) -> None:
        if not self._cfg.hl_wallet_address:
            log.debug("No wallet address set – skipping equity refresh")
            return
        try:
            state = await self._rest.get_user_state(self._cfg.hl_wallet_address)
            margin = state.get("marginSummary", {})
            equity = float(margin.get("accountValue", 0))
            if equity > 0:
                self._risk.update_equity(equity)
                log.debug("Equity refreshed: %.2f", equity)
        except Exception as exc:
            log.warning("Equity refresh failed: %s", exc)
        self._last_equity_refresh = time.time()

    async def _refresh_equity_safe(self) -> None:
        try:
            await self._refresh_equity()
        except Exception as exc:
            log.warning("Equity refresh error: %s", exc)

    def _risk_manager_update_held(self) -> None:
        self._universe.update_held_positions(self._risk.held_coins())

    async def _maybe_daily_report(self) -> None:
        now = datetime.now(timezone.utc)
        if (
            now.hour == self._cfg.daily_report_hour_utc
            and now.strftime("%Y-%m-%d") != self._last_daily_report_date
        ):
            self._last_daily_report_date = now.strftime("%Y-%m-%d")
            stats = self._journal.today_stats()
            self._journal.log_daily_summary(
                equity=self._risk.equity,
                daily_pnl=stats["total_pnl"],
                weekly_pnl=0.0,  # filled by loss tracker if needed
                n_trades=stats["n_trades"],
                win_rate=stats["win_rate"],
            )

    async def _shutdown(self) -> None:
        log.info("Shutting down…")
        self._state.save(self._risk)
        await self._rest.close()
        log.info("Shutdown complete.")


# ══════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════

async def _async_main() -> None:
    cfg = Config()
    setup_logging(cfg.log_level)
    bot = CryptoBot(cfg)

    loop = asyncio.get_running_loop()

    def _sig_handler():
        log.warning("Signal received – stopping…")
        asyncio.ensure_future(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _sig_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals
            pass

    await bot.run()


def main() -> None:
    # Windows: aiohttp/aiodns requires SelectorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
