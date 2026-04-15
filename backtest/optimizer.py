"""
Config Parameter Optimizer
===========================
Runs a grid search over key config parameters using the walk-forward
backtest engine and reports the best combinations.

Usage
-----
    python -m backtest.optimizer                    # quick: 2 days, top 10 pairs
    python -m backtest.optimizer --days 5 --top 20  # fuller test

The optimizer tests every combination of the parameter grid defined
in SEARCH_SPACE and prints a ranked table of results.

Based on the current backtest result analysis:
  - Win rate 36.4% (needs ~40%+)
  - Only 4 low-quality pairs traded (now fixed by quality filter)
  - 46/76 same-pair re-entries (now fixed by cooldown)
  - No market regime awareness (now fixed by market_regime.py)

Parameters being optimised here:
  SCORE_THRESHOLD, REFINED_SCORE_THRESHOLD, ADX_BOUNDS (min),
  RSI_BOUNDS, VOLUME_SPIKE_THRESHOLD, RR_THRESHOLD (min)
"""

from __future__ import annotations

import argparse
import copy
import itertools
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from misc.config import config as BASE_CONFIG
from backtest.engine import BacktestEngine, BacktestSummary


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Parameter search space
# ╚══════════════════════════════════════════════════════════════════════════════
SEARCH_SPACE: dict[str, list[Any]] = {
    "SCORE_THRESHOLD":         [4.0, 5.0, 6.0],
    "REFINED_SCORE_THRESHOLD": [5.0, 6.0, 7.0],
    "ADX_MIN":                 [20,  25,  30 ],   # mapped to ADX_BOUNDS[0]
    "RSI_LOWER":               [50,  52,  55 ],   # mapped to RSI_BOUNDS[0]
    "VOLUME_SPIKE_THRESHOLD":  [1.8, 2.0, 2.5],
    "RR_MIN":                  [1.0, 1.2, 1.4],   # mapped to RR_THRESHOLD[0]
}

# Fitness function weights (tune to your preference)
W_WIN_RATE     = 0.30
W_PROFIT_FACTOR= 0.35
W_SHARPE       = 0.20
W_DRAWDOWN     = 0.15   # lower DD = better (inverted)


