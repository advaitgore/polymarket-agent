# Backtest Parity Update Summary

Date: 2026-04-15
Branch: main

## Why this change was made

The live pipeline had already been hardened with new requirements (minimum-history guard, recency-aware scoring, and market+side cooldown), but the backtest engine was still running older selection logic.

That meant prior backtest output could not reliably answer whether the new requirements improved strategy quality.

## What was changed

File changed:
- backtest/backtest_engine.py

Implemented parity behavior in the backtest engine:

1. Added parity config values:
- `MIN_HISTORY_HOURS_FOR_SIGNAL = 12.0`
- `MARKET_SIGNAL_COOLDOWN_HOURS = 24.0`

2. Added edge-scoring parity function:
- `compute_edge_score(prob_change_pp, hours_since_move, explained=False)`
- Includes recency multiplier so newer moves can receive higher score.

3. Added 24h delta + fallback parity:
- `get_24h_delta_with_anchor(...)`
- Uses true 24h anchor when available.
- Uses cold-start anchor only if enough history exists (minimum-history guard).

4. Added move recency estimation parity:
- `estimate_hours_since_move(...)`
- Finds the first threshold-cross timestamp in the last 24h window.

5. Reworked trade selection flow to mirror live behavior:
- Build candidate signals per bar.
- Rank candidates by `edge_score`.
- Select the single best tradable signal for the bar.
- Enforce market+side cooldown before entry.

## Backtest results after parity update

Latest updated run:
- Final equity: 1033.00
- Total PnL: +33.00 (+3.3%)
- Total signals: 4615
- Signals traded: 15
- Closed trades: 15
- Win rate: 53.3%
- Max drawdown: -68.13

Theme totals from that run:
- defense_geopolitics: +44.76
- energy_geopolitics: -120.96
- global_macro: +7.56
- us_politics_macro: +101.64

## Comparison to earlier pre-parity backtest run

Earlier run (before parity update) reported approximately:
- Final equity: 1072.87
- Total PnL: +72.87 (+7.3%)
- Closed trades: 14
- Win rate: 57.1%
- Max drawdown: -45.99

After parity update:
- PnL lower (+33.00)
- Slightly more trades (15)
- Lower win rate (53.3%)
- Worse drawdown (-68.13)

## Why this can happen even with >50% win rate (Expectancy)

Win rate alone does not determine profitability.

Long-run edge is driven by expectancy:

`E = p(win) * avg_win - (1 - p(win)) * avg_loss`

From the updated run:
- `p(win) = 0.533`
- `avg_win = 25.52`
- `avg_loss = 24.45`

Approximate expectancy per trade:
- `E ~= 0.533 * 25.52 - 0.467 * 24.45 ~= +2.2`

So the strategy still has only a small per-trade edge in this sample, and concentrated losses (especially energy-theme churn) can materially reduce total PnL even with a win rate above 50%.

## Scope and safety

Only the backtest strategy logic was committed for this update, along with this summary document. Runtime CSV/data churn and generated images were intentionally excluded from commit scope.
