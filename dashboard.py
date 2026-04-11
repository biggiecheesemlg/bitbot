"""
LIVE DASHBOARD: Real-time heatmap of odds movement
===================================================
Run this AFTER the WebSocket stream has collected data.
Or run alongside it — reads SQLite every N seconds.

Launch:  python dashboard.py
Opens:   http://localhost:8050
"""

import sqlite3
import json
import time
from datetime import datetime, timezone

try:
    import dash
    from dash import dcc, html, Input, Output
    import plotly.graph_objects as go
    import plotly.express as px
    import pandas as pd
    DASH_AVAILABLE = True
except ImportError:
    DASH_AVAILABLE = False

DB_PATH = "polymarket_stream.db"
META_FILE = "markets_cache.json"


# ──────────────────────────────────────────────
# DATA LOADER
# ──────────────────────────────────────────────

def load_price_history(db_path: str, limit: int = 500) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("""
        SELECT pt.token_id, pt.ts, pt.price, mm.question, mm.side
        FROM price_ticks pt
        LEFT JOIN market_meta mm ON pt.token_id = mm.token_id
        ORDER BY pt.ts DESC LIMIT ?
    """, conn, params=(limit,))
    conn.close()
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def load_latest_book(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("""
        SELECT bs.token_id, bs.best_bid, bs.best_ask, bs.spread, bs.mid, bs.ts,
               mm.question, mm.side
        FROM book_snapshots bs
        LEFT JOIN market_meta mm ON bs.token_id = mm.token_id
        WHERE bs.id IN (
            SELECT MAX(id) FROM book_snapshots GROUP BY token_id
        )
        ORDER BY mm.question, mm.side
    """, conn)
    conn.close()
    return df


def load_recent_trades(db_path: str, limit: int = 100) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("""
        SELECT t.*, mm.question, mm.side
        FROM trades t
        LEFT JOIN market_meta mm ON t.token_id = mm.token_id
        ORDER BY t.ts DESC LIMIT ?
    """, conn, params=(limit,))
    conn.close()
    return df


# ──────────────────────────────────────────────
# HEATMAP BUILDER
# ──────────────────────────────────────────────

def build_heatmap(book_df: pd.DataFrame) -> go.Figure:
    """Heatmap: outcomes (rows) × YES/NO side (cols) coloured by mid price."""
    if book_df.empty:
        return go.Figure().update_layout(title="No data yet")

    yes_df = book_df[book_df["side"] == "YES"].copy()
    # Shorten question labels
    yes_df["label"] = yes_df["question"].str.replace("Will Bitcoin hit ", "BTC ", regex=False)
    yes_df = yes_df.sort_values("mid", ascending=False).head(20)

    fig = go.Figure(go.Heatmap(
        z=yes_df["mid"].tolist(),
        y=yes_df["label"].tolist(),
        x=["Implied Prob (YES)"],
        colorscale="RdYlGn",
        zmin=0, zmax=1,
        text=[[f"{v:.2f}"] for v in yes_df["mid"]],
        texttemplate="%{text}",
        colorbar=dict(title="Probability"),
    ))
    fig.update_layout(
        title="🔥 Live Odds Heatmap — BTC April 2026",
        height=max(400, len(yes_df) * 35),
        margin=dict(l=220, r=20, t=50, b=20),
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#e6edf3",
    )
    return fig


def build_spread_chart(book_df: pd.DataFrame) -> go.Figure:
    """Bar chart of bid-ask spreads — wide spread = illiquid / mispriced."""
    if book_df.empty:
        return go.Figure()

    yes_df = book_df[book_df["side"] == "YES"].copy()
    yes_df["label"] = yes_df["question"].str.slice(0, 40)
    yes_df = yes_df.sort_values("spread", ascending=False).head(15)

    fig = go.Figure(go.Bar(
        x=yes_df["spread"],
        y=yes_df["label"],
        orientation="h",
        marker_color="rgba(255,100,80,0.8)",
    ))
    fig.update_layout(
        title="📊 Bid-Ask Spreads (wider = mispriced opportunity)",
        xaxis_title="Spread ($)",
        height=400,
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#e6edf3",
    )
    return fig


def build_price_lines(price_df: pd.DataFrame) -> go.Figure:
    """Line chart of recent price ticks per outcome."""
    if price_df.empty:
        return go.Figure()

    fig = go.Figure()
    for (question, side), grp in price_df.groupby(["question", "side"]):
        label = f"{question[:30]}… [{side}]"
        fig.add_trace(go.Scatter(
            x=grp["ts"], y=grp["price"],
            mode="lines", name=label,
            line=dict(width=1.5),
        ))
    fig.update_layout(
        title="📈 Live Price Ticks",
        yaxis_title="Price",
        height=400,
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#e6edf3",
        showlegend=False,
    )
    return fig


# ──────────────────────────────────────────────
# DASH APP
# ──────────────────────────────────────────────

if DASH_AVAILABLE:
    app = dash.Dash(__name__)
    app.title = "Polymarket BTC Dashboard"

    app.layout = html.Div(style={"backgroundColor": "#0d1117", "color": "#e6edf3", "fontFamily": "monospace"}, children=[
        html.H2("⚡ Polymarket BTC April 2026 — Live Dashboard",
                style={"padding": "20px", "color": "#58a6ff"}),

        dcc.Interval(id="refresh", interval=5000, n_intervals=0),  # refresh every 5s

        html.Div(id="stats-row", style={"padding": "0 20px"}),

        html.Div([
            dcc.Graph(id="heatmap"),
            dcc.Graph(id="spread-chart"),
            dcc.Graph(id="price-lines"),
        ], style={"padding": "0 20px"}),

        html.Div(id="alerts-panel",
                 style={"padding": "20px", "backgroundColor": "#161b22", "margin": "20px",
                        "borderRadius": "8px", "border": "1px solid #30363d"}),
    ])

    @app.callback(
        Output("heatmap", "figure"),
        Output("spread-chart", "figure"),
        Output("price-lines", "figure"),
        Output("stats-row", "children"),
        Output("alerts-panel", "children"),
        Input("refresh", "n_intervals"),
    )
    def refresh_dashboard(_):
        try:
            book_df   = load_latest_book(DB_PATH)
            price_df  = load_price_history(DB_PATH)
            trade_df  = load_recent_trades(DB_PATH, limit=20)

            heatmap      = build_heatmap(book_df)
            spread_chart = build_spread_chart(book_df)
            price_lines  = build_price_lines(price_df)

            stats = html.Div([
                html.Span(f"📦 Markets tracked: {book_df['token_id'].nunique() // 2}",
                          style={"margin": "0 20px"}),
                html.Span(f"📡 Price ticks: {len(price_df)}",
                          style={"margin": "0 20px"}),
                html.Span(f"💹 Recent trades: {len(trade_df)}",
                          style={"margin": "0 20px"}),
                html.Span(f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC",
                          style={"margin": "0 20px", "color": "#8b949e"}),
            ])

            # Arbitrage alerts
            alerts_content = [html.H4("🚨 Arbitrage Scanner", style={"color": "#f85149"})]
            if not book_df.empty:
                yes_df = book_df[book_df["side"] == "YES"].set_index("token_id")
                no_df  = book_df[book_df["side"] == "NO"].set_index("token_id")
                found_arb = False
                for _, yrow in yes_df.iterrows():
                    q = yrow["question"]
                    matched = no_df[no_df["question"] == q]
                    if matched.empty:
                        continue
                    nrow = matched.iloc[0]
                    sum_bids = (yrow["best_bid"] or 0) + (nrow["best_bid"] or 0)
                    sum_asks = (yrow["best_ask"] or 0) + (nrow["best_ask"] or 0)
                    if sum_bids < 0.97:
                        alerts_content.append(html.P(
                            f"✅ BUY BOTH: {q[:50]}  (sum_bids={sum_bids:.4f}, profit={1-sum_bids:.4f})",
                            style={"color": "#3fb950"}
                        ))
                        found_arb = True
                    if sum_asks > 1.03:
                        alerts_content.append(html.P(
                            f"✅ SELL BOTH: {q[:50]}  (sum_asks={sum_asks:.4f}, profit={sum_asks-1:.4f})",
                            style={"color": "#3fb950"}
                        ))
                        found_arb = True
                if not found_arb:
                    alerts_content.append(html.P("No arb detected at this time.", style={"color": "#8b949e"}))
            else:
                alerts_content.append(html.P("Waiting for book data...", style={"color": "#8b949e"}))

            return heatmap, spread_chart, price_lines, stats, alerts_content

        except Exception as e:
            empty = go.Figure()
            return empty, empty, empty, html.Span(f"Error: {e}", style={"color":"red"}), []


    def run_dashboard():
        print("\n[DASHBOARD] Starting at http://localhost:8050")
        print("[DASHBOARD] Make sure step3_4_websocket_stream.py is running!")
        app.run(debug=False, port=8050)


else:
    def run_dashboard():
        print("[!] Install dash + plotly: pip install dash plotly pandas")


if __name__ == "__main__":
    run_dashboard()
