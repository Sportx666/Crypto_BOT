"""
Hyperliquid REST + WebSocket client wrapper.

REST  : thin async wrapper over requests (blocking calls in executor)
WS    : asyncio-native, auto-reconnect, subscription registry
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp

log = logging.getLogger(__name__)

# ── Type aliases ──────────────────────────────────────────────────────────
CandleCallback = Callable[[str, str, dict], None]   # (coin, interval, bar)
MsgCallback = Callable[[dict], None]


# ══════════════════════════════════════════════════════════════════════════
#  REST client
# ══════════════════════════════════════════════════════════════════════════

class HLRestClient:
    """Async Hyperliquid REST client (no SDK dependency – raw JSON-RPC calls)."""

    def __init__(self, api_url: str) -> None:
        self._url = api_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"},
                connector=aiohttp.TCPConnector(ssl=True),
            )
        return self._session

    async def _post(self, payload: dict) -> Any:
        session = await self._ensure_session()
        async with session.post(
            f"{self._url}/info", json=payload, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Market data ───────────────────────────────────────────────────────

    async def get_meta(self) -> dict:
        """Returns universe metadata (all perp markets)."""
        return await self._post({"type": "meta"})

    async def get_all_mids(self) -> Dict[str, str]:
        """Returns {coin: mid_price_str} for all markets."""
        return await self._post({"type": "allMids"})

    async def get_candles(
        self,
        coin: str,
        interval: str,
        start_time: int,
        end_time: Optional[int] = None,
    ) -> List[dict]:
        """
        Fetch historical candles.

        Returns list of dicts with keys:
          t (open ms), T (close ms), s (coin), i (interval),
          o, h, l, c (prices as str), v (volume str), n (trades int)
        """
        payload: dict = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
            },
        }
        if end_time is not None:
            payload["req"]["endTime"] = end_time
        result = await self._post(payload)
        return result if isinstance(result, list) else []

    async def get_candles_latest(
        self, coin: str, interval: str, n_bars: int = 200
    ) -> List[dict]:
        """Fetch the last n_bars candles ending now."""
        interval_ms = _interval_to_ms(interval)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - n_bars * interval_ms
        return await self.get_candles(coin, interval, start_ms, end_ms)

    async def get_user_state(self, address: str) -> dict:
        """Returns account state: positions, margin, equity."""
        return await self._post({"type": "clearinghouseState", "user": address})

    async def get_open_orders(self, address: str) -> List[dict]:
        return await self._post({"type": "openOrders", "user": address})

    async def get_funding_rates(self) -> List[dict]:
        meta_and_asset_ctxs = await self._post(
            {"type": "metaAndAssetCtxs"}
        )
        # returns [meta_obj, [assetCtx, ...]]
        if isinstance(meta_and_asset_ctxs, list) and len(meta_and_asset_ctxs) == 2:
            return meta_and_asset_ctxs
        return meta_and_asset_ctxs

    async def get_l2_book(self, coin: str) -> dict:
        return await self._post({"type": "l2Book", "coin": coin})

    # ── Order actions (require signing) ──────────────────────────────────
    # These delegate to the Exchange class from the SDK (synchronous).
    # We run them in an executor to avoid blocking the event loop.


# ══════════════════════════════════════════════════════════════════════════
#  WebSocket manager
# ══════════════════════════════════════════════════════════════════════════

class HLWebSocket:
    """
    Async WebSocket manager for Hyperliquid.

    Maintains a single connection and multiplexes subscriptions.
    Auto-reconnects with exponential backoff.
    """

    RECONNECT_BASE = 2.0   # seconds
    RECONNECT_MAX = 60.0
    PING_INTERVAL = 30.0   # seconds between pings

    def __init__(self, ws_url: str) -> None:
        self._url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None

        # subscription_key → set of callbacks
        self._subs: Dict[str, Set[MsgCallback]] = {}
        # currently sent subscriptions (to replay on reconnect)
        self._active_subs: List[dict] = []

        self._running = False
        self._reconnect_delay = self.RECONNECT_BASE
        self._candle_callbacks: List[CandleCallback] = []
        self._raw_callbacks: List[MsgCallback] = []

        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._last_ping = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    def add_candle_callback(self, cb: CandleCallback) -> None:
        """Called whenever a candle update arrives: cb(coin, interval, bar)."""
        self._candle_callbacks.append(cb)

    def add_raw_callback(self, cb: MsgCallback) -> None:
        """Called for every raw message."""
        self._raw_callbacks.append(cb)

    async def subscribe_candles(self, coin: str, interval: str) -> None:
        sub = {"type": "candle", "coin": coin, "interval": interval}
        await self._subscribe(sub)

    async def unsubscribe_candles(self, coin: str, interval: str) -> None:
        sub = {"type": "candle", "coin": coin, "interval": interval}
        await self._unsubscribe(sub)

    async def subscribe_user_events(self, address: str) -> None:
        sub = {"type": "userEvents", "user": address}
        await self._subscribe(sub)

    async def run(self) -> None:
        """Start the WebSocket loop. Runs until stop() is called."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as exc:
                if not self._running:
                    break
                log.warning(
                    "WS disconnected (%s). Reconnecting in %.1fs…",
                    exc,
                    self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self.RECONNECT_MAX
                )
            else:
                self._reconnect_delay = self.RECONNECT_BASE

    async def stop(self) -> None:
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Internals ─────────────────────────────────────────────────────────

    async def _connect_and_run(self) -> None:
        self._session = aiohttp.ClientSession()
        log.info("WS connecting to %s", self._url)
        async with self._session.ws_connect(
            self._url,
            heartbeat=self.PING_INTERVAL,
            max_msg_size=0,
        ) as ws:
            self._ws = ws
            self._reconnect_delay = self.RECONNECT_BASE
            log.info("WS connected")

            # Replay active subscriptions
            for sub in list(self._active_subs):
                await self._send_msg({"method": "subscribe", "subscription": sub})

            # Drain the send queue (items queued before connect)
            while not self._send_queue.empty():
                msg = self._send_queue.get_nowait()
                await self._send_msg(msg)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._dispatch(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    raise ConnectionError(f"WS error: {ws.exception()}")
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED
                ):
                    break

    async def _dispatch(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        for cb in self._raw_callbacks:
            try:
                cb(data)
            except Exception:
                log.exception("raw_callback error")

        channel = data.get("channel", "")
        if channel == "candle":
            candle_data = data.get("data", {})
            coin = candle_data.get("s", "")
            interval = candle_data.get("i", "")
            for cb in self._candle_callbacks:
                try:
                    cb(coin, interval, candle_data)
                except Exception:
                    log.exception("candle_callback error")

    async def _subscribe(self, sub: dict) -> None:
        key = _sub_key(sub)
        if sub not in self._active_subs:
            self._active_subs.append(sub)
        msg = {"method": "subscribe", "subscription": sub}
        await self._enqueue(msg)
        log.debug("Subscribed: %s", key)

    async def _unsubscribe(self, sub: dict) -> None:
        if sub in self._active_subs:
            self._active_subs.remove(sub)
        msg = {"method": "unsubscribe", "subscription": sub}
        await self._enqueue(msg)
        log.debug("Unsubscribed: %s", _sub_key(sub))

    async def _enqueue(self, msg: dict) -> None:
        if self._ws and not self._ws.closed:
            await self._send_msg(msg)
        else:
            await self._send_queue.put(msg)

    async def _send_msg(self, msg: dict) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(msg))


