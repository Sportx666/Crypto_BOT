"""
Simple candle-replay backtester.

Replays historical 5m/15m/1h bars bar-by-bar through the full pipeline:
  regime → strategy → risk → simulated execution

Usage (CLI):
    python -m bot.backtester --coins ETH SOL --days 30 --equity 10000

Usage (code):
    from bot.backtester import Backtester, BacktestConfig
    bt = Backtester(BacktestConfig(coins=["ETH","SOL"], days=30, equity=10000))
    result = asyncio.run(bt.run())
    print(result.summary())
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from .config import Config
from .candles import CandleCache, raw_to_row, COLS
from .hl_client import HLRestClient
from .regime import RegimeEngine, MarketRegime
from .strategies.trend import TrendStrategy, TrendSignal
from .strategies.range_mean import RangeStrategy
from .risk import RiskManager, Position
from .journal import Journal

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestConfig:
    coins: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    days: int = 30
    equity: float = 10_000.0
    # Override any bot Config fields via this dict
    config_overrides: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
#  Result
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    coin: str
    direction: int
    strategy: str
    pattern: str
    entry: float
    exit: float
    size: float
    pnl_usd: float
    pnl_pct: float   # % of equity at time of entry
    duration_bars: int
    reason: str
    opened_at: str
    closed_at: str


@dataclass
class BacktestResult:
    trades: List[TradeRecord]
    starting_equity: float
    ending_equity: float
    equity_curve: List[float]

    def summary(self) -> str:
        if not self.trades:
            return "No trades executed."
        n = len(self.trades)
        wins = [t for t in self.trades if t.pnl_usd > 0]
        losses = [t for t in self.trades if t.pnl_usd < 0]
        total_pnl = sum(t.pnl_usd for t in self.trades)
        win_rate = len(wins) / n * 100
        avg_win = sum(t.pnl_usd for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0
        profit_factor = (
            abs(sum(t.pnl_usd for t in wins)) / abs(sum(t.pnl_usd for t in losses))
            if losses else float("inf")
        )
        max_dd = _max_drawdown(self.equity_curve)
        reasons = {}
        for t in self.trades:
            reasons[t.reason] = reasons.get(t.reason, 0) + 1
        by_reason = " | ".join(f"{r}:{c}" for r, c in sorted(reasons.items()))

        patterns = {}
        for t in self.trades:
            patterns[t.pattern] = patterns.get(t.pattern, 0) + 1
        by_pattern = " | ".join(f"{p}:{c}" for p, c in sorted(patterns.items()))

        return (
            f"\n{'='*60}\n"
            f"BACKTEST RESULT\n"
            f"{'='*60}\n"
            f"Trades          : {n}  (wins={len(wins)} losses={len(losses)})\n"
            f"Win rate        : {win_rate:.1f}%\n"
            f"Total P&L       : {total_pnl:+.2f} USD  "
            f"({(self.ending_equity/self.starting_equity-1)*100:+.2f}%)\n"
            f"Avg win         : {avg_win:+.2f}  |  Avg loss: {avg_loss:+.2f}\n"
            f"Profit factor   : {profit_factor:.2f}\n"
            f"Max drawdown    : {max_dd:.2f}%\n"
            f"Equity          : {self.starting_equity:.0f} → {self.ending_equity:.0f}\n"
            f"Exit reasons    : {by_reason}\n"
            f"Patterns        : {by_pattern}\n"
            f"{'='*60}\n"
        )


# ══════════════════════════════════════════════════════════════════════════
#  Backtester
# ══════════════════════════════════════════════════════════════════════════

class Backtester:
    """
    Event-driven candle replay backtester.
    Fetches real historical data from HL REST API.
    """

    EXEC_TF = "5m"
    REGIME_TF = "15m"
    GLOBAL_TF = "1h"
    BAR_SECONDS = 300   # 5m = 300s

    def __init__(self, bt_cfg: BacktestConfig) -> None:
        self._bt = bt_cfg

        # Build bot config with optional overrides
        cfg = Config()
        cfg.dry_run = True
        cfg.testnet = False   # use mainnet data for backtesting
        for k, v in bt_cfg.config_overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        self._cfg = cfg

        self._rest = HLRestClient(cfg.hl_api_url)
        self._cache = CandleCache(max_bars=1000)
        self._regime = RegimeEngine(cfg, self._cache)
        self._trend_strat = TrendStrategy(cfg)
        self._range_strat = RangeStrategy(cfg)

        # Simulated risk (no real sizing, just tracking)
        self._risk = RiskManager(cfg)
        self._risk.update_equity(bt_cfg.equity)
        self._journal = Journal("data/bt_journal.jsonl")

        # State
        self._equity = bt_cfg.equity
        self._equity_curve: List[float] = [bt_cfg.equity]
        self._trades: List[TradeRecord] = []
        self._open_sims: Dict[str, _SimPos] = {}   # coin → sim position

    async def run(self) -> BacktestResult:
        log.info("Backtest: coins=%s days=%d equity=%.0f",
                 self._bt.coins, self._bt.days, self._bt.equity)

        # Fetch history
        n_5m_bars = self._bt.days * 24 * 12
        n_15m_bars = self._bt.days * 24 * 4
        n_1h_bars = self._bt.days * 24

        all_coins = self._bt.coins + [self._cfg.global_symbol]
        for coin in all_coins:
            for tf, n in [(self.EXEC_TF, n_5m_bars), (self.REGIME_TF, n_15m_bars),
                          (self.GLOBAL_TF, n_1h_bars)]:
                try:
                    bars = await self._rest.get_candles_latest(coin, tf, n)
                    self._cache.seed(coin, tf, bars)
                    log.info("  %s %s: %d bars", coin, tf, len(bars))
                except Exception as exc:
                    log.error("  %s %s fetch failed: %s", coin, tf, exc)

        # Find common 5m timestamps across all non-BTC coins
        ref_coin = self._bt.coins[0] if self._bt.coins else None
        if ref_coin is None:
            return BacktestResult([], self._equity, self._equity, self._equity_curve)

        df_ref = self._cache.get(ref_coin, self.EXEC_TF)
        if df_ref is None or df_ref.empty:
            log.error("No 5m data for %s", ref_coin)
            return BacktestResult([], self._equity, self._equity, self._equity_curve)

        timestamps = df_ref["ts"].tolist()
        warmup = 60   # skip first 60 bars while indicators warm up

        log.info("Replaying %d bars (skip first %d warmup)…", len(timestamps), warmup)

        for i, ts in enumerate(timestamps):
            if i < warmup:
                continue

            for coin in self._bt.coins:
                await self._process_bar(coin, ts, i)

            self._equity_curve.append(self._equity)

        # Close any remaining open positions at last price
        for coin in list(self._open_sims.keys()):
            df = self._cache.get(coin, self.EXEC_TF)
            if df is not None and not df.empty:
                last_price = float(df["close"].iloc[-1])
                self._close_sim(coin, last_price, "end_of_test")

        await self._rest.close()

        return BacktestResult(
            trades=self._trades,
            starting_equity=self._bt.equity,
            ending_equity=self._equity,
            equity_curve=self._equity_curve,
        )

    async def _process_bar(self, coin: str, ts: int, bar_idx: int) -> None:
        """Simulate signal evaluation and position management at bar `ts`."""
        # Build sub-DataFrames up to this timestamp
        df_5m = _slice_at(self._cache.get(coin, self.EXEC_TF), ts, 100)
        df_15m = _slice_at(self._cache.get(coin, self.REGIME_TF), ts, 100)
        df_1h_btc = _slice_at(
            self._cache.get(self._cfg.global_symbol, self.GLOBAL_TF), ts, 100
        )

        if df_5m is None or len(df_5m) < 55:
            return

        current_price = float(df_5m["close"].iloc[-1])

        # ── Monitor open position ──────────────────────────────────────────
        if coin in self._open_sims:
            self._monitor_sim(coin, current_price, bar_idx)
            return  # don't look for new entry while in position

        # ── Regime ────────────────────────────────────────────────────────
        # Temporarily seed the sliced data for regime/strategy computation
        if df_15m is not None:
            self._cache.seed(coin, self.REGIME_TF, _df_to_bars(df_15m))
        if df_1h_btc is not None:
            self._cache.seed(self._cfg.global_symbol, self.GLOBAL_TF, _df_to_bars(df_1h_btc))
        # Seed 5m slice
        self._cache.seed(coin, self.EXEC_TF, _df_to_bars(df_5m))

        global_ctx = self._regime.global_bias()
        regime = self._regime.classify(coin)

        if not RegimeEngine.is_tradeable(regime):
            return

        # ── Strategy ──────────────────────────────────────────────────────
        signal = None
        if regime.regime == MarketRegime.TREND:
            signal = self._trend_strat.evaluate(coin, df_5m, regime, global_ctx)
        elif regime.regime == MarketRegime.RANGE:
            signal = self._range_strat.evaluate(coin, df_5m, regime, global_ctx)

        if signal is None:
            return

        # ── Risk ──────────────────────────────────────────────────────────
        verdict = self._risk.check_entry(coin, signal.direction, signal.r_distance)
        if not verdict.allowed:
            return

        # ── Open simulated position ────────────────────────────────────────
        pattern = getattr(signal, "pattern", "SIGNAL")
        strategy = "TREND" if isinstance(signal, TrendSignal) else "RANGE"
        self._open_sims[coin] = _SimPos(
            coin=coin,
            direction=signal.direction,
            entry=signal.entry,
            sl=signal.sl,
            tp=signal.tp_partial,
            trail_dist=signal.trail_distance,
            atr=signal.atr,
            r_distance=signal.r_distance,
            size=verdict.size,
            risk_usd=verdict.risk_usd,
            strategy=strategy,
            pattern=pattern,
            opened_bar=bar_idx,
            opened_ts=_ts_to_iso(ts),
            highest=signal.entry,
            lowest=signal.entry,
            partial_taken=False,
        )
        self._risk.open_position(Position(
            coin=coin, direction=signal.direction, entry_price=signal.entry,
            size=verdict.size, sl=signal.sl, tp_partial=signal.tp_partial,
            trail_distance=signal.trail_distance, atr=signal.atr,
            strategy=strategy,
        ))
        log.debug("BT OPEN %s %s entry=%.4f sl=%.4f rr=%.2f",
                  coin, "L" if signal.direction==1 else "S",
                  signal.entry, signal.sl,
                  abs(signal.tp_partial - signal.entry) / signal.r_distance)

    def _monitor_sim(self, coin: str, price: float, bar_idx: int) -> None:
        sim = self._open_sims[coin]

        # Update trail
        if sim.direction == 1:
            sim.highest = max(sim.highest, price)
        else:
            sim.lowest = min(sim.lowest, price)

        # FIX #12: stale timeout
        age_bars = bar_idx - sim.opened_bar
        if age_bars >= self._cfg.trade_max_duration_bars:
            upnl = sim.direction * (price - sim.entry) * sim.size
            if upnl < sim.risk_usd * self._cfg.trade_exit_fraction_of_risk:
                self._close_sim(coin, price, "timeout_exit")
                return

        # Partial TP
        if not sim.partial_taken:
            tp_hit = (sim.direction == 1 and price >= sim.tp) or \
                     (sim.direction == -1 and price <= sim.tp)
            if tp_hit:
                # close half
                close_sz = sim.size * self._cfg.trend_partial_close_pct
                pnl_partial = sim.direction * (price - sim.entry) * close_sz
                self._equity += pnl_partial
                sim.size -= close_sz
                sim.partial_taken = True
                # FIX #7: move SL to B/E
                if self._cfg.move_sl_to_be_after_partial:
                    be_sl = sim.entry + sim.direction * sim.atr * self._cfg.be_buffer_atr_mult
                    if sim.direction == 1 and be_sl > sim.sl:
                        sim.sl = be_sl
                    elif sim.direction == -1 and be_sl < sim.sl:
                        sim.sl = be_sl

        # Trailing stop
        if sim.partial_taken:
            if sim.direction == 1:
                trail_sl = sim.highest - sim.trail_dist
            else:
                trail_sl = sim.lowest + sim.trail_dist
            trail_hit = (sim.direction == 1 and price <= trail_sl) or \
                        (sim.direction == -1 and price >= trail_sl)
            if trail_hit:
                self._close_sim(coin, price, "trail_stop")
                return

        # Hard SL
        sl_hit = (sim.direction == 1 and price <= sim.sl) or \
                 (sim.direction == -1 and price >= sim.sl)
        if sl_hit:
            self._close_sim(coin, price, "stop_loss")
            return

        # Range TP
        if sim.strategy == "RANGE":
            tp_hit = (sim.direction == 1 and price >= sim.tp) or \
                     (sim.direction == -1 and price <= sim.tp)
            if tp_hit:
                self._close_sim(coin, price, "tp")

    def _close_sim(self, coin: str, price: float, reason: str) -> None:
        sim = self._open_sims.pop(coin, None)
        if sim is None:
            return
        pnl = sim.direction * (price - sim.entry) * sim.size
        self._equity = max(self._equity + pnl, 0)
        pnl_pct = pnl / self._bt.equity * 100

        self._trades.append(TradeRecord(
            coin=coin,
            direction=sim.direction,
            strategy=sim.strategy,
            pattern=sim.pattern,
            entry=sim.entry,
            exit=price,
            size=sim.size,
            pnl_usd=pnl,
            pnl_pct=pnl_pct,
            duration_bars=0,
            reason=reason,
            opened_at=sim.opened_ts,
            closed_at=_now_iso(),
        ))
        self._risk.close_position(coin, price, reason=reason)
        self._risk.update_equity(self._equity)
        log.debug("BT CLOSE %s  pnl=%+.2f  reason=%s  equity=%.0f",
                  coin, pnl, reason, self._equity)


# ══════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class _SimPos:
    coin: str
    direction: int
    entry: float
    sl: float
    tp: float
    trail_dist: float
    atr: float
    r_distance: float
    size: float
    risk_usd: float
    strategy: str
    pattern: str
    opened_bar: int
    opened_ts: str
    highest: float
    lowest: float
    partial_taken: bool


def _slice_at(df: Optional[pd.DataFrame], ts: int, n: int) -> Optional[pd.DataFrame]:
    """Return the last `n` bars up to and including timestamp `ts`."""
    if df is None or df.empty:
        return None
    sub = df[df["ts"] <= ts].tail(n).reset_index(drop=True)
    return sub if not sub.empty else None


def _df_to_bars(df: pd.DataFrame) -> list:
    """Convert DataFrame rows back to raw bar dicts for re-seeding."""
    bars = []
    for _, row in df.iterrows():
        bars.append({
            "t": int(row["ts"]),
            "T": int(row["ts"]) + 299999,
            "o": str(row["open"]),
            "h": str(row["high"]),
            "l": str(row["low"]),
            "c": str(row["close"]),
            "v": str(row["volume"]),
            "n": int(row.get("n_trades", 0)),
        })
    return bars


def _max_drawdown(curve: List[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


def _ts_to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    parser = argparse.ArgumentParser(description="CryptoBOT backtester")
    parser.add_argument("--coins", nargs="+", default=["ETH", "SOL", "AVAX", "LINK"],
                        help="Coins to backtest (HL names, no -USDC suffix)")
    parser.add_argument("--days", type=int, default=30, help="History days")
    parser.add_argument("--equity", type=float, default=10_000, help="Starting equity USD")
    parser.add_argument("--min-rr", type=float, default=None,
                        help="Override min_rr_trend (e.g. 2.0)")
    parser.add_argument("--sl-mult", type=float, default=None,
                        help="Override trend_atr_sl_mult")
    parser.add_argument("--adx-min", type=float, default=None,
                        help="Override trend_min_adx")
    args = parser.parse_args()

    overrides: dict = {}
    if args.min_rr is not None:
        overrides["min_rr_trend"] = args.min_rr
    if args.sl_mult is not None:
        overrides["trend_atr_sl_mult"] = args.sl_mult
    if args.adx_min is not None:
        overrides["trend_min_adx"] = args.adx_min

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("bot.strategies").setLevel(logging.WARNING)
    logging.getLogger("bot.regime").setLevel(logging.WARNING)
    logging.getLogger("bot.risk").setLevel(logging.WARNING)

    bt = Backtester(BacktestConfig(
        coins=args.coins,
        days=args.days,
        equity=args.equity,
        config_overrides=overrides,
    ))
    result = await bt.run()
    print(result.summary())

    if result.trades:
        print("\nTrade breakdown by coin:")
        by_coin: dict = {}
        for t in result.trades:
            if t.coin not in by_coin:
                by_coin[t.coin] = []
            by_coin[t.coin].append(t)
        for coin, ts in sorted(by_coin.items()):
            wins = sum(1 for t in ts if t.pnl_usd > 0)
            total = sum(t.pnl_usd for t in ts)
            print(f"  {coin:8s}  trades={len(ts):3d}  wr={wins/len(ts)*100:.0f}%  pnl={total:+.2f}")


if __name__ == "__main__":
    asyncio.run(_main())
