"""
Trade journal – JSONL append-only log + daily summary.

Each trade event is one JSON line in the journal file.
Event types: open, partial, close, daily_summary
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from .risk import Position, RiskVerdict
    from .strategies.trend import TrendSignal
    from .strategies.range_mean import RangeSignal

log = logging.getLogger(__name__)


class Journal:
    """Append-only JSONL trade journal with daily summary helper."""

    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        log.info("Journal: %s", path)

    # ── Write helpers ─────────────────────────────────────────────────────

    def _append(self, record: dict) -> None:
        record.setdefault("ts", _now_iso())
        record.setdefault("ts_unix", time.time())
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error("Journal write error: %s", exc)

    # ── Event logging ─────────────────────────────────────────────────────

    def log_open(
        self,
        pos: "Position",
        signal: Union["TrendSignal", "RangeSignal"],
        verdict: "RiskVerdict",
    ) -> None:
        self._append(
            {
                "event": "open",
                "coin": pos.coin,
                "direction": "LONG" if pos.direction == 1 else "SHORT",
                "strategy": pos.strategy,
                "entry": pos.entry_price,
                "sl": pos.sl,
                "tp_partial": pos.tp_partial,
                "trail_distance": pos.trail_distance,
                "size": pos.size,
                "risk_usd": verdict.risk_usd,
                "atr": pos.atr,
                "signal_score": getattr(signal, "score", 0.0),
                "signal_reason": getattr(signal, "reason", ""),
            }
        )

    def log_partial(
        self,
        pos: "Position",
        price: float,
        close_size: float,
        partial_pnl: float,
    ) -> None:
        self._append(
            {
                "event": "partial",
                "coin": pos.coin,
                "price": price,
                "close_size": close_size,
                "partial_pnl": partial_pnl,
                "remaining_size": pos.size,
            }
        )

    def log_close(
        self,
        pos: "Position",
        exit_price: float,
        pnl: float,
        reason: str,
    ) -> None:
        duration = time.time() - pos.opened_at
        self._append(
            {
                "event": "close",
                "coin": pos.coin,
                "direction": "LONG" if pos.direction == 1 else "SHORT",
                "strategy": pos.strategy,
                "entry": pos.entry_price,
                "exit": exit_price,
                "size": pos.size,
                "pnl": pnl,
                "duration_min": round(duration / 60, 1),
                "reason": reason,
                "partial_taken": pos.partial_taken,
            }
        )

    def log_signal(self, coin: str, regime: str, signal_type: str, detail: str) -> None:
        """Log a signal that was evaluated but not traded (filtered by risk)."""
        self._append(
            {
                "event": "signal",
                "coin": coin,
                "regime": regime,
                "signal_type": signal_type,
                "detail": detail,
            }
        )

    def log_daily_summary(
        self,
        equity: float,
        daily_pnl: float,
        weekly_pnl: float,
        n_trades: int,
        win_rate: Optional[float] = None,
    ) -> None:
        self._append(
            {
                "event": "daily_summary",
                "equity": equity,
                "daily_pnl": daily_pnl,
                "weekly_pnl": weekly_pnl,
                "n_trades": n_trades,
                "win_rate": win_rate,
            }
        )
        log.info(
            "[Daily Summary] equity=%.2f | daily_pnl=%+.2f | weekly_pnl=%+.2f | "
            "trades=%d | win_rate=%s",
            equity,
            daily_pnl,
            weekly_pnl,
            n_trades,
            f"{win_rate*100:.1f}%" if win_rate is not None else "n/a",
        )

    # ── Read helpers ──────────────────────────────────────────────────────

    def read_all(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        records = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def today_stats(self) -> dict:
        """Return quick stats for today's trades."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        closes = [
            r for r in self.read_all()
            if r.get("event") == "close" and r.get("ts", "").startswith(today)
        ]
        if not closes:
            return {"n_trades": 0, "win_rate": None, "total_pnl": 0.0}
        wins = sum(1 for r in closes if r.get("pnl", 0) > 0)
        total_pnl = sum(r.get("pnl", 0) for r in closes)
        return {
            "n_trades": len(closes),
            "win_rate": wins / len(closes),
            "total_pnl": total_pnl,
        }


# ── Helpers ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
