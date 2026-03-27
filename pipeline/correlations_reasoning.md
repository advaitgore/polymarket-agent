# Polymarket → Tradable Instrument Correlations: Reasoning

## Framework

Prediction markets price the **probability** of specific events. When that probability changes significantly faster than correlated financial instruments react, a **temporal arbitrage** opportunity may exist. The core logic:

> If Polymarket says P(event) rose sharply but the correlated equity/ETF hasn't moved, either:
> 1. The market hasn't absorbed the information yet (edge exists), or
> 2. The prediction market move is noise/manipulation (no edge)

The news check step distinguishes these two cases.

---

## Correlation Categories

### Macro / Interest Rates
| Market Type | Instrument | Reasoning |
|---|---|---|
| Fed rate decisions | TLT | 20yr Treasuries are the most rate-sensitive instrument. A rising cut probability should precede TLT rallies. |
| Inflation prints | TIPS | TIPS directly compensate for CPI; if Polymarket shows higher inflation probability, TIPS should follow. |
| Dollar strength | UUP | DXY proxy. Higher USD strength probability should lead UUP. |
| Recession | SHY | Flight to short-term safety. Recession probability rise should precede SHY rally. |

### Crypto
| Market Type | Instrument | Reasoning |
|---|---|---|
| Bitcoin price levels | IBIT | BlackRock's spot Bitcoin ETF is the most liquid BTC proxy in equities. |
| Ethereum price | ETHA | BlackRock's spot ETH ETF; direct proxy. |

### Politics & Policy
| Market Type | Instrument | Reasoning |
|---|---|---|
| Trump election probability | DJT | Trump Media literally tracks Trump's political fortune. |
| Democratic win | SPY | Markets broadly prefer status quo; Democratic wins in recent cycles correlated with stable SPY performance. |
| Elon Musk / DOGE | TSLA | Musk news carries disproportionate weight on Tesla stock regardless of topic. |
| TikTok ban | META | If TikTok disappears, Meta's Reels captures the displaced audience and ad revenue. |
| AI regulation | IGV | Software ETF with heavy AI exposure. Heavy regulation = headwind. |
| Debt ceiling | BIL | T-bill ETF; default risk makes short-dated bills unusually volatile. |

### Geopolitics
| Market Type | Instrument | Reasoning |
|---|---|---|
| Ukraine/Russia escalation | LMT | Defense contractors directly benefit from NATO spending increases. |
| US-China/Taiwan | FXI | Chinese large-cap ETF is most liquid expression of Sino-American tension. |
| Middle East war | ITA | Aerospace & Defense ETF captures broader defense spending expectations. |
| Nuclear/Iran | CCJ | Uranium miner. Nuclear power expansion is bullish; also geopolitical risk premium. |

### Individual Stocks
| Market Type | Instrument | Reasoning |
|---|---|---|
| NVIDIA / AI chips | NVDA | Direct ticker play. Polymarket often runs markets on NVDA earnings beats and price targets. |
| Apple | AAPL | Direct. App store antitrust, earnings, China ban risk. |
| Microsoft | MSFT | Direct + OpenAI proxy. OpenAI regulatory markets spill into MSFT. |
| Tesla | TSLA | Direct + Elon Musk proxy. Unusually high alpha in prediction market vs. stock divergences. |
| SpaceX markets | RKLB | Rocket Lab is the most liquid public space company; SpaceX success validates the sector. |

### Commodities
| Market Type | Instrument | Reasoning |
|---|---|---|
| Oil price | USO | WTI front-month crude oil ETF. OPEC decisions and supply shock markets. |
| Gold | GLD | Direct gold ETF. Gold is the most-traded safe-haven asset. |
| Natural gas | UNG | US Natural Gas Fund. Supply/demand shock markets. |
| Copper | COPX | Copper miners ETF; copper is a leading global growth indicator. |

### Sector / Thematic
| Market Type | Instrument | Reasoning |
|---|---|---|
| Biotech / FDA | XBI | Biotech ETF. FDA approval markets can move individual stocks 50%+; sector-level signal. |
| Semiconductors | SOXX | CHIPS Act, export controls, TSMC supply markets. |
| Housing | ITB | Homebuilders ETF responds to mortgage rate and housing market predictions. |
| Clean energy | ICLN | IRA policy and renewable energy mandate Polymarkets directly price ICLN. |
| Airlines/Travel | JETS | Demand forecast and aviation policy markets. |
| Sports gambling | DKNG | DraftKings; sports outcome markets and gambling regulation Polymarkets. |

### International
| Market Type | Instrument | Reasoning |
|---|---|---|
| Japan / BOJ | EWJ + FXY | BOJ policy surprise markets can cause 2-3% daily moves in yen and Japanese equities. |
| UK politics | EWU | UK election and fiscal policy markets. |
| India | INDA | Modi policy and Indian economic growth markets. |
| Brazil | EWZ | Lula policy, commodity, and political risk markets. |
| China | FXI | Trade war, Taiwan, and Chinese economic policy markets. |

---

## Edge Score Formula

The edge score is computed as:

```
base_score = abs(prob_change_pp) / 5.0   # normalized, 5pp = 1.0 base
recency_bonus = 1.5 if change happened in last 6h else 1.0
news_multiplier = 0.3 if explained_by_news else 1.5
instrument_lag_bonus = 1.2 if instrument_has_not_moved else 0.8

edge_score = base_score * recency_bonus * news_multiplier * instrument_lag_bonus
```

Scores above 2.0 are considered high confidence. We trade only unexplained edges.

---

## Limitations & Caveats

1. **Liquidity on Polymarket is thin** for many markets — a single large bettor can move prices 10pp without informational content. The news check partially filters this.
2. **Direction logic** for politics is not universal — use with caution.
3. **Time-to-event matters** — a 5pp move 48h before resolution is very different from 30 days before.
4. **IBKR trade sizes are kept small** ($5 × position) specifically because edge scores have not been back-tested yet.
