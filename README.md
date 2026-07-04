# Polymarket Trading Agent

A fully automated system that turns Polymarket probability moves into simulated
equity/ETF paper trades, using real Yahoo Finance prices. No broker, no
credentials, no real money.

## What this system actually is (and what we learned)

The core thesis: a move in a Polymarket contract can lead a move in a
correlated equity, because equities do not reprice event probabilities
instantly. Research and our own trade history refined this into a sharper,
more honest picture:

- **Genuine alpha lives in two channels.** Defense-geopolitical names
  (LMT, NOC, RTX, GD, ITA, PLTR) reprice geopolitical events with a real lag of
  hours to days, because institutional defense-portfolio rebalancing is slow.
  Crypto proxies (IBIT, ETHA, MSTR) track Bitcoin/Ethereum level markets with a
  measurable multi-hour wedge to fair value.
- **Some themes are beta, not alpha.** SPY on macro/politics, TLT on Fed rates,
  and XLE on oil-supply shocks all reprice faster than the 15-minute polling
  cycle. By the time the pipeline sees the Polymarket move, the equity has
  already adjusted. These themes are gated and quality-discounted, not removed
  (they stay alive so the adaptive layer keeps learning).
- **Execution/exit quality matters as much as entry.** A trailing stop,
  event-resolution exit, and theme-differentiated hold periods protect P&L that
  the original fixed-exit design gave back.

## Architecture

```
polymarket-agent/
|-- pipeline/     # Python backend: signal detection, scoring, trade execution
|-- dashboard/    # React + Express web dashboard
```

## Pipeline (`pipeline/`)

Runs one single-shot cycle every 15 minutes during US market hours
(Mon-Fri 07:30-18:00 ET), re-invoked by an external scheduler:

1. **Fetch** - pulls active Polymarket markets via the public Gamma API (no auth).
2. **Detect** - scores 24h probability moves, classifies by theme, computes an
   instrument-quality-adjusted edge score.
3. **Trade** - selects the best eligible signal, sizes it against real Yahoo
   Finance prices, applies gates, and opens a simulated position.
4. **Mark** - marks open positions to live prices; runs resolution-exit,
   trailing-stop, and close-trigger logic every cycle.
5. **Adapt** - daily, adjusts theme->ticker weights from realized P&L and prunes
   stale weight keys.

(A news-verification step exists in the codebase but is disabled;
`NEWS_CHECK_ENABLED = False`.)

### Edge score and instrument quality

```
raw_edge  = (|prob_change_pp| / 5) * recency * confidence
quality   = 0.4 * correlation_quality(theme) + 0.6 * max(0.1, theme_win_rate)
edge      = raw_edge * quality
```

`correlation_quality` is a per-theme static prior in `correlations.json`;
`theme_win_rate` is the rolling win rate from closed trades. Signals whose
adjusted edge falls below `MIN_EDGE_SCORE` (currently 1.0) are not
trade-eligible. Per-theme gates apply a higher bar to the beta themes.

### Themes -> Instruments

| Theme | Primary | Universe | correlation_quality | Max hold (trading days) |
|---|---|---|---|---|
| `defense_geopolitics` | LMT | NOC, RTX, ITA, GD, PLTR | 0.92 | 12 |
| `crypto_major` | IBIT | ETHA, MSTR | 0.87 | 5 |
| `tech_ai` | QQQ | NVDA, MSFT, AAPL, GOOGL, META, AMZN, TSLA | 0.85 | 6 |
| `us_politics_macro` | SPY | QQQ, XLF, XLI, IWM | 0.70 | 8 |
| `global_macro` | TLT | IEF, SPY, QQQ, XLF, GLD | 0.55 | 7 |
| `energy_geopolitics` | XLE | XOP, USO, XOM, CVX | 0.50 | 7 |

Defense and crypto carry the highest quality priors (the alpha channels).
`us_politics_macro`, `global_macro`, and `energy_geopolitics` are discounted
because they are dominated by fast-repricing beta.

### Entry gates (in `select_best_signal`)

