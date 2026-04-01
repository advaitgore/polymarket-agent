import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ScatterChart, Scatter, ReferenceLine
} from "recharts";
import { format, parseISO } from "date-fns";

// ── Types ─────────────────────────────────────────────────────────────────────
type Stats = {
  total_markets: number; signals_24h: number; open_trades: number;
  total_pnl: number; realized_pnl: number; account_equity: number;
  unexplained_edges: number; top_edge_score: number;
};
type Market = {
  id: string; question: string; category: string;
  volume_usd: number; end_date: string; last_updated: string;
  outcomes: { outcome_name: string; current_price: number }[];
  change_pp: number; is_edge: boolean;
};
type Signal = {
  timestamp: string; market_id: string; market_name: string; outcome: string;
  old_prob: number; new_prob: number; change_pp: number;
  correlated_instrument: string; edge_score: number;
  explained: number; news_summary: string;
  news_check_method?: string;
  theme?: string;
  trade_eligible?: number; // 'sandbox_search' | 'duckduckgo' | 'rss' | 'unverified'
};
type Trade = {
  trade_id: string; signal_id: string;
  timestamp: string; symbol: string; side: string; quantity: number;
  entry_price: number; mark_price: number; unrealized_pnl: number;
  stop_loss: number | ""; take_profit: number | "";
  open_date: string; close_date: string;
  realized_pnl: number | ""; close_reason: string;
  market_id: string; market_name: string; edge_score: number;
  execution_venue: string; broker: string; status: string;
};
type PricePoint = { price: number; timestamp: string };

// ── Tabs ──────────────────────────────────────────────────────────────────────
const TABS = [
  { id: "markets", label: "Live Markets", icon: "◉" },
  { id: "signals", label: "Signals", icon: "⚡" },
  { id: "trades", label: "Trades", icon: "💼" },
  { id: "performance", label: "Performance", icon: "📈" },
] as const;
type TabId = typeof TABS[number]["id"];

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt$(n: number) { return `$${n.toFixed(2)}`; }
function fmtPct(n: number) { return `${(n).toFixed(1)}%`; }
function fmtChange(n: number) { return `${n > 0 ? "+" : ""}${n.toFixed(1)}pp`; }
function fmtTime(ts: string) {
  try { return format(parseISO(ts), "MM-dd HH:mm"); }
  catch { return ts?.slice(5, 16) || "—"; }
}
function clsChange(n: number) { return n > 0 ? "text-green-400" : n < 0 ? "text-red-400" : "text-slate-400"; }
function clsPnl(n: number) { return n > 0 ? "text-green-400 tabular" : n < 0 ? "text-red-400 tabular" : "text-slate-400 tabular"; }

// ── StatCard ──────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, accent }: { label: string; value: string | number; sub?: string; accent?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-1">
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
      <span className={`text-xl font-bold tabular ${accent || "text-foreground"}`}>{value}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

// ── Live dot ──────────────────────────────────────────────────────────────────
function LiveDot() {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="pulse-dot w-2 h-2 rounded-full bg-green-400 inline-block" />
      <span className="text-xs text-green-400 font-medium">LIVE</span>
    </span>
  );
}

