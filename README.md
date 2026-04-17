# CryptoBOT – Hyperliquid Perps Engine

Headless, multi-market perpetuals trading bot for [Hyperliquid](https://hyperliquid.xyz).
Evaluates signals on **5m candle close** across a ranked universe of markets,
using a semi-dynamic regime engine (15m ADX/ATR) and a BTC 1h global filter.

---

## Architecture overview

```
bot/
├── config.py          Config dataclass – all tunables, loaded from .env
├── hl_client.py       Hyperliquid REST (aiohttp) + WebSocket (auto-reconnect)
├── candles.py         Rolling OHLCV cache (closed-bar only)
├── indicators.py      Pure-pandas: EMA, ATR, RSI, MACD, ADX, BB, VWAP, Stoch, HMA
├── regime.py          Per-market regime (TREND/RANGE/NO_TRADE on 15m)
│                      + Global BTC bias (BULL/BEAR/NEUTRAL on 1h)
├── universe.py        Universe scan → quality filter → ranked active set
│                      Maintains ≤ MAX_ACTIVE_SYMBOLS WS subscriptions
├── strategies/
│   ├── trend.py       TREND module – EMA breakout + ATR stop + 1R partial + trail
│   └── range_mean.py  RANGE module – VWAP band fade, limit-preferred
├── risk.py            Position sizing (% equity / ATR stop), concurrent limits,
│                      daily/weekly loss halts, per-symbol cooldowns, exposure caps
├── execution.py       Order placement (dry-run & live), partial TP, trailing SL
├── journal.py         Append-only JSONL trade log + daily summary
├── state.py           Restart-safe JSON persistence (positions, cooldowns, P&L)
└── main.py            Async orchestration loop
```

### Signal flow (every 5m candle close)
```
WS candle update → CandleCache.on_ws_candle()
  → closed=True  → _on_candle_close(coin)
    → RegimeEngine.classify(coin, 15m)   [TREND / RANGE / NO_TRADE]
    → GlobalBias from BTC 1h
    → TrendStrategy.evaluate()  OR  RangeStrategy.evaluate()
    → RiskManager.check_entry()          [size, limits, cooldowns]
    → ExecutionEngine.open_position()    [dry-run or live]
    → Journal.log_open()
```

---

## Quickstart

### 1. Clone & configure

```bash
git clone <repo>
cd Crypto_BOT
cp .env.example .env
# Edit .env – set HL_WALLET_ADDRESS and HL_PRIVATE_KEY
# Keep TESTNET=true and DRY_RUN=true for initial testing
```

### 2. Install locally (dev / testing)

```bash
pip install -r requirements_hl.txt
python -m bot.main
```

### 3. Docker (recommended for VPS)

```bash
# Build
docker compose build

# Run (dry-run on testnet – safe to start)
docker compose up -d

# Follow logs
docker compose logs -f cryptobot

# Stop gracefully
docker compose stop
```

---

## Configuration reference

All settings live in `.env`. Full list in `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `TESTNET` | `true` | Use Hyperliquid testnet |
| `DRY_RUN` | `true` | Log signals, never send orders |
| `HL_WALLET_ADDRESS` | – | Your EVM wallet (0x…) |
| `HL_PRIVATE_KEY` | – | Trading wallet private key |
| `RISK_PCT` | `0.0025` | Equity at risk per trade (0.25%) |
| `MAX_RISK_PCT` | `0.005` | Hard cap (0.5%) |
| `MAX_CONCURRENT_POSITIONS` | `3` | Max open trades |
| `DEFAULT_LEVERAGE` | `3` | Isolated leverage per position |
| `DAILY_LOSS_PCT` | `0.01` | 1% daily loss → halt trading |
| `WEEKLY_LOSS_PCT` | `0.03` | 3% weekly loss → halt trading |
| `COOLDOWN_AFTER_STOP_MIN` | `60` | Per-symbol cooldown after stopout |
| `MIN_VOLUME_24H_USD` | `5000000` | Min $5M 24h volume filter |
| `MAX_ACTIVE_SYMBOLS` | `8` | Max simultaneous WS subscriptions |
| `UNIVERSE_SCAN_INTERVAL_MIN` | `15` | Universe rescan frequency |
| `ADX_TREND_THRESHOLD` | `25.0` | ADX ≥ this → TREND regime |
| `ATR_PCT_MIN` | `0.003` | Min volatility to trade (0.3%) |
| `ATR_PCT_MAX` | `0.05` | Max volatility to trade (5%) |
| `TREND_ATR_SL_MULT` | `1.5` | SL = entry ± ATR × 1.5 |
| `TREND_TRAIL_ATR_MULT` | `2.0` | Trailing stop distance = ATR × 2 |
| `RANGE_VWAP_BAND_ATR_MULT` | `1.5` | VWAP ± ATR×1.5 = fade zone |
| `RANGE_TP_R` | `1.5` | Range trade R:R target |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |

---

## Going live checklist

- [ ] Test on **testnet** with `DRY_RUN=true` for ≥ 48 h – verify logs look sane
- [ ] Test with `DRY_RUN=false TESTNET=true` – orders hit testnet exchange
- [ ] Review `data/journal.jsonl` – P&L, signal quality, regime distribution
- [ ] Fund a **dedicated sub-account** on Hyperliquid mainnet
- [ ] Use a **fresh API key** scoped to the sub-account only
- [ ] Set `TESTNET=false DRY_RUN=false` in `.env`
- [ ] Start with `DEFAULT_LEVERAGE=2` and `RISK_PCT=0.001` until comfortable
- [ ] Monitor `data/bot.log` and journal daily

---

## Monitoring

```bash
# Live log tail
docker compose logs -f --tail=100 cryptobot

# Journal (all trades)
cat data/journal.jsonl | python -m json.tool | less

# Today's P&L summary
python - <<'EOF'
import json, datetime
today = datetime.date.today().isoformat()
pnl = sum(
    r["pnl"] for r in (json.loads(l) for l in open("data/journal.jsonl"))
    if r.get("event") == "close" and r.get("ts","").startswith(today)
)
print(f"Today's realised P&L: {pnl:+.4f} USD")
EOF

# Force resume after manual halt review
# Edit data/state.json → set "halted": false, then restart container
```

---

## Data files

| File | Contents |
|---|---|
| `data/journal.jsonl` | Append-only trade log (open/partial/close/summary events) |
| `data/state.json` | Restart checkpoint (positions, cooldowns, loss counters) |
| `data/bot.log` | Structured application log (rotated by Docker logging driver) |

---

## Strategy details

### TREND module (primary)
- **Regime gate:** 15m ADX ≥ `ADX_TREND_THRESHOLD` + ATR% in bounds
- **Entry:** price breaks swing high/low + EMA fast/slow alignment + MACD histogram confirmation
- **Size:** `equity × RISK_PCT / (ATR × TREND_ATR_SL_MULT)`
- **Exit 1 (partial):** close `TREND_PARTIAL_CLOSE_PCT` (50%) at 1R
- **Exit 2 (trail):** remainder trailed at `ATR × TREND_TRAIL_ATR_MULT`
- **Global filter:** skip longs if BTC 1h BEAR; skip shorts if BTC 1h BULL

### RANGE module (secondary)
- **Regime gate:** 15m ADX < `RANGE_MAX_ADX` + ATR% in bounds
- **Entry:** price touches VWAP ± (ATR × `RANGE_VWAP_BAND_ATR_MULT`) + RSI extreme + Stochastic turning
- **TP:** VWAP mean-reversion target
- **SL:** `ATR × RANGE_ATR_SL_MULT` beyond entry
- **Order type:** limit/maker preferred (`is_limit=True`)

### Risk engine
- Hard cap: 3 concurrent positions (configurable)
- Directional cap: max 15% long / 15% short notional of equity
- Daily loss ≥ 1% → halt until UTC midnight reset
- Weekly loss ≥ 3% → halt until Monday UTC midnight reset
- Stopout → 60-min per-symbol cooldown

---

## Extending

**Add a new indicator:** edit `bot/indicators.py` – `compute_all()` returns a dict of Series.

**Add a new strategy:** create `bot/strategies/my_strategy.py` returning a signal dataclass,
then wire it in `bot/main.py::_on_candle_close()`.

**Change universe ranking:** edit `bot/universe.py::_rank_candidates()`.