- **Neutral-sentiment skip** - ambiguous direction is not traded.
- **energy_geopolitics gate** - blocks `case_by_case` direction; requires edge
  >= 4.5 (relaxes to 3.5 once rolling win rate > 40%).
- **global_macro gate** - requires edge >= 3.5 (relaxes to 2.5 once win rate
  > 45%).
- **Factor-bucket cap** - at most one open position per bucket
  (equity_index, energy, defense, crypto, tech_single, rates).
- **Cooldowns** - per market-side and per symbol re-entry (24h).
- **Realized-vol gate** - blocks/derisks when 1d/30d vol ratio is elevated.
- **MIN_EDGE_SCORE floor** - universal post-quality-adjustment floor (1.0).

### Exit logic (in `update_mark_prices` / `_check_close_triggers`)

Priority order per cycle:

1. **RESOLUTION_EXIT** - if the triggering Polymarket market has effectively
   resolved (max outcome probability >= 0.85 or <= 0.15), close immediately: the
   equity has already repriced and the thesis is spent. Fails open with a warning
   if the probability is unavailable.
2. **Trailing stop / breakeven** - once a position reaches 50% of its
   take-profit distance the stop ratchets to breakeven; past 75% it trails at
   40% of the TP distance behind the mark. The stop never widens.
3. **SL_HIT / TP_HIT** - stop-loss or take-profit touched.
4. **TIME_EXIT** - theme-differentiated max hold reached (see table above).

### Risk management

- **Account equity:** $1,000 virtual
- **Risk per trade:** ~2% of equity (`RISK_PCT`), scaled down in elevated vol
- **Max open positions:** 10
- **Min risk/reward:** 2:1 (`RR_MIN`)
- **Stops:** ATR-based (14d, 1.5x), floored at 5% of entry (`ATR_MIN_STOP_FRACTION`)
- **Simulated fills only** - no broker connectivity

### Key files

| File | Purpose |
|---|---|
| `main.py` | Single-shot pipeline cycle |
| `run_hour.py` | Hourly driver for external schedulers |
| `watchdog.py` | Always-on wrapper that restarts the pipeline if it dies |
| `fetch_markets.py` | Polymarket Gamma API fetcher |
| `detect_signals.py` | Theme classification, edge + instrument-quality scoring |
| `trade_executor.py` | Sizing, gates, exits, Yahoo Finance prices |
| `adaptive_mapper.py` | Daily theme-weight updates + stale-key pruning |
| `config.py` | All tunable parameters |
| `correlations.json` | Theme mappings, correlation_quality, blocklist |
| `correlations_weights.json` | Adaptive theme->ticker weights (managed) |
| `data/signals.csv` | Full signal feed |
| `data/trades.csv` | Simulated trade log |

### Running the pipeline

```bash
cd pipeline
pip install requests pandas
python main.py        # one cycle
python watchdog.py    # always-on local worker
```

`run_hour.py` is a short-lived hourly driver for Task Scheduler / cron.

## Dashboard (`dashboard/`)

React + Express web app that reads `signals.csv` and `trades.csv` in real time:
Live Markets, Signals (with theme badges + tradability filter), Trades
(open/closed with real stock P&L), and Performance (theme breakdown, equity
curve).

```bash
cd dashboard
npm install
npm run dev                       # development
npm run build                     # production build
NODE_ENV=production node dist/index.cjs
```

## Data files

- `data/signals.csv` and `data/trades.csv` are generated on the host.
- `data/markets.db` (SQLite) is excluded from the repo and regenerated on first run.

## No external secrets required

- Polymarket: public Gamma API, no key
- Stock prices: Yahoo Finance public API, no key
- No broker, no trading credentials

## Honest limitations

- The system is a late entrant in the information chain: it reacts to Polymarket
  moves rather than the news that causes them. Its edge is the equity-repricing
  lag, which is real for defense/crypto and largely absent for macro/rates.
- Profitability to date is partly episodic (clusters around major narratives)
  and partly beta. The gates and quality weights push the system toward the
  alpha channels, but do not manufacture alpha where none exists.
- The trade sample is small. `MIN_EDGE_SCORE` and the correlation_quality priors
  are defensible but not yet empirically validated at scale; they should be
  retuned as closed-trade count grows.
