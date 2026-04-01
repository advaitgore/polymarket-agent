# Polymarket Trading Agent

A fully automated Polymarket signal detection and simulated paper trading system.

## Architecture

```
polymarket-agent/
├── pipeline/        # Python backend — signal detection, trade execution
└── dashboard/       # React + Express web dashboard
```

## Pipeline (`pipeline/`)

Python pipeline that runs every 15 minutes during US market hours (Mon–Fri 07:30–18:00 ET):

1. **Fetch** — pulls 500 active Polymarket markets via public Gamma API (no auth)
2. **Detect** — scores probability movements as signals, classifies by theme
3. **News check** — verifies signals against public news sources
4. **Trade** — simulates fills using real Yahoo Finance stock prices (no broker)
5. **Mark** — updates open positions with live stock prices every cycle
6. **Adapt** — adjusts theme→ticker weights based on closed trade P&L

### Themes → Instruments
| Theme | Primary | Universe |
|---|---|---|
| `energy_geopolitics` | XLE | XOP, USO, XOM, CVX |
| `defense_geopolitics` | LMT | NOC, RTX, ITA |
| `us_politics_macro` | SPY | QQQ, XLF, XLI, IWM |
| `global_macro` | TLT | IEF, SPY, QQQ, XLF, GLD |
| `tech_ai` | QQQ | NVDA, MSFT, AAPL, GOOGL, META, AMZN, TSLA |
| `crypto_major` | IBIT | ETHA, MSTR |

### Risk Management
- **Account equity:** $1,000 virtual
- **Risk per trade:** 1% of equity ($10)
- **Max open positions:** 10
- **Min risk/reward:** 2:1
- **Auto-close:** stop-loss, take-profit, or 10 trading days
- **Simulated fills only** — no broker connectivity

### Key Files
| File | Purpose |
|---|---|
| `main.py` | Single-shot pipeline cycle |
| `run_hour.py` | Hourly driver for external schedulers |
| `watchdog.py` | Always-on wrapper that restarts the pipeline if it dies |
| `fetch_markets.py` | Polymarket Gamma API fetcher |
| `detect_signals.py` | Signal classification (theme + tradability) |
| `trade_executor.py` | Position sizing + Yahoo Finance prices |
| `news_checker.py` | Public news verification |
| `adaptive_mapper.py` | Daily theme weight updates |
| `config.py` | All tunable parameters |
| `correlations.json` | Theme→ticker mappings + non-tradable blocklist |
| `data/signals.csv` | Full signal feed |
| `data/trades.csv` | Simulated trade log |

### Running the Pipeline
```bash
cd pipeline
pip install requests pandas
python main.py
```

`main.py` runs one cycle and exits. For an always-on local worker, keep
`watchdog.py` running in a separate terminal or background task:

```bash
cd pipeline
python watchdog.py
```

If you prefer external scheduling, `run_hour.py` is a short-lived hourly
driver that can be launched by Task Scheduler, cron, or another host-level
timer.

## Dashboard (`dashboard/`)

React + Express web app — reads `signals.csv` and `trades.csv` in real time.

- **Live Markets** — 500 Polymarket markets with probability charts
- **Signals** — full signal feed with theme badges, tradability filter
- **Trades** — open/closed positions with real stock P&L
- **Performance** — theme breakdown, equity curve

### Running the Dashboard
```bash
cd dashboard
npm install
npm run dev       # development
npm run build     # production build
NODE_ENV=production node dist/index.cjs
```

## Data Files

- `pipeline/data/signals.csv` and `pipeline/data/trades.csv` are generated on the host
- `pipeline/data/markets.db` — SQLite, excluded from repo (regenerated on first run)

## No External Secrets Required

- Polymarket: public Gamma API, no key
- Stock prices: Yahoo Finance public API, no key
- News: DuckDuckGo/RSS fallback chain, no key
- No IBKR, no broker API, no trading credentials
