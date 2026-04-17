"""
Execution engine.

Handles:
  - Opening positions (market or limit) with dry-run mode
  - Monitoring open positions: partial TP, trailing stop, hard SL
  - Closing positions (full or partial)
  - Rounding sizes/prices to HL precision
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional, Union

from .config import Config
from .hl_client import HLExchange, HLRestClient
from .risk import Position, RiskManager, RiskVerdict
from .strategies.trend import TrendSignal
from .strategies.range_mean import RangeSignal
from .journal import Journal

log = logging.getLogger(__name__)

Signal = Union[TrendSignal, RangeSignal]


class ExecutionEngine:
    """
    Translates approved signals into exchange orders.
    In dry_run=True mode no orders are sent; everything is simulated.
    """

    def __init__(
        self,
        config: Config,
        exchange: HLExchange,
        risk: RiskManager,
        journal: Journal,
    ) -> None:
        self._cfg = config
        self._ex = exchange
        self._risk = risk
        self._journal = journal

    # ── Open ─────────────────────────────────────────────────────────────

    async def open_position(
        self,
        signal: Signal,
        verdict: RiskVerdict,
        sz_decimals: int = 3,
    ) -> Optional[Position]:
        """
        Place an opening order. Returns the Position object if successful.
        """
        coin = signal.coin
        direction = signal.direction
        is_buy = direction == 1
        size = _round_size(verdict.size, sz_decimals)

        if size <= 0:
            log.warning("%s: computed size rounds to 0 (sz_decimals=%d)", coin, sz_decimals)
            return None

        strategy = "TREND" if isinstance(signal, TrendSignal) else "RANGE"

        # Determine TP/trail params
        if isinstance(signal, TrendSignal):
            tp_partial = signal.tp_partial
            trail_dist = signal.trail_distance
        else:
            tp_partial = signal.tp
            trail_dist = signal.atr * self._cfg.trend_trail_atr_mult

        pos = Position(
            coin=coin,
            direction=direction,
            entry_price=signal.entry,
            size=size,
            sl=signal.sl,
            tp_partial=tp_partial,
            trail_distance=trail_dist,
            atr=signal.atr,
            strategy=strategy,
        )

        if self._cfg.dry_run:
            log.info(
                "[DRY-RUN] OPEN %s %s  size=%.4f entry=%.4f sl=%.4f  risk=%.2f USD",
                coin,
                "LONG" if is_buy else "SHORT",
                size,
                signal.entry,
                signal.sl,
                verdict.risk_usd,
            )
            pos.oid = -1  # sentinel for dry-run
        else:
            try:
                # Set leverage before opening
                await self._ex.set_leverage(
                    coin, self._cfg.default_leverage, is_cross=False
                )
                resp = await self._ex.market_open(
                    coin, is_buy, size, slippage=0.002
                )
                log.info("%s open order response: %s", coin, resp)
                # Extract OID from response if available
                pos.oid = _extract_oid(resp)
                # Use actual fill price if available
                fill = _extract_fill_price(resp)
                if fill:
                    pos.entry_price = fill
            except Exception as exc:
                log.error("Failed to open %s: %s", coin, exc)
                return None

        self._risk.open_position(pos)
        self._journal.log_open(pos, signal, verdict)
        return pos

    # ── Monitor & exit ────────────────────────────────────────────────────

    async def monitor_positions(self, mid_prices: dict) -> None:
        """
        Called every 5m candle close (or more frequently from price updates).
        Checks SL, trailing stop, partial TP for all open positions.
        """
        for pos in list(self._risk.all_positions()):
            coin = pos.coin
            price = mid_prices.get(coin)
            if price is None:
                continue
            price = float(price)

            # Update trailing stop high/low
            self._risk.update_position_trail(coin, price)

            # ── Partial TP (trend only) ───────────────────────────────────
            if (
                not pos.partial_taken
                and isinstance(pos.tp_partial, float)
            ):
                tp_hit = (
                    pos.direction == 1 and price >= pos.tp_partial
                ) or (
                    pos.direction == -1 and price <= pos.tp_partial
                )
                if tp_hit:
                    await self._close_partial(pos, price)

            # ── Trailing stop (after partial) ─────────────────────────────
            if pos.partial_taken:
                trail_sl = pos.current_sl()
                trail_hit = (
                    pos.direction == 1 and price <= trail_sl
                ) or (
                    pos.direction == -1 and price >= trail_sl
                )
                if trail_hit:
                    await self._close_full(pos, price, reason="trail_stop")
                    continue

            # ── Hard stop loss ────────────────────────────────────────────
            sl_hit = (
                pos.direction == 1 and price <= pos.sl
            ) or (
                pos.direction == -1 and price >= pos.sl
            )
            if sl_hit:
                await self._close_full(pos, price, reason="stop_loss")
                continue

            # ── Range TP (full close at target) ──────────────────────────
            if pos.strategy == "RANGE":
                range_tp_hit = (
                    pos.direction == 1 and price >= pos.tp_partial
                ) or (
                    pos.direction == -1 and price <= pos.tp_partial
                )
                if range_tp_hit:
                    await self._close_full(pos, price, reason="tp")

    async def _close_partial(self, pos: Position, price: float) -> None:
        close_size = _round_size(
            pos.size * self._cfg.trend_partial_close_pct,
            sz_decimals=3,
        )
        if close_size <= 0:
            return

        log.info(
            "[%s] PARTIAL TP %s  size=%.4f price=%.4f",
            "DRY-RUN" if self._cfg.dry_run else "LIVE",
            pos.coin,
            close_size,
            price,
        )

        if not self._cfg.dry_run:
            try:
                await self._ex.market_close(pos.coin, sz=close_size)
            except Exception as exc:
                log.error("Partial close error %s: %s", pos.coin, exc)
                return

        # Update position size and mark partial taken
        partial_pnl = self._risk.record_partial_close(pos.coin, price, close_size)
        pos.size -= close_size
        self._risk.mark_partial_taken(pos.coin)
        self._journal.log_partial(pos, price, close_size, partial_pnl or 0.0)

    async def _close_full(self, pos: Position, price: float, reason: str) -> None:
        log.info(
            "[%s] CLOSE %s %s  size=%.4f price=%.4f  reason=%s",
            "DRY-RUN" if self._cfg.dry_run else "LIVE",
            pos.coin,
            "LONG" if pos.direction == 1 else "SHORT",
            pos.size,
            price,
            reason,
        )

        if not self._cfg.dry_run:
            try:
                await self._ex.market_close(pos.coin)
            except Exception as exc:
                log.error("Full close error %s: %s", pos.coin, exc)
                return

        pnl = self._risk.close_position(pos.coin, price, reason=reason)
        self._journal.log_close(pos, price, pnl or 0.0, reason)

    # ── Emergency close all ───────────────────────────────────────────────

    async def close_all(self, mid_prices: dict, reason: str = "emergency") -> None:
        for pos in list(self._risk.all_positions()):
            price = float(mid_prices.get(pos.coin, pos.entry_price))
            await self._close_full(pos, price, reason)


# ── Helpers ───────────────────────────────────────────────────────────────

def _round_size(size: float, sz_decimals: int) -> float:
    factor = 10 ** sz_decimals
    return math.floor(size * factor) / factor


def _extract_oid(resp: dict) -> Optional[int]:
    try:
        return int(resp["response"]["data"]["statuses"][0]["resting"]["oid"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _extract_fill_price(resp: dict) -> Optional[float]:
    try:
        return float(resp["response"]["data"]["statuses"][0]["filled"]["avgPx"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
