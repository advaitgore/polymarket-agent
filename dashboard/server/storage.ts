import type {
  Market, Outcome, MarketWithOutcomes, Signal, Trade,
  PricePoint, DashboardStats
} from "@shared/schema";

export interface IStorage {
  getMarkets(): Promise<MarketWithOutcomes[]>;
  getSignals(limit?: number): Promise<Signal[]>;
  getTrades(): Promise<Trade[]>;
  getPriceHistory(marketId: string, outcome: string): Promise<PricePoint[]>;
  getStats(): Promise<DashboardStats>;
  getSystemLog(): Promise<string>;
}

// In-memory store for caching reads
class MemStorage implements IStorage {
  async getMarkets(): Promise<MarketWithOutcomes[]> { return []; }
  async getSignals(): Promise<Signal[]> { return []; }
  async getTrades(): Promise<Trade[]> { return []; }
  async getPriceHistory(): Promise<PricePoint[]> { return []; }
  async getStats(): Promise<DashboardStats> {
    return { total_markets: 0, signals_24h: 0, open_trades: 0, total_pnl: 0, unexplained_edges: 0, top_edge_score: 0 };
  }
  async getSystemLog(): Promise<string> { return ""; }
}

export const storage = new MemStorage();
