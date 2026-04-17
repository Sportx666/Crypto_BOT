"""
Risk manager.

Responsibilities:
  - Size positions from % equity risk + ATR stop distance
  - Gate new entries: max concurrent positions, daily/weekly loss limits,
    per-symbol cooldown, directional exposure caps
  - Track open positions and realised P&L
  - Emit halt events when loss limits are breached
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .config import Config

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
#  Data structures
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    coin: str
    direction: int          # +1 long, -1 short
    entry_price: float
    size: float             # contracts / coins
    sl: float
    tp_partial: float       # first take-profit level
    trail_distance: float   # trailing stop distance in price
    atr: float
    opened_at: float = field(default_factory=time.time)
    partial_taken: bool = False
    highest_price: float = 0.0  # for long trailing
    lowest_price: float = 0.0   # for short trailing
    oid: Optional[int] = None   # exchange order id
    strategy: str = ""

    def __post_init__(self) -> None:
        if self.direction == 1:
            self.highest_price = self.entry_price
        else:
            self.lowest_price = self.entry_price

    def notional(self) -> float:
        return self.entry_price * self.size

    def unrealised_pnl(self, current_price: float) -> float:
        return self.direction * (current_price - self.entry_price) * self.size

    def current_sl(self) -> float:
        """Return the current trailing stop level."""
        if self.direction == 1:
            return self.highest_price - self.trail_distance
        else:
            return self.lowest_price + self.trail_distance

    def update_trail(self, current_price: float) -> None:
        if self.direction == 1:
            self.highest_price = max(self.highest_price, current_price)
        else:
            self.lowest_price = min(self.lowest_price, current_price)


@dataclass
class RiskVerdict:
    allowed: bool
    reason: str
    size: float = 0.0       # approved position size (contracts)
    risk_usd: float = 0.0   # USD at risk


# ══════════════════════════════════════════════════════════════════════════
#  Loss tracker (daily + weekly)
# ══════════════════════════════════════════════════════════════════════════

class LossTracker:
    """
    Tracks realised P&L windows.
    Resets: daily at UTC midnight, weekly at UTC Monday midnight.
    """

    def __init__(self) -> None:
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._day_key: str = ""
        self._week_key: str = ""
        self._update_keys()

    def _update_keys(self) -> None:
        now = datetime.now(timezone.utc)
        self._day_key = now.strftime("%Y-%m-%d")
        self._week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    def record(self, pnl: float) -> None:
        """Record a realised trade P&L (positive = profit, negative = loss)."""
        self._maybe_reset()
        self._daily_pnl += pnl
        self._weekly_pnl += pnl
        log.debug(
            "P&L recorded: %.4f | daily=%.4f | weekly=%.4f",
            pnl,
            self._daily_pnl,
            self._weekly_pnl,
        )

    def _maybe_reset(self) -> None:
        now = datetime.now(timezone.utc)
        new_day = now.strftime("%Y-%m-%d")
        new_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if new_day != self._day_key:
            log.info("Daily P&L reset (was %.4f)", self._daily_pnl)
            self._daily_pnl = 0.0
            self._day_key = new_day
        if new_week != self._week_key:
            log.info("Weekly P&L reset (was %.4f)", self._weekly_pnl)
            self._weekly_pnl = 0.0
            self._week_key = new_week

    @property
    def daily_pnl(self) -> float:
        self._maybe_reset()
        return self._daily_pnl

    @property
    def weekly_pnl(self) -> float:
        self._maybe_reset()
        return self._weekly_pnl

    def to_dict(self) -> dict:
        return {
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "day_key": self._day_key,
            "week_key": self._week_key,
        }

    def from_dict(self, d: dict) -> None:
        self._daily_pnl = d.get("daily_pnl", 0.0)
        self._weekly_pnl = d.get("weekly_pnl", 0.0)
        self._day_key = d.get("day_key", "")
        self._week_key = d.get("week_key", "")
        # Force key refresh to handle missed resets during downtime
        self._maybe_reset()


# ══════════════════════════════════════════════════════════════════════════
#  Main risk manager
# ══════════════════════════════════════════════════════════════════════════

class RiskManager:

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._positions: Dict[str, Position] = {}   # coin → Position
        self._cooldowns: Dict[str, float] = {}       # coin → expiry timestamp
        self._loss_tracker = LossTracker()
        self._halted: bool = False
        self._halt_reason: str = ""
        self._halt_auto_resume: bool = False
        self._equity: float = 0.0                    # updated from exchange

    # ── Equity ────────────────────────────────────────────────────────────

    def update_equity(self, equity: float) -> None:
        self._equity = equity

    @property
    def equity(self) -> float:
        return self._equity

    # ── Halt management ───────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        self._maybe_resume_from_loss_reset()
        return self._halted

    def halt(self, reason: str, auto_resume: bool = False) -> None:
        self._halted = True
        self._halt_reason = reason
        self._halt_auto_resume = auto_resume
        log.critical("TRADING HALTED: %s", reason)

    def resume(self) -> None:
        self._halted = False
        self._halt_reason = ""
        self._halt_auto_resume = False
        log.warning("Trading resumed.")

    # ── Position tracking ─────────────────────────────────────────────────

    def open_position(self, pos: Position) -> None:
        self._positions[pos.coin] = pos
        log.info(
            "Position opened: %s %s  size=%.4f entry=%.4f sl=%.4f",
            pos.coin,
            "LONG" if pos.direction == 1 else "SHORT",
            pos.size,
            pos.entry_price,
            pos.sl,
        )

    def close_position(self, coin: str, exit_price: float, reason: str = "") -> Optional[float]:
        """
        Close the position for coin. Returns realised P&L or None if not found.
        """
        pos = self._positions.pop(coin, None)
        if pos is None:
            return None

        pnl = pos.unrealised_pnl(exit_price)
        self._loss_tracker.record(pnl)

        log.info(
            "Position closed: %s %s  exit=%.4f pnl=%+.4f  reason=%s",
            coin,
            "LONG" if pos.direction == 1 else "SHORT",
            exit_price,
            pnl,
            reason or "-",
        )

        # Set cooldown on stopout
        if "stop" in reason.lower() or pnl < 0:
            self.set_cooldown(coin)

        # Check loss limits
        self._check_loss_limits()
        return pnl

    def get_position(self, coin: str) -> Optional[Position]:
        return self._positions.get(coin)

    def all_positions(self) -> List[Position]:
        return list(self._positions.values())

    def held_coins(self) -> Set[str]:
        return set(self._positions.keys())

    def position_count(self) -> int:
        return len(self._positions)

    def update_position_trail(self, coin: str, current_price: float) -> None:
        pos = self._positions.get(coin)
        if pos:
            pos.update_trail(current_price)

    def mark_partial_taken(self, coin: str) -> None:
        pos = self._positions.get(coin)
        if pos:
            pos.partial_taken = True

    def record_partial_close(self, coin: str, exit_price: float, close_size: float) -> Optional[float]:
        """
        Record realised PnL for a partial close without removing the position.
        Returns realised PnL or None if the position/size is invalid.
        """
        pos = self._positions.get(coin)
        if pos is None or close_size <= 0:
            return None
        if close_size > pos.size:
            close_size = pos.size
        pnl = pos.direction * (exit_price - pos.entry_price) * close_size
        self._loss_tracker.record(pnl)
        self._check_loss_limits()
        return pnl

    # ── Cooldowns ────────────────────────────────────────────────────────

    def set_cooldown(self, coin: str, minutes: Optional[int] = None) -> None:
        mins = minutes if minutes is not None else self._cfg.cooldown_after_stop_min
        self._cooldowns[coin] = time.time() + mins * 60
        log.info("%s: cooldown set for %d min", coin, mins)

    def in_cooldown(self, coin: str) -> bool:
        expiry = self._cooldowns.get(coin, 0)
        if time.time() < expiry:
            return True
        if coin in self._cooldowns:
            del self._cooldowns[coin]
        return False

    def cooldown_remaining(self, coin: str) -> float:
        """Seconds remaining in cooldown, 0 if not in cooldown."""
        expiry = self._cooldowns.get(coin, 0)
        return max(0.0, expiry - time.time())

    # ── Entry gating ──────────────────────────────────────────────────────

    def check_entry(
        self,
        coin: str,
        direction: int,
        r_distance: float,
    ) -> RiskVerdict:
        """
        Full pre-entry risk check. Returns RiskVerdict with allowed flag + size.

        r_distance: |entry - stop_loss| in price terms.
        """
        self._maybe_resume_from_loss_reset()

        # ── Global halt ──────────────────────────────────────────────────
        if self._halted:
            return RiskVerdict(False, f"HALTED: {self._halt_reason}")

        # ── Equity available ─────────────────────────────────────────────
        if self._equity <= 0:
            return RiskVerdict(False, "Equity not set")

        # ── Already have position ────────────────────────────────────────
        if coin in self._positions:
            return RiskVerdict(False, f"{coin}: position already open")

        # ── Cooldown ─────────────────────────────────────────────────────
        if self.in_cooldown(coin):
            rem = self.cooldown_remaining(coin)
            return RiskVerdict(False, f"{coin}: cooldown {rem/60:.1f} min remaining")

        # ── Max concurrent positions ──────────────────────────────────────
        if self.position_count() >= self._cfg.max_concurrent_positions:
            return RiskVerdict(
                False,
                f"Max positions ({self._cfg.max_concurrent_positions}) reached",
            )

        # ── Directional exposure cap ──────────────────────────────────────
        verdict = self._check_exposure(direction)
        if not verdict.allowed:
            return verdict

        # ── Loss limits ───────────────────────────────────────────────────
        daily_loss = self._loss_tracker.daily_pnl
        weekly_loss = self._loss_tracker.weekly_pnl
        daily_limit = -self._equity * self._cfg.daily_loss_pct
        weekly_limit = -self._equity * self._cfg.weekly_loss_pct

        if daily_loss <= daily_limit:
            self.halt(f"Daily loss limit hit ({daily_loss:.2f} USD)", auto_resume=True)
            return RiskVerdict(False, self._halt_reason)
        if weekly_loss <= weekly_limit:
            self.halt(f"Weekly loss limit hit ({weekly_loss:.2f} USD)", auto_resume=True)
            return RiskVerdict(False, self._halt_reason)

        # ── Position sizing ───────────────────────────────────────────────
        size, risk_usd = self._compute_size(r_distance)
        if size <= 0:
            return RiskVerdict(False, "Computed size is zero – r_distance too large?")

        return RiskVerdict(allowed=True, reason="OK", size=size, risk_usd=risk_usd)

    def _compute_size(self, r_distance: float) -> Tuple[float, float]:
        """
        Risk-based position sizing.
        risk_usd = equity × risk_pct
        size = risk_usd / r_distance
        """
        risk_usd = self._equity * self._cfg.risk_pct
        if r_distance <= 0:
            return 0.0, 0.0
        size = risk_usd / r_distance
        return size, risk_usd

    def _check_exposure(self, direction: int) -> RiskVerdict:
        long_notional = sum(
            p.notional() for p in self._positions.values() if p.direction == 1
        )
        short_notional = sum(
            p.notional() for p in self._positions.values() if p.direction == -1
        )
        max_long = self._equity * self._cfg.max_long_notional_pct
        max_short = self._equity * self._cfg.max_short_notional_pct

        if direction == 1 and long_notional >= max_long:
            return RiskVerdict(
                False,
                f"Long exposure cap reached ({long_notional:.0f}/{max_long:.0f} USD)",
            )
        if direction == -1 and short_notional >= max_short:
            return RiskVerdict(
                False,
                f"Short exposure cap reached ({short_notional:.0f}/{max_short:.0f} USD)",
            )
        return RiskVerdict(True, "OK")

    def _check_loss_limits(self) -> None:
        if self._equity <= 0:
            return
        daily = self._loss_tracker.daily_pnl
        weekly = self._loss_tracker.weekly_pnl
        if daily <= -self._equity * self._cfg.daily_loss_pct:
            self.halt(
                f"Daily loss {daily:.2f} exceeds {self._cfg.daily_loss_pct*100:.1f}%",
                auto_resume=True,
            )
        elif weekly <= -self._equity * self._cfg.weekly_loss_pct:
            self.halt(
                f"Weekly loss {weekly:.2f} exceeds {self._cfg.weekly_loss_pct*100:.1f}%",
                auto_resume=True,
            )

    def _maybe_resume_from_loss_reset(self) -> None:
        if not self._halted or not self._halt_auto_resume or self._equity <= 0:
            return
        daily = self._loss_tracker.daily_pnl
        weekly = self._loss_tracker.weekly_pnl
        daily_limit = -self._equity * self._cfg.daily_loss_pct
        weekly_limit = -self._equity * self._cfg.weekly_loss_pct
        if daily > daily_limit and weekly > weekly_limit:
            log.info(
                "Auto-resuming after reset window (daily=%.2f weekly=%.2f)",
                daily,
                weekly,
            )
            self.resume()

    # ── State snapshot for persistence ───────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "halt_auto_resume": self._halt_auto_resume,
            "loss_tracker": self._loss_tracker.to_dict(),
            "cooldowns": self._cooldowns,
            "positions": {
                coin: {
                    "coin": p.coin,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "size": p.size,
                    "sl": p.sl,
                    "tp_partial": p.tp_partial,
                    "trail_distance": p.trail_distance,
                    "atr": p.atr,
                    "opened_at": p.opened_at,
                    "partial_taken": p.partial_taken,
                    "highest_price": p.highest_price,
                    "lowest_price": p.lowest_price,
                    "oid": p.oid,
                    "strategy": p.strategy,
                }
                for coin, p in self._positions.items()
            },
        }

    def from_dict(self, d: dict) -> None:
        self._halted = d.get("halted", False)
        self._halt_reason = d.get("halt_reason", "")
        self._halt_auto_resume = d.get("halt_auto_resume", False)
        if "loss_tracker" in d:
            self._loss_tracker.from_dict(d["loss_tracker"])
        self._cooldowns = {k: float(v) for k, v in d.get("cooldowns", {}).items()}
        for coin, pd_ in d.get("positions", {}).items():
            p = Position(
                coin=pd_["coin"],
                direction=pd_["direction"],
                entry_price=pd_["entry_price"],
                size=pd_["size"],
                sl=pd_["sl"],
                tp_partial=pd_["tp_partial"],
                trail_distance=pd_["trail_distance"],
                atr=pd_["atr"],
                opened_at=pd_.get("opened_at", time.time()),
                partial_taken=pd_.get("partial_taken", False),
                oid=pd_.get("oid"),
                strategy=pd_.get("strategy", ""),
            )
            p.highest_price = pd_.get("highest_price", p.entry_price)
            p.lowest_price = pd_.get("lowest_price", p.entry_price)
            self._positions[coin] = p

    # ── Summary ───────────────────────────────────────────────────────────

    def summary(self) -> str:
        n = self.position_count()
        daily = self._loss_tracker.daily_pnl
        weekly = self._loss_tracker.weekly_pnl
        halt = " [HALTED]" if self._halted else ""
        return (
            f"[Risk]{halt} positions={n}/{self._cfg.max_concurrent_positions} | "
            f"daily_pnl={daily:+.2f} | weekly_pnl={weekly:+.2f} | "
            f"equity={self._equity:.2f}"
        )
