"""
Shared State
============
Global singleton accessible from all modules.
Holds the GUI instance, trade cooldowns, and market regime cache.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import threading

# ── GUI instance ──────────────────────────────────────────────────────────────
gui_instance = None

def set_gui_instance(gui):
    global gui_instance
    gui_instance = gui

def get_gui_instance():
    return gui_instance


# ── Trade cooldown ─────────────────────────────────────────────────────────────
# Prevents the bot from re-entering the same pair immediately after a trade,
# which caused 46/76 consecutive same-pair entries in the backtest.
#
# Default cooldown: 30 minutes per pair after any trade (WIN or LOSS).
# Configurable via config["PAIR_COOLDOWN_MINUTES"] in misc/config.py.

_cooldown_lock  = threading.Lock()
_trade_cooldowns: dict[str, datetime] = {}   # {symbol: last_trade_time}

COOLDOWN_MINUTES_DEFAULT = 30


def set_pair_cooldown(pair: str, minutes: Optional[int] = None):
    """
    Record that `pair` was just traded.  It will be blocked from
    new entries for `minutes` minutes (default COOLDOWN_MINUTES_DEFAULT).
    """
    from misc.config import config as _cfg
    cooldown = minutes or _cfg.get("PAIR_COOLDOWN_MINUTES", COOLDOWN_MINUTES_DEFAULT)
    with _cooldown_lock:
        _trade_cooldowns[pair] = datetime.now() + timedelta(minutes=cooldown)


def is_pair_in_cooldown(pair: str) -> bool:
    """Return True if the pair is still within its cooldown window."""
    with _cooldown_lock:
        expires = _trade_cooldowns.get(pair)
    if expires is None:
        return False
    return datetime.now() < expires


def cooldown_remaining_minutes(pair: str) -> float:
    """Return remaining cooldown in minutes (0 if not cooling)."""
    with _cooldown_lock:
        expires = _trade_cooldowns.get(pair)
    if expires is None:
        return 0.0
    remaining = (expires - datetime.now()).total_seconds() / 60
    return max(0.0, remaining)


def clear_all_cooldowns():
    """Clear all cooldowns (e.g. on bot restart)."""
    with _cooldown_lock:
        _trade_cooldowns.clear()


def get_cooled_pairs() -> dict[str, float]:
    """Return {pair: remaining_minutes} for all pairs currently cooling."""
    with _cooldown_lock:
        snapshot = dict(_trade_cooldowns)
    now = datetime.now()
    return {
        p: max(0.0, (exp - now).total_seconds() / 60)
        for p, exp in snapshot.items()
        if now < exp
    }
