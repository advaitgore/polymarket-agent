"""
Polymarket Trading System Dashboard — Streamlit app.
Reads from: markets.db, signals.csv, trades.csv
Four tabs: Live Markets | Signals | Trades | Performance
"""
import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import json
from datetime import datetime, timezone, timedelta

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "markets.db")
SIGNALS_CSV = os.path.join(BASE_DIR, "data", "signals.csv")
TRADES_CSV = os.path.join(BASE_DIR, "data", "trades.csv")
LOG_FILE = os.path.join(BASE_DIR, "logs", "system.log")

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Polymarket Trading System",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border: 1px solid #0f3460;
    border-radius: 10px;
    padding: 15px;
    text-align: center;
}
.edge-badge-unexplained {
    background-color: #ff4b4b;
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 12px;
    font-weight: bold;
}
.edge-badge-explained {
    background-color: #2e7d32;
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 12px;
}
.status-open { color: #4CAF50; font-weight: bold; }
.status-closed { color: #9E9E9E; }
</style>
""", unsafe_allow_html=True)

# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_markets():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            m.id, m.question, m.category, m.volume_usd,
            o.outcome_name, o.current_price, o.last_updated
        FROM markets m
        JOIN outcomes o ON m.id = o.market_id
        WHERE m.active = 1
        ORDER BY m.volume_usd DESC
    """, conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def load_price_history(market_id: str, outcome: str):
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT price, timestamp
        FROM price_history
        WHERE market_id = ? AND outcome_name = ?
        ORDER BY timestamp ASC
    """, conn, params=(market_id, outcome))
    conn.close()
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["price_pct"] = df["price"] * 100
    return df

@st.cache_data(ttl=30)
def load_signals():
    if not os.path.exists(SIGNALS_CSV):
        return pd.DataFrame()
    df = pd.read_csv(SIGNALS_CSV)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["change_pp"] = pd.to_numeric(df["change_pp"], errors="coerce")
    df["edge_score"] = pd.to_numeric(df["edge_score"], errors="coerce")
    df["new_prob"] = pd.to_numeric(df["new_prob"], errors="coerce")
    df["old_prob"] = pd.to_numeric(df["old_prob"], errors="coerce")
    df["explained"] = df["explained"].astype(int)
    return df.sort_values("timestamp", ascending=False)

@st.cache_data(ttl=30)
def load_trades():
    if not os.path.exists(TRADES_CSV):
        return pd.DataFrame()
    df = pd.read_csv(TRADES_CSV)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    df["mark_price"] = pd.to_numeric(df["mark_price"], errors="coerce")
    df["unrealized_pnl"] = pd.to_numeric(df["unrealized_pnl"], errors="coerce")
    df["edge_score"] = pd.to_numeric(df["edge_score"], errors="coerce")
    return df.sort_values("timestamp", ascending=False)

def load_log_tail(n_lines: int = 50):
    if not os.path.exists(LOG_FILE):
        return "No log file found yet."
    with open(LOG_FILE) as f:
        lines = f.readlines()
    return "".join(lines[-n_lines:])

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🎯 Polymarket Trader")
    st.caption(f"Last refresh: {datetime.now().strftime('%H:%M:%S')}")

    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # Live stats
    signals_df = load_signals()
    trades_df = load_trades()
    markets_df = load_markets()

    n_markets = len(markets_df["id"].unique()) if not markets_df.empty else 0
    n_signals_24h = 0
    if not signals_df.empty:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=24)
        n_signals_24h = len(signals_df[signals_df["timestamp"] >= cutoff])
    n_open_trades = len(trades_df[trades_df["status"] == "OPEN"]) if not trades_df.empty else 0
    total_pnl = trades_df["unrealized_pnl"].sum() if not trades_df.empty else 0.0

    st.metric("Active Markets", n_markets)
    st.metric("Signals (24h)", n_signals_24h)
    st.metric("Open Trades", n_open_trades)
    st.metric("Total Unrealized P&L", f"${total_pnl:.2f}",
              delta=f"${total_pnl:.2f}")

    st.divider()
    st.caption("Auto-refreshes every 60s")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 Live Markets", "⚡ Signals", "💼 Trades", "📈 Performance"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Live Markets
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Live Polymarket Markets")

    df = load_markets()
    if df.empty:
        st.info("Waiting for first data fetch... (runs every 15 minutes)")
        st.caption("The background loop will populate this table shortly.")
    else:
        # Compute 24h change from signals
        if not signals_df.empty:
            latest_sig = signals_df.drop_duplicates(subset=["market_id", "outcome"], keep="first")
            sig_map = {
                (r["market_id"], r["outcome"]): r["change_pp"]
                for _, r in latest_sig.iterrows()
            }
            df["24h_change_pp"] = df.apply(
                lambda row: sig_map.get((row["id"], row["outcome_name"]), None),
                axis=1
            )
            df["is_edge"] = df.apply(
                lambda row: abs(sig_map.get((row["id"], row["outcome_name"]), 0)) >= 5.0,
                axis=1
            )
        else:
            df["24h_change_pp"] = None
            df["is_edge"] = False

        # Category filter
        categories = ["All"] + sorted(df["category"].dropna().unique().tolist())
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            cat_filter = st.selectbox("Category", categories)
        with col2:
            edge_only = st.checkbox("Edge signals only")
        with col3:
            min_volume = st.number_input("Min Volume ($)", value=0, step=1000)

        filtered = df.copy()
        if cat_filter != "All":
            filtered = filtered[filtered["category"] == cat_filter]
        if edge_only:
            filtered = filtered[filtered["is_edge"] == True]
        if min_volume > 0:
            filtered = filtered[filtered["volume_usd"] >= min_volume]

        # Format display
        display_df = filtered[["question", "outcome_name", "current_price", "24h_change_pp", "volume_usd", "category", "is_edge"]].copy()
        display_df.columns = ["Market", "Outcome", "Price", "24h Change (pp)", "Volume ($)", "Category", "Edge?"]
        display_df["Price"] = (display_df["Price"] * 100).round(1).astype(str) + "%"
        display_df["Volume ($)"] = display_df["Volume ($)"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "N/A")
        display_df["24h Change (pp)"] = display_df["24h Change (pp)"].apply(
            lambda x: f"{x:+.1f}" if pd.notna(x) else "—"
        )

        st.dataframe(
            display_df,
            use_container_width=True,
            height=500,
            column_config={
                "Edge?": st.column_config.CheckboxColumn("Edge?"),
            }
        )
        st.caption(f"Showing {len(display_df):,} outcomes across {filtered['id'].nunique():,} markets")

        # Price chart for selected market
        st.subheader("Probability History")
        if not df.empty:
            market_options = df["question"].unique().tolist()[:50]
            selected_market = st.selectbox("Select market to chart", market_options)
            market_row = df[df["question"] == selected_market].iloc[0]
            outcome_options = df[df["id"] == market_row["id"]]["outcome_name"].tolist()
            selected_outcome = st.selectbox("Outcome", outcome_options)

            hist_df = load_price_history(market_row["id"], selected_outcome)
            if not hist_df.empty and len(hist_df) > 1:
                fig = px.line(
                    hist_df, x="timestamp", y="price_pct",
                    title=f"{selected_market[:80]} — {selected_outcome}",
                    labels={"price_pct": "Implied Probability (%)", "timestamp": "Time"},
                    color_discrete_sequence=["#00d2ff"]
                )
                fig.update_layout(
                    template="plotly_dark",
                    hovermode="x unified",
                    height=350
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough history yet to plot. Check back after a few cycles.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Signals
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("Signal Feed")

    df = load_signals()
    if df.empty:
        st.info("No signals detected yet. Signals appear when any market moves >5pp in 24h.")
    else:
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            sig_filter = st.selectbox("Signal type", ["All", "Unexplained Edge", "Explained"])
        with col2:
            min_edge = st.slider("Min edge score", 0.0, 10.0, 0.0, 0.1)
        with col3:
            lookback_hours = st.selectbox("Lookback", [6, 12, 24, 48, 168], index=2)

        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=lookback_hours)
        filtered = df[df["timestamp"] >= cutoff].copy()

        if sig_filter == "Unexplained Edge":
            filtered = filtered[filtered["explained"] == 0]
        elif sig_filter == "Explained":
            filtered = filtered[filtered["explained"] == 1]

        filtered = filtered[filtered["edge_score"] >= min_edge]

        # Stats row
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Signals", len(filtered))
        c2.metric("Unexplained", len(filtered[filtered["explained"] == 0]))
        c3.metric("Max Edge Score", f"{filtered['edge_score'].max():.2f}" if not filtered.empty else "—")
        c4.metric("Avg Change", f"{filtered['change_pp'].abs().mean():.1f}pp" if not filtered.empty else "—")

        st.divider()

        # Display table
        if not filtered.empty:
            display = filtered[[
                "timestamp", "market_name", "outcome",
                "old_prob", "new_prob", "change_pp",
                "correlated_instrument", "edge_score", "explained", "news_summary"
            ]].copy()
            display.columns = [
                "Time", "Market", "Outcome",
                "Old %", "New %", "Change (pp)",
                "Instrument", "Edge Score", "Explained", "News Summary"
            ]
            display["Time"] = display["Time"].dt.strftime("%m-%d %H:%M")
            display["Change (pp)"] = display["Change (pp)"].apply(lambda x: f"{x:+.1f}")
            display["Explained"] = display["Explained"].apply(lambda x: "✅ Yes" if x else "🔴 Edge")

            st.dataframe(display, use_container_width=True, height=450)

        # Edge score histogram
        if not filtered.empty and len(filtered) > 3:
            fig = px.histogram(
                filtered, x="edge_score", color="explained",
                color_discrete_map={0: "#ff4b4b", 1: "#4CAF50"},
                title="Edge Score Distribution",
                labels={"explained": "Explained (1=Yes)", "edge_score": "Edge Score"},
                nbins=20
            )
            fig.update_layout(template="plotly_dark", height=300)
            st.plotly_chart(fig, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Trades
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("Paper Trades — IBKR")

    df = load_trades()
    if df.empty:
        st.info("No trades executed yet. Trades are placed when a high-confidence unexplained edge is found.")
    else:
        open_df = df[df["status"] == "OPEN"]
        closed_df = df[df["status"] != "OPEN"]

        # Open positions
        st.subheader(f"Open Positions ({len(open_df)})")
        if not open_df.empty:
            display_open = open_df[[
                "timestamp", "symbol", "side", "quantity",
                "entry_price", "mark_price", "unrealized_pnl",
                "market_name", "edge_score", "ibkr_order_id"
            ]].copy()
            display_open.columns = [
                "Opened", "Symbol", "Side", "Qty",
                "Entry", "Mark", "Unrealized P&L",
                "Polymarket", "Edge Score", "Order ID"
            ]
            display_open["Opened"] = pd.to_datetime(display_open["Opened"]).dt.strftime("%m-%d %H:%M")
            display_open["Entry"] = display_open["Entry"].apply(lambda x: f"${x:.2f}")
            display_open["Mark"] = display_open["Mark"].apply(lambda x: f"${x:.2f}")
            display_open["Unrealized P&L"] = display_open["Unrealized P&L"].apply(
                lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A"
            )
            st.dataframe(display_open, use_container_width=True)
        else:
            st.caption("No open positions")

        # Total P&L summary
        total_pnl = df["unrealized_pnl"].sum()
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Trades", len(df))
        col2.metric("Open Trades", len(open_df))
        col3.metric("Total Unrealized P&L", f"${total_pnl:+.2f}")

        st.divider()
        st.subheader("All Trades")
        all_display = df[[
            "timestamp", "symbol", "side", "quantity",
            "entry_price", "unrealized_pnl", "market_name",
            "edge_score", "status"
        ]].copy()
        all_display["timestamp"] = pd.to_datetime(all_display["timestamp"]).dt.strftime("%m-%d %H:%M")
        st.dataframe(all_display, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Performance
# ═══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("Performance Analytics")

    trades_df = load_trades()
    signals_df = load_signals()

    if trades_df.empty:
        st.info("Performance metrics will appear after the first trades are logged.")
        # Show signal activity in the meantime
        if not signals_df.empty:
            st.subheader("Signal Activity (no trades yet)")
            sig_hourly = signals_df.copy()
            sig_hourly["hour"] = sig_hourly["timestamp"].dt.floor("H")
            hourly_counts = sig_hourly.groupby(["hour", "explained"]).size().reset_index(name="count")
            fig = px.bar(
                hourly_counts, x="hour", y="count",
                color="explained",
                color_discrete_map={0: "#ff4b4b", 1: "#4CAF50"},
                title="Signals Per Hour",
                labels={"hour": "Time", "count": "Signals", "explained": "Explained"},
                barmode="stack"
            )
            fig.update_layout(template="plotly_dark", height=350)
            st.plotly_chart(fig, use_container_width=True)
    else:
        # Cumulative P&L chart
        df_sorted = trades_df.sort_values("timestamp")
        df_sorted["cumulative_pnl"] = df_sorted["unrealized_pnl"].cumsum()

        fig1 = px.line(
            df_sorted,
            x="timestamp", y="cumulative_pnl",
            title="Cumulative Unrealized P&L",
            labels={"cumulative_pnl": "Cumulative P&L ($)", "timestamp": "Date"},
            color_discrete_sequence=["#00ff88"]
        )
        fig1.add_hline(y=0, line_dash="dash", line_color="gray")
        fig1.update_layout(template="plotly_dark", height=350)
        st.plotly_chart(fig1, use_container_width=True)

        col1, col2 = st.columns(2)

        # Edge score vs P&L scatter
        with col1:
            fig2 = px.scatter(
                trades_df,
                x="edge_score", y="unrealized_pnl",
                color="side",
                size="quantity",
                title="Edge Score vs P&L",
                labels={"edge_score": "Edge Score at Entry", "unrealized_pnl": "Unrealized P&L ($)"},
                color_discrete_map={"BUY": "#4CAF50", "SELL": "#ff4b4b"}
            )
            fig2.add_hline(y=0, line_dash="dash", line_color="gray")
            fig2.update_layout(template="plotly_dark", height=350)
            st.plotly_chart(fig2, use_container_width=True)

        # P&L distribution
        with col2:
            fig3 = px.histogram(
                trades_df, x="unrealized_pnl",
                title="P&L Distribution",
                labels={"unrealized_pnl": "Unrealized P&L ($)"},
                color_discrete_sequence=["#00d2ff"],
                nbins=20
            )
            fig3.add_vline(x=0, line_dash="dash", line_color="white")
            fig3.update_layout(template="plotly_dark", height=350)
            st.plotly_chart(fig3, use_container_width=True)

        # Stats table
        st.subheader("Summary Statistics")
        winners = trades_df[trades_df["unrealized_pnl"] > 0]
        losers = trades_df[trades_df["unrealized_pnl"] < 0]
        stats = {
            "Total Trades": len(trades_df),
            "Winners": len(winners),
            "Losers": len(losers),
            "Win Rate": f"{len(winners)/len(trades_df)*100:.1f}%" if len(trades_df) > 0 else "N/A",
            "Total P&L": f"${trades_df['unrealized_pnl'].sum():+.2f}",
            "Avg P&L per Trade": f"${trades_df['unrealized_pnl'].mean():+.2f}",
            "Best Trade": f"${trades_df['unrealized_pnl'].max():+.2f}",
            "Worst Trade": f"${trades_df['unrealized_pnl'].min():+.2f}",
            "Avg Edge Score": f"{trades_df['edge_score'].mean():.2f}",
            "Max Edge Score": f"{trades_df['edge_score'].max():.2f}",
        }
        stat_df = pd.DataFrame.from_dict(stats, orient="index", columns=["Value"])
        st.dataframe(stat_df, use_container_width=False, width=400)

    # System log
    with st.expander("System Log (last 50 lines)"):
        log_text = load_log_tail(50)
        st.code(log_text, language="text")


# ── Footer ─────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "Polymarket Trading System • Paper trading only • "
    f"Data as of {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local"
)