# ══════════════════════════════════════════════════════════════════════════
#  Exchange (order placement) – wraps SDK Exchange class
# ══════════════════════════════════════════════════════════════════════════

class HLExchange:
    """
    Thin async wrapper around hyperliquid.Exchange.
    Runs blocking SDK calls in the default executor to avoid blocking asyncio.
    """

    def __init__(self, api_url: str, wallet_address: str, private_key: str) -> None:
        self._api_url = api_url
        self._wallet = wallet_address
        self._key = private_key
        self._exchange: Optional[Any] = None

    def _get_exchange(self) -> Any:
        if self._exchange is None:
            try:
                from hyperliquid.exchange import Exchange
                from hyperliquid.utils import constants
                from eth_account import Account

                acct = Account.from_key(self._key)
                url = (
                    constants.TESTNET_API_URL
                    if "testnet" in self._api_url
                    else constants.MAINNET_API_URL
                )
                self._exchange = Exchange(acct, url, account_address=self._wallet)
            except ImportError as exc:
                raise RuntimeError(
                    "hyperliquid-python-sdk not installed. "
                    "Run: pip install hyperliquid-python-sdk"
                ) from exc
        return self._exchange

    async def _run(self, fn, *args, **kwargs) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def market_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        slippage: float = 0.001,
        reduce_only: bool = False,
    ) -> dict:
        ex = self._get_exchange()
        return await self._run(
            ex.market_open, coin, is_buy, sz, None, slippage, reduce_only
        )

    async def market_close(
        self, coin: str, sz: Optional[float] = None, slippage: float = 0.001
    ) -> dict:
        ex = self._get_exchange()
        return await self._run(ex.market_close, coin, sz, None, slippage)

    async def limit_open(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        limit_px: float,
        reduce_only: bool = False,
        post_only: bool = True,
    ) -> dict:
        ex = self._get_exchange()
        order_type = {"limit": {"tif": "Alo"}} if post_only else {"limit": {"tif": "Gtc"}}
        return await self._run(
            ex.order, coin, is_buy, sz, limit_px, order_type, reduce_only
        )

    async def cancel_order(self, coin: str, oid: int) -> dict:
        ex = self._get_exchange()
        return await self._run(ex.cancel, coin, oid)

    async def set_leverage(self, coin: str, leverage: int, is_cross: bool = False) -> dict:
        ex = self._get_exchange()
        return await self._run(ex.update_leverage, leverage, coin, is_cross)

    async def update_isolated_margin(self, coin: str, is_buy: bool, ntli: float) -> dict:
        ex = self._get_exchange()
        return await self._run(ex.update_isolated_margin, is_buy, coin, ntli)


# ══════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════

_INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _interval_to_ms(interval: str) -> int:
    ms = _INTERVAL_MS.get(interval)
    if ms is None:
        raise ValueError(f"Unknown interval: {interval!r}")
    return ms


def _sub_key(sub: dict) -> str:
    return json.dumps(sub, sort_keys=True)
