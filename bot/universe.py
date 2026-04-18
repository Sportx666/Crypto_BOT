"""
Universe scanner & active-set manager.

Responsibilities:
  1. Fetch full perp universe from HL REST (metadata + 24h volume proxy).
  2. Filter by quality gates (volume, spread, blacklist).
  3. Rank surviving symbols by a fast composite score (ATR%, volume, regime).
  4. Maintain an "active set" = top-N candidates + currently-held positions.
  5. Diff old vs new active set → return symbols to subscribe / unsubscribe.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .config import Config
from .hl_client import HLRestClient

log = logging.getLogger(__name__)

# Symbols never tradeable (stables, index tokens, etc.)
_BLACKLIST: Set[str] = {
    "USDC", "USDT", "BUSD", "DAI", "TUSD", "FRAX",
    "USDE", "USDX", "FDUSD",
    "BTC",  # always subscribed but not traded directly (global filter)
}


@dataclass
class MarketInfo:
    coin: str
    max_leverage: int
    sz_decimals: int          # precision for size
    # Enriched from assetCtxs
    open_interest_usd: float = 0.0
    funding_rate: float = 0.0
    mid_price: float = 0.0
    volume_24h_approx: float = 0.0  # OI proxy when true volume unavailable
    score: float = 0.0


@dataclass
class ActiveSet:
    symbols: List[str] = field(default_factory=list)
    updated_at: float = 0.0

    def age_seconds(self) -> float:
        return time.time() - self.updated_at


class UniverseScanner:
    """
    Periodically scans the full HL perp universe and maintains an active set
    of symbols to subscribe/monitor via WebSocket.
    """

    def __init__(self, config: Config, rest: HLRestClient) -> None:
        self._cfg = config
        self._rest = rest
        self._universe: Dict[str, MarketInfo] = {}
        self._active: ActiveSet = ActiveSet()
        self._held_positions: Set[str] = set()  # updated externally by risk manager

    # ── External API ─────────────────────────────────────────────────────

    def update_held_positions(self, coins: Set[str]) -> None:
        self._held_positions = coins

    def get_market_info(self, coin: str) -> Optional[MarketInfo]:
        return self._universe.get(coin)

    def current_active(self) -> List[str]:
        return list(self._active.symbols)

    # ── Main scan ────────────────────────────────────────────────────────

    async def scan(self) -> Tuple[List[str], List[str]]:
        """
        Full universe scan.

        Returns:
            (to_subscribe, to_unsubscribe)  — diff vs previous active set
        """
        log.info("Universe scan started")
        prev = set(self._active.symbols)

        try:
            await self._fetch_universe()
        except Exception as exc:
            log.error("Universe scan failed: %s", exc)
            return [], []

        candidates = self._rank_candidates()
        always = set(self._cfg.always_subscribed)
        held = self._held_positions

        # Active set = top-N candidates ∪ held positions ∪ always_subscribed
        top_n = candidates[: self._cfg.max_active_symbols]
        new_set = list(always | held | set(top_n))
        # Preserve ordering: always first, then held, then candidates
        ordered = []
        for sym in list(always) + list(held) + top_n:
            if sym not in ordered:
                ordered.append(sym)
        new_set = ordered

        self._active = ActiveSet(symbols=new_set, updated_at=time.time())
        new = set(new_set)

        to_sub = sorted(new - prev)
        to_unsub = sorted(prev - new)

        log.info(
            "Active set: %d symbols | +%d sub, -%d unsub | top candidates: %s",
            len(new_set),
            len(to_sub),
            len(to_unsub),
            ", ".join(top_n[:5]),
        )
        return to_sub, to_unsub

    # ── Fetch & enrich ────────────────────────────────────────────────────

    async def _fetch_universe(self) -> None:
        # metaAndAssetCtxs returns [meta, [assetCtx, ...]]
        raw = await self._rest.get_funding_rates()
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError(f"Unexpected metaAndAssetCtxs format: {type(raw)}")

        meta = raw[0]
        asset_ctxs = raw[1]
        universe_list = meta.get("universe", [])

        all_mids = await self._rest.get_all_mids()

        new_universe: Dict[str, MarketInfo] = {}
        for i, asset in enumerate(universe_list):
            coin = asset.get("name", "")
            if not coin or coin in _BLACKLIST:
                continue

            ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
            mid_str = all_mids.get(coin, "0")

            try:
                mid = float(mid_str)
                oi = float(ctx.get("openInterest", 0))
                funding = float(ctx.get("funding", 0))
                # Approximate 24h volume as OI * turnover factor
                # (HL doesn't expose true 24h volume in meta endpoint)
                volume_proxy = oi * mid * 2  # rough proxy
            except (ValueError, TypeError):
                continue

            mi = MarketInfo(
                coin=coin,
                max_leverage=int(asset.get("maxLeverage", 20)),
                sz_decimals=int(asset.get("szDecimals", 2)),
                open_interest_usd=oi * mid,
                funding_rate=funding,
                mid_price=mid,
                volume_24h_approx=volume_proxy,
            )
            new_universe[coin] = mi

        self._universe = new_universe
        log.debug("Fetched universe: %d symbols", len(new_universe))

    # ── Ranking ───────────────────────────────────────────────────────────

    def _rank_candidates(self) -> List[str]:
        """
        Score each symbol and return top-N coins sorted descending by score.
        Scoring factors:
          - Volume / OI (liquidity proxy, higher = better)
          - Funding rate magnitude (small funding = less carry risk)
          - Baseline score normalised
        Heavy meme / micro-cap filtered by min_volume gate.
        """
        scored: List[Tuple[float, str]] = []

        for coin, mi in self._universe.items():
            if coin in _BLACKLIST:
                continue
            if mi.mid_price <= 0:
                continue
            if mi.volume_24h_approx < self._cfg.min_volume_24h_usd:
                continue

            # Score components (all normalised 0-1 or raw)
            vol_score = min(mi.volume_24h_approx / 1e9, 1.0)   # cap at $1B
            funding_penalty = abs(mi.funding_rate) * 1000       # penalise extreme funding
            score = vol_score - funding_penalty

            mi.score = score
            scored.append((score, coin))

        scored.sort(reverse=True)
        return [coin for _, coin in scored]
