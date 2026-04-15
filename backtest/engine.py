"""
Backtest Engine
===============
Walk-forward simulator that reuses the EXACT same strategy pipeline
as the live bot.  Every time you change misc/config.py and run this,
you get fresh profitability metrics against real Binance historical data.

Architecture
------------
1. fetch_history(pair, n_candles)
   Paginates the Binance API to download up to `n_candles` 1-minute bars.

2. BacktestEngine.run(pairs, n_candles, step)
   Slides a window of CANDLES_LIMIT bars over the full history.
   At each step it:
     a. Populates a local indicator_cache with the windowed data
     b. Calls DynamicTrendBreakoutStrategy.evaluate_pair()  (first screen)
     c. If score passes, calls ScalpingBreakoutStrategy._calculate_trade_suggestion()
     d. Simulates the trade on the *next* max_trade_candles bars

3. Trade simulation
   • Entry = close of signal candle
   • Win  = candle where high  >= take_profit
   • Loss = candle where low   <= stop_loss
   • Timeout = TRADE_MAX_TIME_RUNNING seconds elapse with no hit

4. Metrics
   Win rate, P&L%, profit factor, max drawdown, Sharpe ratio,
   average trade duration, per-pair breakdown.

Usage (standalone)
------------------
    python -m backtest.engine                  # default: top 20 pairs, last 3 days
    python -m backtest.engine --pairs BTCUSDT ETHUSDT --days 7 --step 3

Usage (from code)
-----------------
    from backtest.engine import BacktestEngine
    engine = BacktestEngine()
    summary = engine.run(pairs=['BTCUSDT','ETHUSDT'], n_candles=4320)
    print(summary)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── project imports ───────────────────────────────────────────────────────────
from misc.config import (
    client, config, indicator_cache_lock, logging as bot_logger
)
from core.indicator import calculate_indicators
from strategy.dynamic_trend_breakout import DynamicTrendBreakoutStrategy
from strategy.scalping_breakout import ScalpingBreakoutStrategy

# ── constants ─────────────────────────────────────────────────────────────────
CANDLES_PER_REQUEST = 500          # Binance hard limit per klines call
MAX_FETCH_ATTEMPTS  = 3            # retries per API call
STEP_DEFAULT        = 5            # bars to advance after a trade (faster scan)

log = logging.getLogger("backtest")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Data Structures
# ╚══════════════════════════════════════════════════════════════════════════════
@dataclass
class Trade:
    pair:          str
    signal_time:   datetime
    entry:         float
    stop_loss:     float
    take_profit:   float
    rr_ratio:      float
    result:        str     = "PENDING"   # WIN | LOSS | TIMEOUT
    exit_price:    float   = 0.0
    pnl_pct:       float   = 0.0         # % P&L on entry (excl. fees)
    pnl_net_pct:   float   = 0.0         # after fees
    duration_bars: int     = 0
    score:         float   = 0.0


@dataclass
class BacktestSummary:
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    timeouts:        int   = 0
    win_rate:        float = 0.0
    total_pnl_pct:   float = 0.0
    avg_pnl_pct:     float = 0.0
    profit_factor:   float = 0.0
    max_drawdown_pct:float = 0.0
    sharpe_ratio:    float = 0.0
    avg_duration:    float = 0.0        # bars
    trades:          List[Trade] = field(default_factory=list)
    per_pair:        Dict[str, dict] = field(default_factory=dict)
    equity_curve:    List[float] = field(default_factory=list)
    config_snapshot: dict = field(default_factory=dict)
    run_time:        float = 0.0        # seconds


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Historical Data Fetcher
# ╚══════════════════════════════════════════════════════════════════════════════
def fetch_history(
    pair:      str,
    n_candles: int      = 4320,      # default ~3 days of 1-minute bars
    timeframe: str      = None,
    end_time:  Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """
    Download up to `n_candles` of OHLCV data for `pair` using
    paginated Binance klines requests (max 500 per call).
    Returns a time-sorted DataFrame or None on failure.
    """
    tf = timeframe or config.get("TIMEFRAME", "1m")
    all_klines: list = []
    end_ts: Optional[int] = (
        int(end_time.timestamp() * 1000) if end_time else None
    )

    remaining = n_candles
    for _attempt in range(MAX_FETCH_ATTEMPTS):
        try:
            while remaining > 0:
                limit = min(remaining, CANDLES_PER_REQUEST)
                kwargs: dict = dict(symbol=pair, interval=tf, limit=limit)
                if end_ts:
                    kwargs["endTime"] = end_ts

                klines = client.get_klines(**kwargs)
                if not klines:
                    break

                all_klines = klines + all_klines        # prepend older bars
                end_ts     = klines[0][0] - 1           # move window back
                remaining -= len(klines)

                if len(klines) < limit:                 # no more history
                    break

            break   # success

        except Exception as exc:
            log.warning(f"fetch_history {pair}: attempt {_attempt+1} failed — {exc}")
            time.sleep(1)

    if not all_klines:
        log.error(f"fetch_history {pair}: no data returned")
        return None

    df = pd.DataFrame(all_klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_vol', 'trades', 'taker_base', 'taker_quote', 'ignore'
    ])
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df[['open', 'high', 'low', 'close', 'volume']] = \
        df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    df.sort_values('timestamp', inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Keep only the last n_candles to stay within bounds
    return df.tail(n_candles).reset_index(drop=True)


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Trade Simulator
# ╚══════════════════════════════════════════════════════════════════════════════
def simulate_trade(
    df_future:    pd.DataFrame,
    entry:        float,
    stop_loss:    float,
    take_profit:  float,
    max_bars:     int = 600,
) -> Tuple[str, float, int]:
    """
    Replay future candles and determine trade outcome.

    Returns
    -------
    result  : "WIN" | "LOSS" | "TIMEOUT"
    exit_px : price at exit
    n_bars  : number of bars until exit
    """
    fee_rate = config.get("EXCHANGE_FEES", 0.001)
    subset   = df_future.head(max_bars)

    for i, row in subset.iterrows():
        # Check stop-loss first (pessimistic: assume SL hit before TP in same bar)
        if row['low'] <= stop_loss:
            return "LOSS", stop_loss, int(i) + 1

        if row['high'] >= take_profit:
            return "WIN", take_profit, int(i) + 1

    # Neither hit in time
    exit_px = float(df_future.iloc[min(max_bars, len(df_future)) - 1]['close'])
    return "TIMEOUT", exit_px, min(max_bars, len(df_future))


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Mock skip_trade_logger (not needed for backtesting)
# ╚══════════════════════════════════════════════════════════════════════════════
class _SilentLogger:
    def info(self, *a, **k):  pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Backtest Engine
# ╚══════════════════════════════════════════════════════════════════════════════
class BacktestEngine:
    """
    Walk-forward backtester.

    Parameters
    ----------
    cfg         : config dict (defaults to the live bot's misc/config.py config)
    progress_cb : optional callable(pair, pct_done) for UI progress updates
    """

    def __init__(self, cfg: dict = None, progress_cb=None):
        self.config      = cfg or config
        self.progress_cb = progress_cb

        # Reuse live strategy objects with our local cache
        self._local_cache: dict = {}
        self._strategy1 = DynamicTrendBreakoutStrategy(
            self.config, self._local_cache
        )
        self._strategy2 = ScalpingBreakoutStrategy(
            self.config, _SilentLogger()
        )

    # ── public ────────────────────────────────────────────────────────────────
    def run(
        self,
        pairs:     List[str],
        n_candles: int  = 4320,   # ~3 days of 1m data
        step:      int  = STEP_DEFAULT,
        workers:   int  = 4,
    ) -> BacktestSummary:
        """
        Run the full backtest across all pairs in parallel.

        Parameters
        ----------
        pairs     : list of Binance symbols (e.g. ['BTCUSDT', 'ETHUSDT'])
        n_candles : total historical bars to download per pair
        step      : bars to advance when NO signal is found (1 = exhaustive,
                    5 = 5× faster, slight miss rate)
        workers   : parallel fetch threads
        """
        t0     = time.time()
        trades: List[Trade] = []

        log.info(f"Backtest starting — {len(pairs)} pairs, {n_candles} candles each")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._backtest_pair, pair, n_candles, step): pair
                for pair in pairs
            }
            done = 0
            for fut in as_completed(futures):
                pair = futures[fut]
                try:
                    pair_trades = fut.result()
                    trades.extend(pair_trades)
                except Exception as exc:
                    log.error(f"Pair {pair} failed: {exc}")
                done += 1
                if self.progress_cb:
                    self.progress_cb(pair, done / len(pairs))

        summary = self._build_summary(trades, time.time() - t0)
        log.info(
            f"Backtest done in {summary.run_time:.1f}s — "
            f"{summary.total_trades} trades, win rate {summary.win_rate:.1f}%"
        )
        return summary

    # ── per-pair walk-forward ─────────────────────────────────────────────────
    def _backtest_pair(
        self,
        pair:      str,
        n_candles: int,
        step:      int,
    ) -> List[Trade]:
        """Walk-forward simulation for a single pair."""
        df_full = fetch_history(pair, n_candles=n_candles)
        if df_full is None or len(df_full) < self.config["CANDLES_LIMIT"] + 50:
            log.warning(f"{pair}: insufficient history ({len(df_full) if df_full is not None else 0} bars)")
            return []

        window         = self.config["CANDLES_LIMIT"]
        max_bars       = int(self.config.get("TRADE_MAX_TIME_RUNNING", 600))
        fee_rate       = self.config.get("EXCHANGE_FEES", 0.001)
        # 30-min cooldown expressed in bars (1 bar = 1 minute for default TF)
        cooldown_bars  = int(self.config.get("PAIR_COOLDOWN_MINUTES", 30))
        trades:        List[Trade] = []
        i = window
        n = len(df_full)

        while i < n - max_bars:
            # ── 1. Build indicator window ──────────────────────────────────
            df_win = df_full.iloc[i - window: i].copy().reset_index(drop=True)

            try:
                df_win = calculate_indicators(df_win)
            except Exception as exc:
                log.debug(f"{pair}@{i}: indicator error — {exc}")
                i += step
                continue

            # ── 2. First screen (DynamicTrendBreakout) ─────────────────────
            # Inject windowed data into local cache (thread-per-pair → no lock)
            self._local_cache[pair] = df_win

            result1 = self._strategy1.evaluate_pair(pair)
            if result1 is None:
                i += step
                continue

            _, score, _, analysis = result1

            # ── 3. Second screen: trade suggestion only (skip live MTF) ────
            suggestion = self._strategy2._calculate_trade_suggestion(
                pair, df_win, analysis
            )
            if suggestion is None:
                i += step
                continue

            # ── 4. Simulate trade on future candles ────────────────────────
            df_future = df_full.iloc[i:].reset_index(drop=True)
            entry     = suggestion['entry']
            sl        = suggestion['stop_loss']
            tp        = suggestion['take_profit']
            rr        = suggestion['rr_ratio']

            outcome, exit_px, n_bars = simulate_trade(
                df_future, entry, sl, tp, max_bars=max_bars
            )

            pnl_pct     = (exit_px - entry) / entry * 100
            pnl_net_pct = pnl_pct - fee_rate * 2 * 100   # round-trip fees

            trade = Trade(
                pair          = pair,
                signal_time   = df_full.iloc[i]['timestamp'].to_pydatetime(),
                entry         = entry,
                stop_loss     = sl,
                take_profit   = tp,
                rr_ratio      = rr,
                result        = outcome,
                exit_price    = exit_px,
                pnl_pct       = round(pnl_pct, 4),
                pnl_net_pct   = round(pnl_net_pct, 4),
                duration_bars = n_bars,
                score         = score,
            )
            trades.append(trade)

            # Advance past trade + full cooldown window (mirrors live bot behaviour).
            # This prevents same-pair re-entry within PAIR_COOLDOWN_MINUTES bars —
            # the main source of loss clustering seen in the backtest results.
            i += max(n_bars, cooldown_bars)

        return trades

    # ── metrics ───────────────────────────────────────────────────────────────
    def _build_summary(
        self,
        trades: List[Trade],
        elapsed: float,
    ) -> BacktestSummary:

        s = BacktestSummary(
            trades          = trades,
            run_time        = round(elapsed, 1),
            config_snapshot = {
                k: self.config[k] for k in [
                    "SCORE_THRESHOLD", "REFINED_SCORE_THRESHOLD",
                    "RR_THRESHOLD", "ATR_BOUNDS", "ADX_BOUNDS",
                    "RSI_BOUNDS", "VOLUME_SPIKE_THRESHOLD",
                    "TIMEFRAME", "EMA_SPANS", "MIN_VOLUME",
                ] if k in self.config
            },
        )

        if not trades:
            return s

        wins     = [t for t in trades if t.result == "WIN"]
        losses   = [t for t in trades if t.result == "LOSS"]
        timeouts = [t for t in trades if t.result == "TIMEOUT"]

        s.total_trades  = len(trades)
        s.wins          = len(wins)
        s.losses        = len(losses)
        s.timeouts      = len(timeouts)
        s.win_rate      = round(len(wins) / len(trades) * 100, 1)
        s.avg_duration  = round(np.mean([t.duration_bars for t in trades]), 1)

        pnls = np.array([t.pnl_net_pct for t in trades])
        s.total_pnl_pct = round(float(pnls.sum()), 2)
        s.avg_pnl_pct   = round(float(pnls.mean()), 3)

        gross_profit = sum(t.pnl_net_pct for t in trades if t.pnl_net_pct > 0)
        gross_loss   = abs(sum(t.pnl_net_pct for t in trades if t.pnl_net_pct < 0))
        s.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else float('inf')

        # equity curve (cumulative P&L assuming equal sizing each trade)
        equity = np.cumsum(pnls)
        s.equity_curve = equity.tolist()

        # max drawdown
        peak = np.maximum.accumulate(equity)
        dd   = peak - equity
        s.max_drawdown_pct = round(float(dd.max()), 2) if len(dd) else 0.0

        # Sharpe (daily, annualised to 525,600 1m bars / 1440 per day)
        if pnls.std() > 0:
            s.sharpe_ratio = round(
                float(pnls.mean() / pnls.std() * np.sqrt(len(pnls))), 2
            )

        # per-pair breakdown
        for pair in {t.pair for t in trades}:
            pt = [t for t in trades if t.pair == pair]
            pw = [t for t in pt if t.result == "WIN"]
            s.per_pair[pair] = {
                "trades":      len(pt),
                "wins":        len(pw),
                "win_rate":    round(len(pw) / len(pt) * 100, 1),
                "total_pnl":   round(sum(t.pnl_net_pct for t in pt), 2),
                "avg_rr":      round(np.mean([t.rr_ratio for t in pt]), 2),
            }

        return s

    # ── convenience: top pairs from Binance (with quality filter) ────────────
    @staticmethod
    def get_top_pairs(n: int = 20) -> List[str]:
        """
        Return the top-n USDT pairs by 24h quote volume,
        filtered through the same quality gates as the live bot
        (blocks meme coins, non-ASCII symbols, pump/dumps, thin liquidity).
        """
        try:
            from utilities.pair_quality import filter_quality_pairs
            tickers       = client.get_ticker()
            exchange_info = client.get_exchange_info()
            trading_syms  = {
                s['symbol']
                for s in exchange_info['symbols']
                if s['status'] == 'TRADING' and s.get('isSpotTradingAllowed', False)
            }
            quality_pairs = filter_quality_pairs(tickers, trading_syms, config)
            # Sort by volume descending and return top n
            vol_map = {t['symbol']: float(t['quoteVolume']) for t in tickers}
            quality_pairs.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
            return quality_pairs[:n]
        except Exception as exc:
            log.error(f"get_top_pairs failed: {exc}")
            return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Pretty-print summary
# ╚══════════════════════════════════════════════════════════════════════════════
def print_summary(s: BacktestSummary):
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  BACKTEST RESULTS")
    print(sep)
    print(f"  Trades        : {s.total_trades}")
    print(f"  Win / Loss    : {s.wins} / {s.losses}  (timeout: {s.timeouts})")
    print(f"  Win Rate      : {s.win_rate}%")
    print(f"  Total P&L     : {s.total_pnl_pct:+.2f}%")
    print(f"  Avg P&L/trade : {s.avg_pnl_pct:+.3f}%")
    print(f"  Profit Factor : {s.profit_factor}")
    print(f"  Max Drawdown  : {s.max_drawdown_pct:.2f}%")
    print(f"  Sharpe Ratio  : {s.sharpe_ratio}")
    print(f"  Avg Duration  : {s.avg_duration:.0f} bars ({s.avg_duration:.0f} min)")
    print(f"  Run time      : {s.run_time}s")
    print(sep)

    if s.per_pair:
        print(f"\n  Per-Pair Breakdown")
        print(f"  {'Pair':<16} {'Trades':>6} {'Win%':>6} {'P&L%':>8} {'AvgRR':>6}")
        print(f"  {'─'*16} {'─'*6} {'─'*6} {'─'*8} {'─'*6}")
        for pair, d in sorted(s.per_pair.items(),
                               key=lambda x: x[1]['total_pnl'], reverse=True):
            print(
                f"  {pair:<16} {d['trades']:>6} {d['win_rate']:>5.1f}%"
                f" {d['total_pnl']:>+7.2f}% {d['avg_rr']:>6.2f}"
            )

    print(f"\n  Config used:")
    for k, v in s.config_snapshot.items():
        print(f"    {k:<32} {v}")
    print(sep + "\n")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CLI entry point
# ╚══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse, csv, pathlib

    parser = argparse.ArgumentParser(description="SPORTYX Crypto Bot Backtester")
    parser.add_argument("--pairs",  nargs="*",  default=None,
                        help="Pairs to test (default: top 20 by volume)")
    parser.add_argument("--days",   type=float, default=3.0,
                        help="Days of history to fetch (default 3)")
    parser.add_argument("--step",   type=int,   default=5,
                        help="Bar step when no signal (default 5)")
    parser.add_argument("--out",    type=str,   default="backtest_results.csv",
                        help="CSV output path")
    args = parser.parse_args()

    n_candles = int(args.days * 24 * 60)

    engine = BacktestEngine()
    pairs  = args.pairs or BacktestEngine.get_top_pairs(20)

    print(f"Running backtest on {len(pairs)} pairs × {n_candles} candles …")
    summary = engine.run(pairs=pairs, n_candles=n_candles, step=args.step)
    print_summary(summary)

    # Save to CSV
    if summary.trades:
        out_path = pathlib.Path(args.out)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "pair", "signal_time", "entry", "stop_loss", "take_profit",
                "rr_ratio", "result", "exit_price",
                "pnl_pct", "pnl_net_pct", "duration_bars", "score"
            ])
            writer.writeheader()
            for t in summary.trades:
                writer.writerow({
                    "pair":          t.pair,
                    "signal_time":   t.signal_time,
                    "entry":         t.entry,
                    "stop_loss":     t.stop_loss,
                    "take_profit":   t.take_profit,
                    "rr_ratio":      t.rr_ratio,
                    "result":        t.result,
                    "exit_price":    t.exit_price,
                    "pnl_pct":       t.pnl_pct,
                    "pnl_net_pct":   t.pnl_net_pct,
                    "duration_bars": t.duration_bars,
                    "score":         t.score,
                })
        print(f"Results saved → {out_path.resolve()}")
