import { pgTable, text, real, integer, serial } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

// ── Lightweight in-memory types (no DB — we read from SQLite/CSV directly) ──

export type Market = {
  id: string;
  question: string;
  category: string;
  end_date: string;
  volume_usd: number;
  active: number;
  last_updated: string;
};

export type Outcome = {
  market_id: string;
  outcome_name: string;
  token_id: string;
  current_price: number;
  last_updated: string;
};

export type MarketWithOutcomes = Market & {
  outcomes: Outcome[];
  change_pp?: number;
  is_edge?: boolean;
};

export type Signal = {
  timestamp: string;
  market_id: string;
  market_name: string;
  outcome: string;
  old_prob: number;
  new_prob: number;
  change_pp: number;
  correlated_instrument: string;
  edge_score: number;
  explained: number;
  news_summary: string;
  news_check_method?: string;
  theme: string;           // one of the 6 allowed themes, or "none"
  trade_eligible: number;  // 1 = tradable, 0 = noise (sports/celebrity/novelty)
};

export type Trade = {
  trade_id: string;          // short UUID — unique per simulated fill
  signal_id: string;         // market_id prefix that triggered this trade
  timestamp: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  // Risk-management levels (empty string on legacy rows)
  stop_loss: number | "";
  take_profit: number | "";
  open_date: string;
  close_date: string;
  realized_pnl: number | "";
  close_reason: string;      // "" | "SL_HIT" | "TP_HIT" | "TIME_EXIT"
  market_id: string;
  market_name: string;
  edge_score: number;
  execution_venue: string;   // always "simulated" — no broker connectivity
  broker: string;            // always "none" in sandbox
  status: string;            // "OPEN" | "CLOSED"
  theme: string;             // theme that generated this signal
};

export type PricePoint = {
  timestamp: string;
  price: number;
};

export type DashboardStats = {
  total_markets: number;
  signals_24h: number;
  open_trades: number;
  total_pnl: number;          // sum of unrealized_pnl for OPEN positions
  realized_pnl: number;       // sum of realized_pnl for CLOSED positions
  account_equity: number;     // 1000 + realized_pnl
  unexplained_edges: number;
  top_edge_score: number;
};
