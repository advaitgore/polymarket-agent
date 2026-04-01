import type { Express } from "express";
import Database from "better-sqlite3";
import fs from "fs";
import path from "path";
import { parse } from "csv-parse/sync";
import type {
  MarketWithOutcomes, Signal, Trade, PricePoint, DashboardStats
} from "@shared/schema";

const PROJECT_ROOT = path.resolve(process.cwd(), "..");
const PIPELINE_DIR = path.resolve(PROJECT_ROOT, "pipeline");
const DB_PATH = path.resolve(PIPELINE_DIR, "data", "markets.db");
const SIGNALS_CSV = path.resolve(PIPELINE_DIR, "data", "signals.csv");
const TRADES_CSV = path.resolve(PIPELINE_DIR, "data", "trades.csv");
const LOG_FILE = path.resolve(PIPELINE_DIR, "logs", "system.log");

// ── CSV parse helper ─────────────────────────────────────────────────────────
function parseCsv(filePath: string): any[] {
  if (!fs.existsSync(filePath)) return [];
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return parse(raw, { columns: true, skip_empty_lines: true, relax_column_count: true });
  } catch { return []; }
}

// ── DB query helper ──────────────────────────────────────────────────────────
function withDb<T>(fn: (db: Database.Database) => T): T | null {
  if (!fs.existsSync(DB_PATH)) return null;
  const db = new Database(DB_PATH, { readonly: true });
  try {
    return fn(db);
  } finally {
    db.close();
  }
}

