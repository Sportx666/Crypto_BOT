"""
Pair Quality Filter
====================
Keeps junk tokens (meme coins, new listings, non-ASCII symbols, micro-caps)
out of the scanner BEFORE any indicator is calculated.

Problems found in backtest:
  • 币安人生USDT — Chinese-character token, clearly a junk/promo token
  • GIGGLEUSDT   — meme coin with artificially spiked volume
  • ENJUSDT only passed because it briefly spiked; win rate 32%

Filters applied (all must pass):
  1. ASCII-only symbol name
  2. Minimum price  ($0.0001 avoids sub-penny tokens)
  3. Minimum 24h quote volume  (from config MIN_VOLUME)
  4. Minimum 24h trades  (ensures real liquidity, not just volume)
  5. Price change not extreme  (±20% in 24h = pump or dump, skip)
  6. Not in the known-junk blacklist
  7. Symbol not too long  (legitimate pairs are rarely > 12 chars)
"""

from __future__ import annotations
import re
import logging
from typing import Optional

log = logging.getLogger("pair_quality")

# ── Static blacklist (expand as needed) ───────────────────────────────────────
JUNK_BLACKLIST: set[str] = {
    "GIGGLEUSDT", "BABYDOGEUSDT", "SHIBUSDT",   # high ATR% meme coins
    # add any pairs you want permanently excluded
}

# ── Symbol pattern: only uppercase ASCII letters + USDT suffix ────────────────
_ASCII_USDT = re.compile(r'^[A-Z0-9]{2,10}USDT$')


def is_quality_pair(ticker: dict, config: dict) -> bool:
    """
    Return True if the ticker passes all quality gates.

    ticker : one item from client.get_ticker() — must have the keys:
             symbol, quoteVolume, lastPrice, priceChangePercent, count
    config : the bot's config dict (uses MIN_VOLUME, MAX_SPREAD_PCT)
    """
    symbol = ticker.get('symbol', '')

    # 1. ASCII-only + USDT suffix (catches 币安人生USDT, emojis, etc.)
    if not _ASCII_USDT.match(symbol):
        log.debug(f"  rejected {symbol}: non-ASCII or bad format")
        return False

    # 2. Static blacklist
    if symbol in JUNK_BLACKLIST:
        log.debug(f"  rejected {symbol}: in junk blacklist")
        return False

    # 3. Maximum symbol length (e.g. 1MBABYDOGEUSDT = 14 chars — skip)
    if len(symbol) > 14:
        log.debug(f"  rejected {symbol}: symbol too long ({len(symbol)} chars)")
        return False

    try:
        price         = float(ticker.get('lastPrice',         0))
        quote_vol     = float(ticker.get('quoteVolume',       0))
        price_chg_pct = float(ticker.get('priceChangePercent', 0))
        n_trades      = int(  ticker.get('count',             0))
        ask           = float(ticker.get('askPrice',          0))
        bid           = float(ticker.get('bidPrice',          0))
    except (ValueError, TypeError):
        return False

    # 4. Minimum price ($0.0001 keeps out micro-tokens like 0.0000001 coins)
    if price < 0.0001:
        log.debug(f"  rejected {symbol}: price too low ({price})")
        return False

    # 5. Volume gate
    min_vol = config.get("MIN_VOLUME", 20_000_000)
    if quote_vol < min_vol:
        log.debug(f"  rejected {symbol}: volume {quote_vol:.0f} < {min_vol:.0f}")
        return False

    # 6. Minimum number of trades (catches wash-trading / fake volume)
    min_trades = config.get("MIN_TRADES_24H", 50_000)
    if n_trades < min_trades:
        log.debug(f"  rejected {symbol}: only {n_trades} trades in 24h")
        return False

    # 7. Extreme price change → pump/dump in progress, skip
    max_chg = config.get("MAX_24H_CHANGE_PCT", 20.0)
    if abs(price_chg_pct) > max_chg:
        log.debug(f"  rejected {symbol}: 24h change {price_chg_pct:.1f}% exceeds ±{max_chg}%")
        return False

    # 8. Spread check
    if ask > 0 and bid > 0:
        spread_pct = (ask / bid - 1)
        max_spread = config.get("MAX_SPREAD_PCT", 0.0006)
        if spread_pct > max_spread:
            log.debug(f"  rejected {symbol}: spread {spread_pct:.4%} > {max_spread:.4%}")
            return False

    return True


def filter_quality_pairs(tickers: list, exchange_symbols: set, config: dict) -> list[str]:
    """
    Full pipeline: apply all quality gates to a list of tickers.
    Returns a sorted list of symbol strings.
    """
    from misc.config import blacklist as bot_blacklist

    passed = []
    for t in tickers:
        sym = t.get('symbol', '')
        if sym not in exchange_symbols:
            continue
        if sym in bot_blacklist:
            continue
        if is_quality_pair(t, config):
            passed.append(sym)

    log.info(f"Pair quality filter: {len(tickers)} → {len(passed)} pairs")
    return sorted(passed)