def fitness(s: BacktestSummary) -> float:
    """Higher = better. Returns 0 if no trades."""
    if s.total_trades < 5:
        return -999.0   # not enough data

    # Normalise each metric to 0–1 range (approximate)
    wr_score  = min(s.win_rate / 60.0,           1.0)   # 60% = perfect
    pf_score  = min(s.profit_factor / 2.0,       1.0)   # 2.0 = perfect
    sr_score  = min(max(s.sharpe_ratio / 2.0, 0),1.0)   # 2.0 = perfect
    dd_score  = max(1.0 - s.max_drawdown_pct / 20.0, 0) # 0% DD = perfect

    return (
        W_WIN_RATE      * wr_score
      + W_PROFIT_FACTOR * pf_score
      + W_SHARPE        * sr_score
      + W_DRAWDOWN      * dd_score
    )


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Config builder
# ╚══════════════════════════════════════════════════════════════════════════════
def build_config(params: dict) -> dict:
    """Apply a parameter combination to a copy of the base config."""
    cfg = copy.deepcopy(BASE_CONFIG)

    cfg["SCORE_THRESHOLD"]          = params["SCORE_THRESHOLD"]
    cfg["REFINED_SCORE_THRESHOLD"]  = params["REFINED_SCORE_THRESHOLD"]
    cfg["VOLUME_SPIKE_THRESHOLD"]   = params["VOLUME_SPIKE_THRESHOLD"]

    adx_max = cfg.get("ADX_BOUNDS", [20, 65])[1]
    cfg["ADX_BOUNDS"] = [params["ADX_MIN"], adx_max]

    rsi_max = cfg.get("RSI_BOUNDS", [50, 70])[1]
    cfg["RSI_BOUNDS"] = [params["RSI_LOWER"], rsi_max]

    rr_max = cfg.get("RR_THRESHOLD", [1.0, 2.5])[1]
    cfg["RR_THRESHOLD"] = [params["RR_MIN"], rr_max]

    return cfg


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Single run (called in subprocess)
# ╚══════════════════════════════════════════════════════════════════════════════
def _run_one(args):
    params, pairs, n_candles = args
    cfg     = build_config(params)
    engine  = BacktestEngine(cfg=cfg)
    summary = engine.run(pairs=pairs, n_candles=n_candles, step=5, workers=2)
    score   = fitness(summary)
    return params, summary, score


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  Optimizer
# ╚══════════════════════════════════════════════════════════════════════════════
class ConfigOptimizer:

    def __init__(self, search_space: dict = None):
        self.space   = search_space or SEARCH_SPACE
        self.results: list[tuple[dict, BacktestSummary, float]] = []

    def run(
        self,
        pairs:     list[str],
        n_candles: int = 2880,   # 2 days default
        max_workers: int = 2,
    ) -> list[tuple[dict, BacktestSummary, float]]:
        """
        Run all parameter combinations.
        Returns list of (params, summary, score) sorted best-first.
        """
        keys   = list(self.space.keys())
        combos = [dict(zip(keys, vals)) for vals in itertools.product(*self.space.values())]
        total  = len(combos)
        print(f"\nOptimizer: {total} combinations × {len(pairs)} pairs × {n_candles} candles\n")

        t0   = time.time()
        done = 0

        # Run in parallel processes
        args_list = [(p, pairs, n_candles) for p in combos]
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, a): a[0] for a in args_list}
            for fut in as_completed(futures):
                try:
                    params, summary, score = fut.result()
                    self.results.append((params, summary, score))
                except Exception as exc:
                    print(f"  ✗ combo failed: {exc}")
                done += 1
                if done % 10 == 0 or done == total:
                    elapsed = time.time() - t0
                    eta     = elapsed / done * (total - done)
                    print(f"  {done}/{total}  elapsed={elapsed:.0f}s  ETA={eta:.0f}s")

        self.results.sort(key=lambda x: x[2], reverse=True)
        return self.results

    def print_report(self, top_n: int = 10):
        """Print the top-N combinations in a formatted table."""
        sep = "─" * 110
        print(f"\n{sep}")
        print(f"  TOP {top_n} CONFIGURATIONS")
        print(sep)
        hdr = (f"  {'Rank':>4}  {'Score':>6}  {'WR%':>5}  {'PnL%':>7}  "
               f"{'PF':>5}  {'DD%':>6}  {'Shrpe':>6}  {'Trades':>6}  "
               f"ScTh  RefSc  ADXmn  RSIlo  VolSp  RRmn")
        print(hdr)
        print(f"  {'─'*4}  {'─'*6}  {'─'*5}  {'─'*7}  "
              f"{'─'*5}  {'─'*6}  {'─'*6}  {'─'*6}  "
              f"{'─'*5} {'─'*6} {'─'*5} {'─'*5} {'─'*5} {'─'*4}")

        for rank, (params, s, score) in enumerate(self.results[:top_n], 1):
            pnl_sign = "+" if s.total_pnl_pct >= 0 else ""
            print(
                f"  {rank:>4}  {score:>6.3f}  {s.win_rate:>5.1f}  "
                f"{pnl_sign}{s.total_pnl_pct:>6.2f}%  "
                f"{s.profit_factor:>5.2f}  {s.max_drawdown_pct:>5.2f}%  "
                f"{s.sharpe_ratio:>6.2f}  {s.total_trades:>6}  "
                f"{params['SCORE_THRESHOLD']:>5.1f} "
                f"{params['REFINED_SCORE_THRESHOLD']:>5.1f}  "
                f"{params['ADX_MIN']:>4}  "
                f"{params['RSI_LOWER']:>4}  "
                f"{params['VOLUME_SPIKE_THRESHOLD']:>5.1f}  "
                f"{params['RR_MIN']:>4.1f}"
            )
        print(sep)

        if self.results:
            best_params, best_s, _ = self.results[0]
            print(f"\n  ✅  RECOMMENDED CONFIG CHANGES:")
            print(f"       SCORE_THRESHOLD          = {best_params['SCORE_THRESHOLD']}")
            print(f"       REFINED_SCORE_THRESHOLD  = {best_params['REFINED_SCORE_THRESHOLD']}")
            print(f"       ADX_BOUNDS               = [{best_params['ADX_MIN']}, {BASE_CONFIG.get('ADX_BOUNDS',[20,65])[1]}]")
            print(f"       RSI_BOUNDS               = [{best_params['RSI_LOWER']}, {BASE_CONFIG.get('RSI_BOUNDS',[50,70])[1]}]")
            print(f"       VOLUME_SPIKE_THRESHOLD   = {best_params['VOLUME_SPIKE_THRESHOLD']}")
            print(f"       RR_THRESHOLD             = [{best_params['RR_MIN']}, {BASE_CONFIG.get('RR_THRESHOLD',[1.0,2.5])[1]}]")
        print(sep + "\n")


# ╔══════════════════════════════════════════════════════════════════════════════
# ║  CLI
# ╚══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Config Parameter Optimizer")
    parser.add_argument("--days",    type=float, default=2.0)
    parser.add_argument("--top",     type=int,   default=20,
                        help="Number of top pairs to test on")
    parser.add_argument("--workers", type=int,   default=2)
    parser.add_argument("--show",    type=int,   default=10,
                        help="Number of top results to display")
    args = parser.parse_args()

    pairs = BacktestEngine.get_top_pairs(args.top)
    print(f"Testing on pairs: {pairs}")

    opt = ConfigOptimizer()
    opt.run(
        pairs=pairs,
        n_candles=int(args.days * 24 * 60),
        max_workers=args.workers,
    )
    opt.print_report(top_n=args.show)