export function registerRoutes(server: any, app: Express) {

  // ── GET /api/stats ──────────────────────────────────────────────────────────
  app.get("/api/stats", (_req, res) => {
    const ACCOUNT_BASE = 1000;
    const stats: DashboardStats = {
      total_markets: 0, signals_24h: 0, open_trades: 0,
      total_pnl: 0, realized_pnl: 0, account_equity: ACCOUNT_BASE,
      unexplained_edges: 0, top_edge_score: 0
    };

    withDb(db => {
      stats.total_markets = (db.prepare("SELECT COUNT(*) as c FROM markets WHERE active=1").get() as any).c;
    });

    const signals = parseCsv(SIGNALS_CSV);
    const cutoff = new Date(Date.now() - 86400000).toISOString();
    const recent = signals.filter(s => s.timestamp >= cutoff);
    stats.signals_24h = recent.length;
    // Only count tradable unexplained edges (exclude sports/celebrity/novelty noise)
    stats.unexplained_edges = recent.filter(s =>
      (s.explained === "0" || s.explained === 0) &&
      (s.trade_eligible === undefined || parseInt(s.trade_eligible as any) !== 0)
    ).length;
    if (recent.length > 0) {
      stats.top_edge_score = Math.max(...recent.map(s => parseFloat(s.edge_score) || 0));
    }

    const trades = parseCsv(TRADES_CSV);
    const openTrades   = trades.filter(t => t.status === "OPEN");
    const closedTrades = trades.filter(t => t.status === "CLOSED");
    stats.open_trades   = openTrades.length;
    stats.total_pnl     = openTrades.reduce((sum, t) => sum + (parseFloat(t.unrealized_pnl as any) || 0), 0);
    stats.realized_pnl  = closedTrades.reduce((sum, t) => sum + (parseFloat(t.realized_pnl as any) || 0), 0);
    stats.account_equity = ACCOUNT_BASE + stats.realized_pnl;

    res.json(stats);
  });

  // ── GET /api/markets ────────────────────────────────────────────────────────
  app.get("/api/markets", (req, res) => {
    const limit = parseInt(req.query.limit as string) || 100;

    const markets = withDb(db => {
      return db.prepare(`
        SELECT m.id, m.question, m.category, m.volume_usd, m.end_date, m.last_updated,
               o.outcome_name, o.current_price
        FROM markets m
        JOIN outcomes o ON m.id = o.market_id
        WHERE m.active = 1
        ORDER BY m.volume_usd DESC
        LIMIT ?
      `).all(limit * 2) as any[]; // *2 because each market has ~2 outcomes
    }) || [];

    // Group by market
    const grouped: Record<string, MarketWithOutcomes> = {};
    for (const row of markets) {
      if (!grouped[row.id]) {
        grouped[row.id] = {
          id: row.id, question: row.question, category: row.category || "Other",
          volume_usd: row.volume_usd, end_date: row.end_date,
          last_updated: row.last_updated, active: 1, outcomes: []
        };
      }
      grouped[row.id].outcomes.push({
        market_id: row.id, outcome_name: row.outcome_name,
        token_id: "", current_price: row.current_price,
        last_updated: row.last_updated
      });
    }

    // Attach signal changes
    const signals = parseCsv(SIGNALS_CSV);
    const latestSignal: Record<string, number> = {};
    for (const s of signals) {
      const key = `${s.market_id}_${s.outcome}`;
      if (!latestSignal[key]) latestSignal[key] = parseFloat(s.change_pp) || 0;
    }

    const result = Object.values(grouped).slice(0, limit).map(m => ({
      ...m,
      change_pp: m.outcomes.reduce((max, o) => {
        const k = `${m.id}_${o.outcome_name}`;
        return Math.max(max, Math.abs(latestSignal[k] || 0));
      }, 0),
      is_edge: m.outcomes.some(o => {
        const k = `${m.id}_${o.outcome_name}`;
        return Math.abs(latestSignal[k] || 0) >= 5;
      })
    }));

    res.json(result);
  });

  // ── GET /api/signals ────────────────────────────────────────────────────────
  app.get("/api/signals", (req, res) => {
    const limit = parseInt(req.query.limit as string) || 200;
    const filterExplained = req.query.explained;

    let signals = parseCsv(SIGNALS_CSV) as Signal[];
    signals = signals.map(s => ({
      ...s,
      old_prob: parseFloat(s.old_prob as any) || 0,
      new_prob: parseFloat(s.new_prob as any) || 0,
      change_pp: parseFloat(s.change_pp as any) || 0,
      edge_score: parseFloat(s.edge_score as any) || 0,
      explained: parseInt(s.explained as any) || 0,
      theme: (s.theme as any) || "none",
      trade_eligible: parseInt((s.trade_eligible as any) ?? "1") || 0,
    }));

    if (filterExplained === "0") signals = signals.filter(s => s.explained === 0);
    if (filterExplained === "1") signals = signals.filter(s => s.explained === 1);

    signals.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    res.json(signals.slice(0, limit));
  });

  // ── GET /api/trades ─────────────────────────────────────────────────────────
  app.get("/api/trades", (_req, res) => {
    let trades = parseCsv(TRADES_CSV) as Trade[];
    const _pf = (v: any) => { const n = parseFloat(v); return isNaN(n) ? "" : n; };
    trades = trades.map(t => ({
      ...t,
      // numeric coercions
      quantity:        parseFloat(t.quantity as any)       || 0,
      entry_price:     parseFloat(t.entry_price as any)    || 0,
      mark_price:      parseFloat(t.mark_price as any)     || 0,
      unrealized_pnl:  parseFloat(t.unrealized_pnl as any) || 0,
      edge_score:      parseFloat(t.edge_score as any)     || 0,
      // risk-management fields (blank on legacy rows)
      stop_loss:       _pf(t.stop_loss),
      take_profit:     _pf(t.take_profit),
      realized_pnl:    _pf(t.realized_pnl),
      open_date:       (t.open_date  as any) || "",
      close_date:      (t.close_date as any) || "",
      close_reason:    (t.close_reason as any) || "",
      // identity fields
      trade_id:        t.trade_id        || "",
      signal_id:       t.signal_id       || "",
      execution_venue: t.execution_venue || "simulated",
      broker:          t.broker          || "none",
      theme:           (t.theme as any)   || "unclassified",
    }));
    trades.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
    res.json(trades);
  });

  // ── GET /api/price-history ──────────────────────────────────────────────────
  app.get("/api/price-history", (req, res) => {
    const { market_id, outcome } = req.query;
    if (!market_id || !outcome) return res.json([]);

    const rows = withDb(db => {
      return db.prepare(`
        SELECT price, timestamp FROM price_history
        WHERE market_id = ? AND outcome_name = ?
        ORDER BY timestamp ASC
        LIMIT 500
      `).all(market_id, outcome) as any[];
    }) || [];

    res.json(rows.map(r => ({ price: r.price * 100, timestamp: r.timestamp })));
  });

  // ── GET /api/theme-performance ─────────────────────────────────────────────
  app.get("/api/theme-performance", (_req, res) => {
    const trades = parseCsv(TRADES_CSV);
    const signals = parseCsv(SIGNALS_CSV);
    const cutoff24h = new Date(Date.now() - 86400000).toISOString();

    type ThemeEntry = {
      theme: string; total_trades: number; open_trades: number;
      realized_pnl: number; unrealized_pnl: number;
      win_trades: number; loss_trades: number; signals_24h: number;
    };
    const themeMap: Record<string, ThemeEntry> = {};

    const getOrInit = (theme: string): ThemeEntry => {
      if (!themeMap[theme]) {
        themeMap[theme] = { theme, total_trades: 0, open_trades: 0,
          realized_pnl: 0, unrealized_pnl: 0, win_trades: 0, loss_trades: 0, signals_24h: 0 };
      }
      return themeMap[theme];
    };

    for (const t of trades) {
      const theme = ((t.theme as any) || "unclassified").trim();
      const entry = getOrInit(theme);
      entry.total_trades++;
      if (t.status === "OPEN") {
        entry.open_trades++;
        entry.unrealized_pnl += parseFloat(t.unrealized_pnl as any) || 0;
      } else if (t.status === "CLOSED") {
        const rpnl = parseFloat(t.realized_pnl as any) || 0;
        entry.realized_pnl += rpnl;
        if (rpnl > 0) entry.win_trades++;
        else if (rpnl < 0) entry.loss_trades++;
      }
    }

    for (const s of signals) {
      if ((s.timestamp as string) < cutoff24h) continue;
      const theme = ((s.theme as any) || "unclassified").trim();
      getOrInit(theme).signals_24h++;
    }

    const result = Object.values(themeMap).map(t => ({
      ...t,
      realized_pnl: Math.round(t.realized_pnl * 100) / 100,
      unrealized_pnl: Math.round(t.unrealized_pnl * 100) / 100,
      win_rate: t.win_trades + t.loss_trades > 0
        ? Math.round((t.win_trades / (t.win_trades + t.loss_trades)) * 100)
        : null,
    })).sort((a, b) => b.signals_24h - a.signals_24h);

    res.json(result);
  });

  // ── GET /api/log ────────────────────────────────────────────────────────────
  app.get("/api/log", (_req, res) => {
    const logPath = (fs.existsSync(LOG_FILE) && fs.statSync(LOG_FILE).size > 0)
      ? LOG_FILE
      : null;
    if (!logPath) return res.json({ log: "Log file not found yet." });
    const lines = fs.readFileSync(logPath, "utf-8").split("\n").slice(-60).join("\n");
    res.json({ log: lines });
  });
}
