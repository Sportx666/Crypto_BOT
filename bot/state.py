"""
Restart-safe state persistence.

Serialises/deserialises the bot's runtime state to a JSON file so that
positions, cooldowns, and loss counters survive process restarts.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .risk import RiskManager

log = logging.getLogger(__name__)


class StateManager:
    """Atomic JSON persistence for runtime state."""

    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    # ── Save ─────────────────────────────────────────────────────────────

    def save(self, risk: "RiskManager") -> None:
        state = {
            "saved_at": time.time(),
            "risk": risk.to_dict(),
        }
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            shutil.move(tmp, self._path)
            log.debug("State saved → %s", self._path)
        except OSError as exc:
            log.error("State save failed: %s", exc)

    # ── Load ─────────────────────────────────────────────────────────────

    def load(self, risk: "RiskManager") -> bool:
        """
        Load state into risk manager. Returns True if state was restored.
        """
        if not os.path.exists(self._path):
            log.info("No saved state found – starting fresh")
            return False
        try:
            with open(self._path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("State load failed: %s – starting fresh", exc)
            return False

        saved_at = state.get("saved_at", 0)
        age_min = (time.time() - saved_at) / 60

        if "risk" in state:
            risk.from_dict(state["risk"])
            log.info(
                "State restored from %.1f min ago | positions=%d | halted=%s",
                age_min,
                risk.position_count(),
                risk.is_halted,
            )
            return True
        return False

    def delete(self) -> None:
        if os.path.exists(self._path):
            os.remove(self._path)
            log.info("State file deleted")