// ── Logo SVG ──────────────────────────────────────────────────────────────────
function Logo() {
  return (
    <svg aria-label="Polymarket Trader" width="28" height="28" viewBox="0 0 28 28" fill="none">
      <rect width="28" height="28" rx="6" fill="hsl(195 100% 50% / 0.15)" stroke="hsl(195 100% 50% / 0.4)" strokeWidth="1"/>
      <path d="M6 20 L10 12 L14 16 L18 9 L22 14" stroke="hsl(195 100% 50%)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      <circle cx="22" cy="14" r="2" fill="hsl(195 100% 50%)"/>
    </svg>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [tab, setTab] = useState<TabId>("markets");
  const [marketFilter, setMarketFilter] = useState("");
  const [signalFilter, setSignalFilter] = useState<"all"|"unexplained"|"explained">("all");
  const [selectedMarket, setSelectedMarket] = useState<Market | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState<string>("Yes");
  const [minEdge, setMinEdge] = useState(0);
  const [filterTradable, setFilterTradable] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [clockET, setClockET] = useState("");

  // ── Queries ──────────────────────────────────────────────────────────────
  const statsQ = useQuery<Stats>({ queryKey: ["/api/stats"], refetchInterval: 30000 });
  const marketsQ = useQuery<Market[]>({ queryKey: ["/api/markets"], refetchInterval: 60000 });
  const signalsQ = useQuery<Signal[]>({ queryKey: ["/api/signals"], refetchInterval: 30000 });
  const tradesQ = useQuery<Trade[]>({ queryKey: ["/api/trades"], refetchInterval: 30000 });
  const logQ = useQuery<{ log: string }>({ queryKey: ["/api/log"], refetchInterval: 15000 });
  const themePerfQ = useQuery<any[]>({ queryKey: ["/api/theme-performance"], refetchInterval: 30000 });
  const priceHistoryQ = useQuery<PricePoint[]>({
    queryKey: ["/api/price-history", selectedMarket?.id, selectedOutcome],
    queryFn: async () => {
      if (!selectedMarket) return [];
      const r = await fetch(`/api/price-history?market_id=${encodeURIComponent(selectedMarket.id)}&outcome=${encodeURIComponent(selectedOutcome)}`);
      return r.json();
    },
    enabled: !!selectedMarket,
    refetchInterval: 60000,
  });

  useEffect(() => {
    const i = setInterval(() => setLastRefresh(new Date()), 30000);
    // Live ET clock — updates every second
    const tickET = () => {
      const s = new Date().toLocaleTimeString("en-US", {
        timeZone: "America/New_York",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
      setClockET(s);
    };
    tickET();
    const clockI = setInterval(tickET, 1000);
    return () => { clearInterval(i); clearInterval(clockI); };
  }, []);

  const stats = statsQ.data;
  const markets = marketsQ.data || [];
  const signals = signalsQ.data || [];
  const trades = tradesQ.data || [];

  // ── Filtered data ─────────────────────────────────────────────────────────
  const filteredMarkets = markets.filter(m =>
    m.question.toLowerCase().includes(marketFilter.toLowerCase())
  );
  const filteredSignals = signals.filter(s => {
    if (signalFilter === "unexplained" && s.explained !== 0) return false;
    if (signalFilter === "explained" && s.explained === 0) return false;
    if (s.edge_score < minEdge) return false;
    if (filterTradable && s.trade_eligible === 0) return false;
    return true;
  });
  const openTrades = trades.filter(t => t.status === "OPEN");

  // ── Performance data ──────────────────────────────────────────────────────
  // Build cumulative P&L from CLOSED trades using realized_pnl
  const closedSorted = trades
    .filter(t => t.status === "CLOSED" && t.realized_pnl !== "")
    .sort((a, b) => (a.close_date || a.timestamp).localeCompare(b.close_date || b.timestamp));
  const cumulativePnl = closedSorted.reduce((acc, t, i) => {
    const prev = acc[i - 1]?.cumPnl || 0;
    const rpnl = t.realized_pnl !== "" ? Number(t.realized_pnl) : 0;
    acc.push({ ...t, cumPnl: prev + rpnl, label: fmtTime(t.close_date || t.timestamp) });
    return acc;
  }, [] as any[]);

  const scatterData = closedSorted.map(t => ({
    edge_score: t.edge_score,
    pnl: t.realized_pnl !== "" ? Number(t.realized_pnl) : 0,
    symbol: t.symbol
  }));

  // ── Top signals for performance tab ──────────────────────────────────────
  const topSignals = [...signals].sort((a, b) => b.edge_score - a.edge_score).slice(0, 10);

  return (
    <div className="flex flex-col h-screen bg-background overflow-hidden">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-border bg-card/50 shrink-0">
        <div className="flex items-center gap-3">
          <Logo />
          <div>
            <h1 className="text-sm font-bold text-foreground leading-none">Polymarket Trader</h1>
            <p className="text-xs text-muted-foreground mt-0.5">Simulated Paper Trading · No Broker</p>
          </div>
          <span className="ml-2 hidden sm:block"><LiveDot /></span>
        </div>

        {/* Stats strip */}
        <div className="hidden md:flex items-center gap-5 text-xs">
          <span className="text-muted-foreground">
            Markets: <span className="text-foreground font-semibold tabular">{stats?.total_markets ?? "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Signals 24h: <span className="text-amber-400 font-semibold tabular">{stats?.signals_24h ?? "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Edges: <span className="text-amber-400 font-semibold tabular">{stats?.unexplained_edges ?? "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Open: <span className="text-foreground font-semibold tabular">{stats?.open_trades ?? "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Unreal: <span className={clsPnl(stats?.total_pnl || 0)}>{stats ? fmt$(stats.total_pnl) : "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Realized: <span className={clsPnl(stats?.realized_pnl || 0)}>{stats ? fmt$(stats.realized_pnl ?? 0) : "—"}</span>
          </span>
          <span className="text-muted-foreground">
            Equity: <span className="text-cyan-400 font-bold tabular">{stats ? fmt$(stats.account_equity ?? 1000) : "—"}</span>
          </span>
        </div>

        <div className="text-xs text-muted-foreground tabular">
          {clockET} ET
        </div>
      </header>

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <nav className="flex items-center gap-0.5 px-4 border-b border-border bg-card/30 shrink-0">
        {TABS.map(t => (
          <button
            key={t.id}
            data-testid={`tab-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px
              ${tab === t.id
                ? "border-cyan-400 text-cyan-400"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"}`}
          >
            <span className="text-base leading-none">{t.icon}</span>
            <span>{t.label}</span>
            {t.id === "signals" && stats?.unexplained_edges ? (
              <span className="badge-edge text-xs px-1.5 py-0.5 rounded-full font-bold ml-1">
                {stats.unexplained_edges}
              </span>
            ) : null}
            {t.id === "trades" && openTrades.length > 0 ? (
              <span className="bg-green-500/20 text-green-400 text-xs px-1.5 py-0.5 rounded-full font-bold ml-1">
                {openTrades.length}
              </span>
            ) : null}
          </button>
        ))}
      </nav>

      {/* ── Main content ────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto overscroll-contain p-4 md:p-5">

        {/* ══ MARKETS TAB ══════════════════════════════════════════════════ */}
        {tab === "markets" && (
          <div className="flex flex-col gap-4">

            {/* KPI row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="Active Markets" value={stats?.total_markets ?? "—"} />
              <StatCard label="Signals (24h)" value={stats?.signals_24h ?? "—"} accent="text-amber-400" />
              <StatCard label="Unexplained Edges" value={stats?.unexplained_edges ?? "—"} accent="text-amber-400" />
              <StatCard label="Top Edge Score" value={stats?.top_edge_score?.toFixed(2) ?? "—"} accent="text-cyan-400" />
            </div>

            {/* Filter bar */}
            <div className="flex items-center gap-3">
              <input
                data-testid="input-market-filter"
                type="text"
                value={marketFilter}
                onChange={e => setMarketFilter(e.target.value)}
                placeholder="Filter markets..."
                className="flex-1 max-w-xs bg-muted text-sm text-foreground border border-border rounded-md px-3 py-1.5 placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-cyan-400"
              />
              <span className="text-xs text-muted-foreground">{filteredMarkets.length} markets</span>
            </div>

            {/* Markets table */}
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="overflow-x-auto">
                <table className="data-table w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground uppercase tracking-wide border-b border-border">
                      <th className="px-3 py-2.5 text-left font-medium">Market</th>
                      <th className="px-3 py-2.5 text-left font-medium">Category</th>
                      <th className="px-3 py-2.5 text-right font-medium">Yes %</th>
                      <th className="px-3 py-2.5 text-right font-medium">No %</th>
                      <th className="px-3 py-2.5 text-right font-medium">24h Δ</th>
                      <th className="px-3 py-2.5 text-right font-medium">Volume</th>
                      <th className="px-3 py-2.5 text-center font-medium">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {marketsQ.isLoading ? (
                      Array.from({ length: 10 }).map((_, i) => (
                        <tr key={i} className="border-b border-border/50">
                          {Array.from({ length: 7 }).map((_, j) => (
                            <td key={j} className="px-3 py-2.5">
                              <div className="h-4 bg-muted rounded animate-pulse" />
                            </td>
                          ))}
                        </tr>
                      ))
                    ) : filteredMarkets.length === 0 ? (
                      <tr><td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">No markets yet — data loads every 15 min</td></tr>
                    ) : filteredMarkets.slice(0, 80).map(m => {
                      const yes = m.outcomes.find(o => o.outcome_name === "Yes");
                      const no = m.outcomes.find(o => o.outcome_name === "No");
                      return (
                        <tr
                          key={m.id}
                          data-testid={`row-market-${m.id.slice(0, 8)}`}
                          className="border-b border-border/40 cursor-pointer transition-colors hover:bg-muted/30"
                          onClick={() => { setSelectedMarket(m); setSelectedOutcome("Yes"); }}
                        >
                          <td className="px-3 py-2.5 max-w-xs">
                            <span className="block truncate text-foreground" title={m.question}>{m.question}</span>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{m.category || "Other"}</span>
                          </td>
                          <td className="px-3 py-2.5 text-right tabular text-foreground">
                            {yes ? fmtPct(yes.current_price * 100) : "—"}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular text-muted-foreground">
                            {no ? fmtPct(no.current_price * 100) : "—"}
                          </td>
                          <td className={`px-3 py-2.5 text-right tabular font-medium ${clsChange(m.change_pp)}`}>
                            {m.change_pp > 0 ? `+${m.change_pp.toFixed(1)}pp` : m.change_pp === 0 ? "—" : `${m.change_pp.toFixed(1)}pp`}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular text-muted-foreground">
                            ${(m.volume_usd / 1e6).toFixed(1)}M
                          </td>
                          <td className="px-3 py-2.5 text-center">
                            {m.is_edge ? (
                              <span className="badge-edge text-xs px-2 py-0.5 rounded-full font-bold">EDGE</span>
                            ) : (
                              <span className="text-muted-foreground text-xs">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Price chart for selected market */}
            {selectedMarket && (
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <p className="text-sm font-medium text-foreground line-clamp-1">{selectedMarket.question}</p>
                    <div className="flex gap-2 mt-1">
                      {selectedMarket.outcomes.map(o => (
                        <button
                          key={o.outcome_name}
                          onClick={() => setSelectedOutcome(o.outcome_name)}
                          className={`text-xs px-2 py-0.5 rounded-full border transition-colors
                            ${selectedOutcome === o.outcome_name
                              ? "border-cyan-400 text-cyan-400 bg-cyan-400/10"
                              : "border-border text-muted-foreground hover:border-slate-500"}`}
                        >
                          {o.outcome_name} — {fmtPct(o.current_price * 100)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button onClick={() => setSelectedMarket(null)} className="text-muted-foreground hover:text-foreground text-lg">×</button>
                </div>
                {priceHistoryQ.isLoading ? (
                  <div className="h-40 flex items-center justify-center text-muted-foreground text-sm">Loading chart…</div>
                ) : (priceHistoryQ.data?.length || 0) < 2 ? (
                  <div className="h-40 flex items-center justify-center text-muted-foreground text-sm">Not enough history yet — check back after more cycles</div>
                ) : (
                  <ResponsiveContainer width="100%" height={160}>
                    <AreaChart data={priceHistoryQ.data}>
                      <defs>
                        <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#00d4ff" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#00d4ff" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(222 15% 18%)" />
                      <XAxis dataKey="timestamp" tick={{ fill: "#64748b", fontSize: 10 }} tickFormatter={v => v?.slice(11, 16)} />
                      <YAxis tick={{ fill: "#64748b", fontSize: 10 }} domain={["auto", "auto"]} tickFormatter={v => `${v.toFixed(0)}%`} />
                      <Tooltip
                        contentStyle={{ background: "hsl(222 18% 13%)", border: "1px solid hsl(222 15% 22%)", borderRadius: 6, color: "#e2e8f0" }}
                        formatter={(v: any) => [`${Number(v).toFixed(1)}%`, "Probability"]}
                        labelFormatter={l => l?.slice(5, 16) || l}
                      />
                      <Area type="monotone" dataKey="price" stroke="#00d4ff" strokeWidth={2} fill="url(#priceGrad)" dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            )}
          </div>
        )}

        {/* ══ SIGNALS TAB ══════════════════════════════════════════════════ */}
        {tab === "signals" && (
          <div className="flex flex-col gap-4">

            {/* Filter bar */}
            <div className="flex flex-wrap items-center gap-3">
              {(["all","unexplained","explained"] as const).map(f => (
                <button
                  key={f}
                  data-testid={`filter-signal-${f}`}
                  onClick={() => setSignalFilter(f)}
                  className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors
                    ${signalFilter === f
                      ? f === "unexplained" ? "border-amber-400 text-amber-400 bg-amber-400/10"
                        : f === "explained" ? "border-green-400 text-green-400 bg-green-400/10"
                        : "border-cyan-400 text-cyan-400 bg-cyan-400/10"
                      : "border-border text-muted-foreground hover:border-slate-500"}`}
                >
                  {f === "all" ? "All" : f === "unexplained" ? "⚡ Unexplained Edge" : "✅ Explained"}
                </button>
              ))}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Min edge:</span>
                <input
                  type="range" min="0" max="10" step="0.5" value={minEdge}
                  onChange={e => setMinEdge(parseFloat(e.target.value))}
                  className="w-24 accent-cyan-400"
                />
                <span className="text-xs text-cyan-400 tabular w-6">{minEdge}</span>
              </div>
              <button
                  onClick={() => setFilterTradable(f => !f)}
                  className={`text-xs px-3 py-1 rounded-full border transition-colors ${filterTradable ? "bg-cyan-500/20 text-cyan-400 border-cyan-500/40" : "border-border text-muted-foreground hover:text-foreground"}`}
                  title="Show only economically tradable signals (hides sports, celebrity, novelty)"
                >
                  {filterTradable ? "✓ " : ""}Tradable only
                </button>
                <span className="text-xs text-muted-foreground ml-auto">{filteredSignals.length} signals</span>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="Total Signals" value={signals.length} />
              <StatCard label="Unexplained" value={signals.filter(s => s.explained === 0).length} accent="text-amber-400" />
              <StatCard label="Avg Change" value={signals.length ? `${(signals.reduce((s, x) => s + Math.abs(x.change_pp), 0) / signals.length).toFixed(1)}pp` : "—"} />
              <StatCard label="Max Edge Score" value={signals.length ? Math.max(...signals.map(s => s.edge_score)).toFixed(2) : "—"} accent="text-cyan-400" />
            </div>

            {/* Signals table */}
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="overflow-x-auto">
                <table className="data-table w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground uppercase tracking-wide border-b border-border">
                      <th className="px-3 py-2.5 text-left font-medium">Time</th>
                      <th className="px-3 py-2.5 text-left font-medium">Market</th>
                      <th className="px-3 py-2.5 text-left font-medium">Outcome</th>
                      <th className="px-3 py-2.5 text-right font-medium">Old %</th>
                      <th className="px-3 py-2.5 text-right font-medium">New %</th>
                      <th className="px-3 py-2.5 text-right font-medium">Δ (pp)</th>
                      <th className="px-3 py-2.5 text-center font-medium">Instrument</th>
                      <th className="px-3 py-2.5 text-center font-medium">Theme</th>
                      <th className="px-3 py-2.5 text-right font-medium">Edge</th>
                      <th className="px-3 py-2.5 text-center font-medium">Eligible</th>
                    </tr>
                  </thead>
                  <tbody>
                    {signalsQ.isLoading ? (
                      Array.from({ length: 8 }).map((_, i) => (
                        <tr key={i} className="border-b border-border/50">
                          {Array.from({ length: 10 }).map((_, j) => (
                            <td key={j} className="px-3 py-2.5"><div className="h-4 bg-muted rounded animate-pulse"/></td>
                          ))}
                        </tr>
                      ))
                    ) : filteredSignals.length === 0 ? (
                      <tr><td colSpan={10} className="px-3 py-8 text-center text-muted-foreground">No signals matching filters</td></tr>
                    ) : filteredSignals.slice(0, 100).map((s, i) => (
                      <tr key={i} data-testid={`row-signal-${i}`} className="border-b border-border/40 group">
                        <td className="px-3 py-2.5 tabular text-muted-foreground text-xs whitespace-nowrap">{fmtTime(s.timestamp)}</td>
                        <td className="px-3 py-2.5 max-w-xs">
                          <span className="block truncate text-foreground text-xs" title={s.market_name}>{s.market_name}</span>
                        </td>
                        <td className="px-3 py-2.5 text-xs text-muted-foreground">{s.outcome}</td>
                        <td className="px-3 py-2.5 text-right tabular text-muted-foreground text-xs">{fmtPct(s.old_prob)}</td>
                        <td className="px-3 py-2.5 text-right tabular text-xs font-medium text-foreground">{fmtPct(s.new_prob)}</td>
                        <td className={`px-3 py-2.5 text-right tabular text-xs font-bold ${clsChange(s.change_pp)}`}>
                          {fmtChange(s.change_pp)}
                        </td>
                        <td className="px-3 py-2.5 text-center">
                          <span className="text-xs font-bold text-cyan-400 tabular">{s.correlated_instrument}</span>
                        </td>
                        <td className="px-3 py-2.5 text-center">
                          <span className="text-xs px-1.5 py-0.5 rounded text-slate-300 bg-slate-700/50 border border-slate-600 truncate max-w-[90px] block text-center" title={s.theme}>{(s.theme || "—").replace(/_/g, " ")}</span>
                        </td>
                        <td className="px-3 py-2.5 text-right tabular font-bold text-amber-400 text-xs">{s.edge_score.toFixed(2)}</td>
                        <td className="px-3 py-2.5 text-center">
                          {s.trade_eligible === 0 ? (
                            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-slate-800/80 text-slate-500 border border-slate-700" title="Sports / celebrity / novelty — not tradable">NOISE</span>
                          ) : s.news_check_method === "unverified" || (!s.news_check_method && s.explained === 0) ? (
                            <span className="badge-edge text-xs px-2 py-0.5 rounded-full">EDGE</span>
                          ) : s.explained === 0 ? (
                            <span className="badge-edge text-xs px-2 py-0.5 rounded-full">EDGE</span>
                          ) : (
                            <span className="badge-explained text-xs px-2 py-0.5 rounded-full">OK</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* News summary for top signal */}
            {/* News checker note — always visible */}
            <div className="rounded-lg border border-slate-700 bg-slate-800/40 p-3">
              <p className="text-xs text-muted-foreground leading-relaxed">
                <span className="text-slate-300 font-medium">News checker: </span>
                Uses public, unauthenticated sources (DuckDuckGo Instant Answer, public RSS feeds). No API key required.
                {" "}If no source is reachable, signals are tagged <span className="text-slate-400 font-medium">UNVERIF</span> — unverified but not suppressed.
                {" "}Explained/unexplained classification is heuristic and may be imprecise.
              </p>
            </div>

            {filteredSignals[0]?.news_summary && (
              <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-4">
                <div className="flex items-center gap-2 mb-1">
                  <p className="text-xs font-semibold text-amber-400">Top Signal — News Context</p>
                  {filteredSignals[0].news_check_method && (
                    <span className="text-xs text-muted-foreground">via {filteredSignals[0].news_check_method}</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">{filteredSignals[0].market_name}</p>
                <p className="text-xs text-foreground mt-2 leading-relaxed">{filteredSignals[0].news_summary}</p>
              </div>
            )}
          </div>
        )}

        {/* ══ TRADES TAB ═══════════════════════════════════════════════════ */}
        {tab === "trades" && (
          <div className="flex flex-col gap-4">

            {(() => {
              const closedTrades = trades.filter(t => t.status === "CLOSED");
              const realizedSum  = closedTrades.reduce((s, t) => s + (t.realized_pnl !== "" ? Number(t.realized_pnl) : 0), 0);
              const unrealSum    = openTrades.reduce((s, t) => s + t.unrealized_pnl, 0);
              const equity       = 1000 + realizedSum;
              return (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard label="Account Equity" value={fmt$(equity)} accent={equity >= 1000 ? "text-cyan-400" : "text-red-400"} sub="Base $1,000" />
                  <StatCard label="Realized P&L" value={fmt$(realizedSum)} accent={realizedSum >= 0 ? "text-green-400" : "text-red-400"} sub={`${closedTrades.length} closed`} />
                  <StatCard label="Unrealized P&L" value={fmt$(unrealSum)} accent={unrealSum >= 0 ? "text-green-400" : "text-red-400"} sub={`${openTrades.length} open`} />
                  <StatCard label="Avg Edge at Entry" value={trades.length ? (trades.reduce((s, t) => s + t.edge_score, 0) / trades.length).toFixed(2) : "—"} accent="text-amber-400" />
                </div>
              );
            })()}

            {/* Open positions */}
            {openTrades.length > 0 && (
              <>
                <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-green-400 pulse-dot inline-block"/>
                  Open Positions
                </h3>
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                  {openTrades.map((t, i) => (
                    <div key={i} data-testid={`card-trade-${i}`} className="rounded-lg border border-green-500/20 bg-green-500/5 p-4 glow-green">
                      <div className="flex items-start justify-between mb-2">
                        <div>
                          <span className="text-base font-bold text-foreground tabular">{t.symbol}</span>
                          <span className={`ml-2 text-xs px-1.5 py-0.5 rounded font-bold ${t.side === "BUY" ? "badge-buy" : "badge-sell"}`}>
                            {t.side}
                          </span>
                          <span className="ml-1.5 text-xs px-1.5 py-0.5 rounded font-medium bg-slate-700/60 text-slate-400 border border-slate-600 uppercase tracking-wide"
                            title="Simulated fill — no broker connection">
                            SIM
                          </span>
                        </div>
                        <span className={`text-sm font-bold tabular ${clsPnl(t.unrealized_pnl)}`}>
                          {fmt$(t.unrealized_pnl)}
                        </span>
                      </div>
                      <div className="space-y-1 text-xs text-muted-foreground">
                        <div className="flex justify-between">
                          <span>Entry</span><span className="tabular text-foreground">{fmt$(t.entry_price)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Mark</span><span className="tabular text-foreground">{fmt$(t.mark_price)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Qty</span><span className="tabular text-foreground">{typeof t.quantity === "number" ? t.quantity.toFixed(4) : t.quantity}</span>
                        </div>
                        {t.stop_loss !== "" && (
                          <div className="flex justify-between">
                            <span className="text-red-400">Stop Loss</span>
                            <span className="tabular text-red-400 font-medium">{fmt$(Number(t.stop_loss))}</span>
                          </div>
                        )}
                        {t.take_profit !== "" && (
                          <div className="flex justify-between">
                            <span className="text-green-400">Take Profit</span>
                            <span className="tabular text-green-400 font-medium">{fmt$(Number(t.take_profit))}</span>
                          </div>
                        )}
                        <div className="flex justify-between">
                          <span>Edge Score</span><span className="tabular text-amber-400 font-medium">{t.edge_score.toFixed(2)}</span>
                        </div>
                        {t.trade_id && (
                          <div className="flex justify-between">
                            <span>Trade ID</span><span className="tabular text-muted-foreground font-mono">{t.trade_id}</span>
                          </div>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-2 line-clamp-1" title={t.market_name}>{t.market_name}</p>
                    </div>
                  ))}
                </div>
              </>
            )}

            {/* All trades table */}
            <h3 className="text-sm font-semibold text-foreground">All Trades</h3>
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="overflow-x-auto">
                <table className="data-table w-full text-sm">
                  <thead>
                    <tr className="text-xs text-muted-foreground uppercase tracking-wide border-b border-border">
                      <th className="px-3 py-2.5 text-left font-medium">Time</th>
                      <th className="px-3 py-2.5 text-left font-medium">Symbol</th>
                      <th className="px-3 py-2.5 text-center font-medium">Side</th>
                      <th className="px-3 py-2.5 text-right font-medium">Qty</th>
                      <th className="px-3 py-2.5 text-right font-medium">Entry</th>
                      <th className="px-3 py-2.5 text-right font-medium">SL</th>
                      <th className="px-3 py-2.5 text-right font-medium">TP</th>
                      <th className="px-3 py-2.5 text-right font-medium">Mark</th>
                      <th className="px-3 py-2.5 text-right font-medium">P&L</th>
                      <th className="px-3 py-2.5 text-right font-medium">Edge</th>
                      <th className="px-3 py-2.5 text-center font-medium">Exit</th>
                      <th className="px-3 py-2.5 text-center font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradesQ.isLoading ? (
                      Array.from({ length: 5 }).map((_, i) => (
                        <tr key={i} className="border-b border-border/50">
                          {Array.from({length:12}).map((_,j)=>(
                            <td key={j} className="px-3 py-2.5"><div className="h-4 bg-muted rounded animate-pulse"/></td>
                          ))}
                        </tr>
                      ))
                    ) : trades.length === 0 ? (
                      <tr>
                        <td colSpan={12} className="px-3 py-10 text-center">
                          <p className="text-muted-foreground text-sm">No trades yet</p>
                          <p className="text-xs text-muted-foreground mt-1">Simulated fills execute when a high-confidence unexplained edge is detected</p>
                        </td>
                      </tr>
                    ) : trades.map((t, i) => (
                      <tr key={i} className={`border-b border-border/40 ${t.status === "CLOSED" ? "opacity-70" : ""}`}>
                        <td className="px-3 py-2.5 text-xs tabular text-muted-foreground whitespace-nowrap" title={t.trade_id ? `ID: ${t.trade_id}` : undefined}>{fmtTime(t.timestamp)}</td>
                        <td className="px-3 py-2.5 font-bold text-foreground">{t.symbol}</td>
                        <td className="px-3 py-2.5 text-center">
                          <span className={`text-xs px-2 py-0.5 rounded font-bold ${t.side === "BUY" ? "badge-buy" : "badge-sell"}`}>{t.side}</span>
                        </td>
                        <td className="px-3 py-2.5 text-right tabular text-muted-foreground">
                          {typeof t.quantity === "number" ? t.quantity.toFixed(4) : t.quantity}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular text-foreground">{fmt$(t.entry_price)}</td>
                        <td className="px-3 py-2.5 text-right tabular">
                          {t.stop_loss !== ""
                            ? <span className="text-red-400">{fmt$(Number(t.stop_loss))}</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular">
                          {t.take_profit !== ""
                            ? <span className="text-green-400">{fmt$(Number(t.take_profit))}</span>
                            : <span className="text-muted-foreground">—</span>}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular text-muted-foreground">{fmt$(t.mark_price)}</td>
                        <td className={`px-3 py-2.5 text-right tabular font-bold ${clsPnl(
                          t.status === "CLOSED" && t.realized_pnl !== ""
                            ? Number(t.realized_pnl)
                            : t.unrealized_pnl
                        )}`}>
                          {t.status === "CLOSED" && t.realized_pnl !== ""
                            ? fmt$(Number(t.realized_pnl))
                            : fmt$(t.unrealized_pnl)}
                        </td>
                        <td className="px-3 py-2.5 text-right tabular text-amber-400 font-medium">{t.edge_score.toFixed(2)}</td>
                        <td className="px-3 py-2.5 text-center">
                          {t.close_reason ? (
                            <span className={`text-xs px-2 py-0.5 rounded font-bold ${
                              t.close_reason === "TP_HIT" ? "text-green-400 bg-green-400/10" :
                              t.close_reason === "SL_HIT" ? "text-red-400 bg-red-400/10" :
                              "text-slate-400 bg-slate-700/40"
                            }`}>{t.close_reason}</span>
                          ) : (
                            <span className="text-muted-foreground text-xs">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-center">
                          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            t.status === "OPEN"
                              ? "text-green-400 bg-green-400/10"
                              : "text-slate-400 bg-slate-700/40"
                          }`}>
                            {t.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* ══ PERFORMANCE TAB ══════════════════════════════════════════════ */}
        {tab === "performance" && (
          <div className="flex flex-col gap-4">

{(() => {
              const closedTrades = trades.filter(t => t.status === "CLOSED");
              const realizedPnls = closedTrades.map(t => t.realized_pnl !== "" ? Number(t.realized_pnl) : 0);
              const realizedSum  = realizedPnls.reduce((s, v) => s + v, 0);
              const winners      = realizedPnls.filter(v => v > 0).length;
              const winRate      = closedTrades.length ? (winners / closedTrades.length * 100).toFixed(0) + "%" : "—";
              const best         = realizedPnls.length ? Math.max(...realizedPnls) : null;
              const worst        = realizedPnls.length ? Math.min(...realizedPnls) : null;
              return (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <StatCard label="Realized P&L" value={fmt$(realizedSum)} accent={realizedSum >= 0 ? "text-green-400" : "text-red-400"} sub={`${closedTrades.length} closed trades`} />
                  <StatCard label="Win Rate" value={winRate} sub={`${winners}/${closedTrades.length} wins`} />
                  <StatCard label="Best Trade" value={best !== null ? fmt$(best) : "—"} accent="text-green-400" />
                  <StatCard label="Worst Trade" value={worst !== null ? fmt$(worst) : "—"} accent="text-red-400" />
                </div>
              );
            })()}

            <div className="grid md:grid-cols-2 gap-4">
              {/* Cumulative P&L */}
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Cumulative P&L</p>
                {cumulativePnl.length < 2 ? (
                  <div className="h-48 flex flex-col items-center justify-center gap-2">
                    <p className="text-muted-foreground text-sm">No trade data yet</p>
                    {/* Signal activity chart instead */}
                    {signals.length > 1 && (() => {
                      const byHour: Record<string, number> = {};
                      signals.forEach(s => {
                        const h = s.timestamp?.slice(0, 13) || "?";
                        byHour[h] = (byHour[h] || 0) + 1;
                      });
                      const chartData = Object.entries(byHour).slice(-24).map(([h, c]) => ({ hour: h.slice(11), count: c }));
                      return (
                        <div className="w-full">
                          <p className="text-xs text-muted-foreground text-center mb-2">Signal activity by hour</p>
                          <ResponsiveContainer width="100%" height={140}>
                            <BarChart data={chartData}>
                              <CartesianGrid strokeDasharray="3 3" stroke="hsl(222 15% 18%)" />
                              <XAxis dataKey="hour" tick={{ fill: "#64748b", fontSize: 9 }} />
                              <YAxis tick={{ fill: "#64748b", fontSize: 9 }} />
                              <Tooltip contentStyle={{ background: "hsl(222 18% 13%)", border: "1px solid hsl(222 15% 22%)", borderRadius: 6, color: "#e2e8f0" }} />
                              <Bar dataKey="count" fill="#f59e0b" radius={[2,2,0,0]} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      );
                    })()}
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <AreaChart data={cumulativePnl}>
                      <defs>
                        <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#22c55e" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(222 15% 18%)" />
                      <XAxis dataKey="label" tick={{ fill: "#64748b", fontSize: 10 }} />
                      <YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickFormatter={v => `$${v}`} />
                      <ReferenceLine y={0} stroke="hsl(222 15% 30%)" strokeDasharray="4 4" />
                      <Tooltip contentStyle={{ background: "hsl(222 18% 13%)", border: "1px solid hsl(222 15% 22%)", borderRadius: 6, color: "#e2e8f0" }} formatter={(v:any)=>[`$${Number(v).toFixed(2)}`, "Cum. P&L"]} />
                      <Area type="monotone" dataKey="cumPnl" stroke="#22c55e" strokeWidth={2} fill="url(#pnlGrad)" dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>

              {/* Edge Score vs P&L */}
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Edge Score vs P&L</p>
                {scatterData.length < 2 ? (
                  <div className="h-48 flex flex-col items-center justify-center gap-2">
                    <p className="text-muted-foreground text-sm">Waiting for trades</p>
                    {/* Top signals chart */}
                    {topSignals.length > 0 && (
                      <div className="w-full">
                        <p className="text-xs text-muted-foreground text-center mb-2">Top edge scores</p>
                        <ResponsiveContainer width="100%" height={140}>
                          <BarChart data={topSignals} layout="vertical">
                            <CartesianGrid strokeDasharray="3 3" stroke="hsl(222 15% 18%)" horizontal={false} />
                            <XAxis type="number" tick={{ fill: "#64748b", fontSize: 9 }} />
                            <YAxis type="category" dataKey="correlated_instrument" tick={{ fill: "#94a3b8", fontSize: 9 }} width={35} />
                            <Tooltip contentStyle={{ background: "hsl(222 18% 13%)", border: "1px solid hsl(222 15% 22%)", borderRadius: 6, color: "#e2e8f0" }} />
                            <Bar dataKey="edge_score" fill="#f59e0b" radius={[0,2,2,0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    )}
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <ScatterChart>
                      <CartesianGrid strokeDasharray="3 3" stroke="hsl(222 15% 18%)" />
                      <XAxis dataKey="edge_score" name="Edge Score" tick={{ fill: "#64748b", fontSize: 10 }} />
                      <YAxis dataKey="pnl" name="P&L ($)" tick={{ fill: "#64748b", fontSize: 10 }} tickFormatter={v => `$${v}`} />
                      <ReferenceLine y={0} stroke="hsl(222 15% 30%)" strokeDasharray="4 4" />
                      <Tooltip
                        cursor={{ strokeDasharray: "3 3", stroke: "#475569" }}
                        contentStyle={{ background: "hsl(222 18% 13%)", border: "1px solid hsl(222 15% 22%)", borderRadius: 6, color: "#e2e8f0" }}
                        formatter={(v:any, name:string) => [name === "pnl" ? `$${Number(v).toFixed(2)}` : v, name === "pnl" ? "P&L" : "Edge Score"]}
                      />
                      <Scatter data={scatterData} fill="#00d4ff" opacity={0.8} />
                    </ScatterChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            {/* Theme Performance Breakdown */}
            <div className="rounded-lg border border-border bg-card p-4">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Theme Performance Breakdown</p>
              {themePerfQ.isLoading ? (
                <div className="flex items-center justify-center h-16 text-muted-foreground text-sm">Loading…</div>
              ) : !themePerfQ.data || themePerfQ.data.length === 0 ? (
                <div className="flex items-center justify-center h-16 text-muted-foreground text-sm">No data yet — themes will appear as signals are generated</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="data-table w-full text-xs">
                    <thead>
                      <tr className="text-xs text-muted-foreground uppercase tracking-wide border-b border-border">
                        <th className="px-3 py-2 text-left font-medium">Theme</th>
                        <th className="px-3 py-2 text-right font-medium">Signals 24h</th>
                        <th className="px-3 py-2 text-right font-medium">Trades</th>
                        <th className="px-3 py-2 text-right font-medium">Open</th>
                        <th className="px-3 py-2 text-right font-medium">Realized P&L</th>
                        <th className="px-3 py-2 text-right font-medium">Unrealized</th>
                        <th className="px-3 py-2 text-right font-medium">Win Rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {themePerfQ.data.map((t: any, i: number) => (
                        <tr key={i} className="border-b border-border/40">
                          <td className="px-3 py-2 text-foreground font-medium">
                            <span className="px-1.5 py-0.5 rounded text-xs bg-slate-700/50 border border-slate-600">
                              {(t.theme || "—").replace(/_/g, " ")}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right tabular text-amber-400">{t.signals_24h}</td>
                          <td className="px-3 py-2 text-right tabular text-muted-foreground">{t.total_trades}</td>
                          <td className="px-3 py-2 text-right tabular text-cyan-400">{t.open_trades}</td>
                          <td className={`px-3 py-2 text-right tabular font-bold ${Number(t.realized_pnl) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {Number(t.realized_pnl) >= 0 ? "+" : ""}{Number(t.realized_pnl).toFixed(2)}
                          </td>
                          <td className={`px-3 py-2 text-right tabular ${Number(t.unrealized_pnl) >= 0 ? "text-green-300/80" : "text-red-300/80"}`}>
                            {Number(t.unrealized_pnl) >= 0 ? "+" : ""}{Number(t.unrealized_pnl).toFixed(2)}
                          </td>
                          <td className="px-3 py-2 text-right tabular text-muted-foreground">
                            {t.win_rate !== null ? `${t.win_rate}%` : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* System log */}
            <div className="rounded-lg border border-border bg-card p-4">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">System Log</p>
              <pre className="text-xs text-muted-foreground font-mono leading-relaxed overflow-auto max-h-48 overscroll-contain whitespace-pre-wrap break-all">
                {logQ.data?.log || "Loading system log…"}
              </pre>
            </div>
          </div>
        )}
      </main>

      {/* ── Footer ──────────────────────────────────────────────────────── */}
      <footer className="shrink-0 border-t border-border px-5 py-2 flex items-center justify-between bg-card/30">
        <span className="text-xs text-muted-foreground">
          15 min (market hours ET 07:30–18:00) · 60 min off-hours · Simulated paper trades · {DB_PATH_DISPLAY}
        </span>
        <span className="text-xs text-muted-foreground">Hetzner VPS deployment</span>
      </footer>
    </div>
  );
}

const DB_PATH_DISPLAY = "markets.db";
