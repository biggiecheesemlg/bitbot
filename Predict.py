"""
predictor.py — Polymarket BTC Price Prediction Engine
======================================================
Streams all 20 BTC outcome markets simultaneously.
Builds a unified feature vector from the full probability curve,
order book microstructure, and trade flow across every outcome.
Predicts which price level BTC will hit by May 1 resolution.

Architecture:
  - Single WS → all 40 tokens
  - Snapshot loop builds feature vector every 10s
  - XGBoost model trains on historical snapshots
  - Broadcasts predictions + live odds to browser via WS
  - HTTP server serves dashboard.html

Run:  python predictor.py
Open: http://localhost:8090
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import requests
import websockets
from websockets.server import serve

os.environ.setdefault("XGBOOST_VERBOSITY", "0")

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("[predictor] WARNING: xgboost not installed — pip install xgboost")

try:
    import pandas as pd
    PD_AVAILABLE = True
except ImportError:
    PD_AVAILABLE = False
    print("[predictor] WARNING: pandas not installed — pip install pandas")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

HTTP_PORT       = 8090
PRED_WS_PORT    = 8091
SNAP_INTERVAL   = 10          # seconds between feature snapshots
MIN_TRAIN_ROWS  = 30         # lower threshold for faster first train
RETRAIN_EVERY   = 30          # retrain every N new rows
DATASET_PATH    = "btc_poly_data.json"
MODEL_PATH      = "btc_poly_xgb.json"
MARKETS_CACHE   = "markets_cache.json"

CLOB_WS_URI = "wss://ws-subscriptions-frontend-clob.polymarket.com/ws/market"
BITUNIX_KLINE = "https://fapi.bitunix.com/api/v1/futures/market/kline"

WS_HEADERS = {
    "Origin":     "https://polymarket.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
}


# ══════════════════════════════════════════════════════════════════════════════
# LOAD MARKETS FROM CACHE
# ══════════════════════════════════════════════════════════════════════════════

def load_markets() -> list[dict]:
    """Load all 20 BTC outcome markets from markets_cache.json."""
    path = Path(MARKETS_CACHE)
    if not path.exists():
        raise FileNotFoundError(f"{MARKETS_CACHE} not found — run step1_gamma_pull.py first")

    with open(path) as f:
        cache = json.load(f)

    markets = []
    raw_markets = cache.get("event", {}).get("markets", cache.get("markets", []))

    for mkt in raw_markets:
        clob_ids = mkt.get("clobTokenIds", "[]")
        if isinstance(clob_ids, str):
            clob_ids = json.loads(clob_ids)

        yes_id = clob_ids[0] if len(clob_ids) > 0 else None
        no_id  = clob_ids[1] if len(clob_ids) > 1 else None

        if not yes_id:
            continue

        # Parse outcome prices
        out_prices = mkt.get("outcomePrices", "[0.5, 0.5]")
        if isinstance(out_prices, str):
            out_prices = json.loads(out_prices)

        markets.append({
            "question":    mkt.get("question", ""),
            "slug":        mkt.get("slug", ""),
            "yes_token":   yes_id,
            "no_token":    no_id,
            "yes_price":   float(out_prices[0]) if out_prices else 0.5,
            "no_price":    float(out_prices[1]) if out_prices else 0.5,
            "volume":      float(mkt.get("volumeNum", mkt.get("volume", 0))),
            "best_bid":    float(mkt.get("bestBid", 0)),
            "best_ask":    float(mkt.get("bestAsk", 0)),
        })

    markets.sort(key=lambda x: x["volume"], reverse=True)
    print(f"[markets] loaded {len(markets)} outcomes")
    for m in markets:
        print(f"  {m['question'][:50]:50s}  YES={m['yes_price']:.3f}  vol=${m['volume']:,.0f}")
    return markets


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════════

MARKETS: list[dict] = []

# token_id → {best_bid, best_ask, last_trade, bids, asks, spread, mid}
book_state: dict[str, dict] = {}

# token_id → deque of recent price changes
price_history: dict[str, deque] = {}

# rolling snapshots for training
snapshots:  deque = deque(maxlen=2000)
btc_prices: deque = deque(maxlen=60)

# model state
model       = None
model_rows  = 0
last_pred   = None
dataset     = []

# ws clients
clients: set = set()
main_loop: Optional[asyncio.AbstractEventLoop] = None
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET — CLOB STREAM
# ══════════════════════════════════════════════════════════════════════════════

async def clob_ws_loop():
    global MARKETS
    all_token_ids = []
    for m in MARKETS:
        all_token_ids.append(m["yes_token"])
        if m["no_token"]:
            all_token_ids.append(m["no_token"])

    retry_delay = 1
    while True:
        try:
            async with websockets.connect(
                CLOB_WS_URI,
                additional_headers=WS_HEADERS,
            ) as ws:
                print(f"[clob-ws] connected — subscribing {len(all_token_ids)} tokens")
                await ws.send(json.dumps({
                    "type":       "markets",
                    "assets_ids": all_token_ids,
                }))
                print(f"[clob-ws] subscribed")
                retry_delay = 1

                while True:
                    try:
                        raw   = await ws.recv()
                        data  = json.loads(raw)
                        items = data if isinstance(data, list) else [data]
                        for item in items:
                            handle_clob_message(item)
                    except websockets.ConnectionClosed as e:
                        print(f"[clob-ws] closed: {e.code} {e.reason}")
                        raise

        except Exception as e:
            print(f"[clob-ws] disconnected: {e} — retry in {retry_delay}s")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


def handle_clob_message(data: dict):
    event_type = data.get("event_type")
    token_id   = data.get("asset_id", "")
    if not token_id:
        return

    if event_type == "book":
        bids = sorted([(float(b["price"]), float(b["size"])) for b in data.get("bids", [])], reverse=True)
        asks = sorted([(float(a["price"]), float(a["size"])) for a in data.get("asks", [])], key=lambda x: x[0])
        last = float(data.get("last_trade_price", 0) or 0)

        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread   = round(best_ask - best_bid, 4) if best_bid and best_ask else None
        mid      = round((best_bid + best_ask) / 2, 4) if best_bid and best_ask else None

        with _lock:
            book_state[token_id] = {
                "bids": bids[:10], "asks": asks[:10],
                "best_bid": best_bid, "best_ask": best_ask,
                "spread": spread, "mid": mid,
                "last_trade": last, "ts": time.time(),
            }
            if token_id not in price_history:
                price_history[token_id] = deque(maxlen=100)
            if mid:
                price_history[token_id].append((time.time(), mid))

        print(f"[book] ...{token_id[-8:]}  bid={best_bid}  ask={best_ask}  mid={mid}")

    elif event_type == "price_change":
        with _lock:
            if token_id not in book_state:
                return
            book = book_state[token_id]
            bids = list(book.get("bids", []))
            asks = list(book.get("asks", []))

        for c in data.get("changes", []):
            side  = c.get("side")
            price = float(c.get("price", 0))
            size  = float(c.get("size",  0))
            if side == "BUY":
                bids = _apply_level(bids, price, size, desc=True)
            else:
                asks = _apply_level(asks, price, size, desc=False)

        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        mid      = round((best_bid + best_ask) / 2, 4) if best_bid and best_ask else None

        with _lock:
            book_state[token_id].update({
                "bids": bids, "asks": asks,
                "best_bid": best_bid, "best_ask": best_ask,
                "mid": mid, "ts": time.time(),
            })
            if mid and token_id in price_history:
                price_history[token_id].append((time.time(), mid))

    elif event_type == "last_trade_price":
        price = float(data.get("price", 0) or 0)
        with _lock:
            if token_id in book_state:
                book_state[token_id]["last_trade"] = price


def _apply_level(levels, price, size, desc):
    levels = [(p, s) for p, s in levels if p != price]
    if size > 0:
        levels.append((price, size))
    levels.sort(key=lambda x: x[0], reverse=desc)
    return levels


# ══════════════════════════════════════════════════════════════════════════════
# BTC PRICE (Bitunix)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_btc_price() -> Optional[float]:
    try:
        r = requests.get(BITUNIX_KLINE,
                         params={"symbol": "BTCUSDT", "interval": "1m", "limit": "2"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        data = r.json()
        raw  = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(raw, dict):
            raw = raw.get("list", raw.get("klines", raw.get("data", [])))
        if not raw:
            return None
        item = raw[-2] if len(raw) >= 2 else raw[-1]
        if isinstance(item, list):
            return float(item[4])
        return float(item.get("close", item.get("c", 0)))
    except Exception as e:
        print(f"[btc] fetch error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ══════════════════════════════════════════════════════════════════════════════

def build_snapshot() -> dict:
    """
    Build the full feature vector:
    - For each of 20 markets: yes_mid, spread, imbalance, bid_depth, ask_depth, momentum
    - Global: BTC price, curve shape (entropy, peak, spread of probs), time features
    """
    with _lock:
        book_snap = {k: dict(v) for k, v in book_state.items()}
        ph_snap   = {k: list(v) for k, v in price_history.items()}

    snap = {"ts": time.time()}

    # ── Per-market features ──────────────────────────────────────────────────
    yes_mids     = []
    total_spread = 0
    n_books      = 0

    for i, mkt in enumerate(MARKETS):
        prefix = f"m{i:02d}"
        yt     = mkt["yes_token"]
        nt     = mkt.get("no_token")
        book   = book_snap.get(yt, {})

        mid     = book.get("mid")
        bid     = book.get("best_bid")
        ask     = book.get("best_ask")
        spread  = book.get("spread")
        bids    = book.get("bids", [])
        asks    = book.get("asks", [])

        # Fallback to gamma REST price if no book yet
        if mid is None:
            mid = mkt["yes_price"]

        yes_mids.append(mid if mid is not None else float("nan"))

        # Order book imbalance
        bid_sz = sum(s for _, s in bids[:5])
        ask_sz = sum(s for _, s in asks[:5])
        imbal  = (bid_sz - ask_sz) / (bid_sz + ask_sz + 1e-9)

        # Momentum: price change over last 6 ticks
        hist = ph_snap.get(yt, [])
        momentum = float("nan")
        if len(hist) >= 2:
            old_p = hist[max(0, len(hist)-6)][1]
            new_p = hist[-1][1]
            momentum = new_p - old_p

        snap[f"{prefix}_mid"]      = mid if mid is not None else float("nan")
        snap[f"{prefix}_spread"]   = spread if spread is not None else float("nan")
        snap[f"{prefix}_imbal"]    = imbal
        snap[f"{prefix}_bid_sz"]   = bid_sz
        snap[f"{prefix}_ask_sz"]   = ask_sz
        snap[f"{prefix}_momentum"] = momentum
        snap[f"{prefix}_last"]     = book.get("last_trade", float("nan"))

        if spread is not None:
            total_spread += spread
            n_books += 1

    # ── Probability curve shape features ────────────────────────────────────
    valid_mids = [m for m in yes_mids if not math.isnan(m)]
    total_prob  = sum(valid_mids) if valid_mids else 1.0

    # Normalize to get a proper probability distribution
    if total_prob > 0:
        norm = [m / total_prob for m in valid_mids]
        # Shannon entropy — low = market is very confident in one outcome
        entropy = -sum(p * math.log(p + 1e-9) for p in norm)
        # Peak probability (most likely outcome)
        peak_prob = max(norm) if norm else float("nan")
        # Index of peak (which price level is most likely)
        peak_idx = norm.index(peak_prob) if norm else float("nan")
    else:
        entropy   = float("nan")
        peak_prob = float("nan")
        peak_idx  = float("nan")

    snap["curve_entropy"]   = entropy
    snap["curve_peak_prob"] = peak_prob
    snap["curve_peak_idx"]  = peak_idx
    snap["curve_total"]     = total_prob
    snap["avg_spread"]      = total_spread / n_books if n_books > 0 else float("nan")
    snap["n_books_live"]    = n_books

    # ── BTC price features ───────────────────────────────────────────────────
    btc = fetch_btc_price()
    with _lock:
        btc_prices.append(btc)
        btc_hist = list(btc_prices)

    snap["btc_price"] = btc if btc else float("nan")
    if len(btc_hist) >= 2 and btc_hist[-2]:
        snap["btc_ret_1m"] = (btc_hist[-1] - btc_hist[-2]) / btc_hist[-2] if btc_hist[-1] else float("nan")
    else:
        snap["btc_ret_1m"] = float("nan")

    if len(btc_hist) >= 10:
        valid_btc = [p for p in btc_hist if p]
        if len(valid_btc) >= 5:
            rets = [(valid_btc[i] - valid_btc[i-1]) / valid_btc[i-1] for i in range(1, len(valid_btc))]
            snap["btc_volatility"] = float(np.std(rets))
        else:
            snap["btc_volatility"] = float("nan")
    else:
        snap["btc_volatility"] = float("nan")

    # ── Time features ────────────────────────────────────────────────────────
    dt = datetime.now(tz=timezone.utc)
    snap["hour_sin"]      = math.sin(2 * math.pi * dt.hour   / 24)
    snap["hour_cos"]      = math.cos(2 * math.pi * dt.hour   / 24)
    snap["min_sin"]       = math.sin(2 * math.pi * dt.minute / 60)
    snap["min_cos"]       = math.cos(2 * math.pi * dt.minute / 60)

    # Days until resolution (May 1 2026)
    resolve_ts = datetime(2026, 5, 1, 4, 0, 0, tzinfo=timezone.utc).timestamp()
    snap["days_to_expiry"] = max(0, (resolve_ts - time.time()) / 86400)

    return snap


def get_feature_cols(n_markets: int) -> list[str]:
    cols = []
    for i in range(n_markets):
        p = f"m{i:02d}"
        cols += [f"{p}_mid", f"{p}_spread", f"{p}_imbal",
                 f"{p}_bid_sz", f"{p}_ask_sz", f"{p}_momentum", f"{p}_last"]
    cols += ["curve_entropy", "curve_peak_prob", "curve_peak_idx", "curve_total",
             "avg_spread", "n_books_live",
             "btc_price", "btc_ret_1m", "btc_volatility",
             "hour_sin", "hour_cos", "min_sin", "min_cos", "days_to_expiry"]
    return cols


# ══════════════════════════════════════════════════════════════════════════════
# LABELING
# Target: predict BTC direction using the most liquid YES mid price.
# Uses the highest-volume market's yes_mid as the price proxy.
# Label every snapshot — 1 if price goes up, 0 if down or flat.
# This guarantees labels even in quiet markets.
# ══════════════════════════════════════════════════════════════════════════════

HORIZON = 3   # snapshots ahead (3 × 10s = 30s)

def assign_labels(snaps: list) -> list:
    labeled = []
    for i in range(len(snaps) - HORIZON):
        cur = snaps[i]
        fut = snaps[i + HORIZON]

        # Use BTC price only — no fallback to synthetic market prices
        cur_price = cur.get("btc_price")
        fut_price = fut.get("btc_price")

        if not cur_price or not fut_price:
            continue
        try:
            if math.isnan(cur_price) or math.isnan(fut_price):
                continue
        except TypeError:
            continue

        # 1 = BTC up, 0 = BTC down/flat
        label = 1 if fut_price > cur_price else 0

        row = dict(cur)
        row["label"] = label
        labeled.append(row)
    return labeled


# ══════════════════════════════════════════════════════════════════════════════
# MODEL TRAIN / INFER
# ══════════════════════════════════════════════════════════════════════════════

def train_model(rows: list, feature_cols: list):
    if not XGB_AVAILABLE or not PD_AVAILABLE:
        return None
    if len(rows) < MIN_TRAIN_ROWS:
        return None

    import pandas as pd
    df = pd.DataFrame(rows)
    print(f"[train] {len(df)} rows, {len(feature_cols)} features")

    # Fill NaN with 0 — XGBoost handles missing values natively
    # Only drop rows where label or btc_price is missing
    df = df.dropna(subset=["label", "btc_price"])
    for col in feature_cols:
        if col not in df.columns:
            df[col] = 0.0
    df[feature_cols] = df[feature_cols].fillna(0.0)

    print(f"[train] {len(df)} rows after cleaning")
    if len(df) < MIN_TRAIN_ROWS:
        print(f"[train] not enough clean rows: {len(df)} < {MIN_TRAIN_ROWS}")
        return None

    X     = df[feature_cols].values.astype(np.float32)
    y     = df["label"].values.astype(int)
    split = int(len(X) * 0.8)

    has_val = split < len(X) and len(X) - split >= 2
    m = xgb.XGBClassifier(
        n_estimators=100, max_depth=3, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="logloss", verbosity=0, random_state=42,
        early_stopping_rounds=10 if has_val else None,
    )
    try:
        if has_val:
            m.fit(X[:split], y[:split],
                  eval_set=[(X[split:], y[split:])],
                  verbose=False)
            acc = float(np.mean(m.predict(X[split:]) == y[split:]))
            print(f"[model] trained {len(X)} rows  val_acc={acc:.3f}")
        else:
            m.fit(X, y, verbose=False)
            print(f"[model] trained {len(X)} rows (no val split)")
    except Exception as e:
        print(f"[train] fit error: {e}")
        import traceback; traceback.print_exc()
        return None

    try:
        m.save_model(MODEL_PATH)
        print(f"[model] saved to {MODEL_PATH}")
    except Exception as e:
        print(f"[model] save error: {e}")

    return m


def infer(m, snap: dict, feature_cols: list) -> dict:
    X     = np.array([[snap.get(k, float("nan")) for k in feature_cols]], dtype=np.float32)
    proba = m.predict_proba(X)[0]
    # proba[0] = prob BTC goes lower, proba[1] = prob BTC goes higher
    prob_up   = float(proba[1])
    prob_down = float(proba[0])
    edge      = abs(prob_up - 0.5) * 2
    direction = "UP 📈" if prob_up > 0.55 else "DOWN 📉" if prob_down > 0.55 else "NEUTRAL ➡️"
    confidence = "HIGH" if edge >= 0.4 else "MEDIUM" if edge >= 0.2 else "LOW"

    # Most likely outcome based on current curve
    peak_idx = snap.get("curve_peak_idx")
    if peak_idx is not None and not math.isnan(peak_idx):
        peak_idx = int(round(peak_idx))
        if 0 <= peak_idx < len(MARKETS):
            most_likely = MARKETS[peak_idx]["question"]
        else:
            most_likely = "unknown"
    else:
        most_likely = "no data"

    return {
        "type":        "prediction",
        "direction":   direction,
        "prob_up":     round(prob_up, 4),
        "prob_down":   round(prob_down, 4),
        "edge":        round(edge, 4),
        "confidence":  confidence,
        "most_likely": most_likely,
        "model_rows":  model_rows,
        "ts":          int(time.time()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT LOOP
# ══════════════════════════════════════════════════════════════════════════════

def snap_loop():
    global model, model_rows, last_pred, dataset

    feature_cols = get_feature_cols(len(MARKETS))

    # Load saved model
    if XGB_AVAILABLE and os.path.exists(MODEL_PATH):
        try:
            m = xgb.XGBClassifier()
            m.load_model(MODEL_PATH)
            with _lock:
                model = m
            print(f"[model] loaded from {MODEL_PATH}")
        except Exception as e:
            print(f"[model] load error: {e}")

    # Load saved dataset
    if os.path.exists(DATASET_PATH):
        try:
            with open(DATASET_PATH) as f:
                dataset = json.load(f)
            print(f"[data] loaded {len(dataset)} rows from {DATASET_PATH}")
        except Exception:
            dataset = []

    # Use dict keyed by ts for O(1) dedup
    dataset_dict = {r["ts"]: r for r in dataset}
    rows_at_last_train = 0  # always start at 0 so milestone triggers on first run
    last_save_time = 0

    # Train immediately if we have enough data from disk
    if len(dataset_dict) >= MIN_TRAIN_ROWS:
        print(f"[model] training on {len(dataset_dict)} rows loaded from disk…")
        feature_cols_startup = get_feature_cols(len(MARKETS))
        rows_sorted = sorted(dataset_dict.values(), key=lambda x: x["ts"])
        m = train_model(rows_sorted, feature_cols_startup)
        if m:
            with _lock:
                model = m
            model_rows = len(dataset_dict)
            rows_at_last_train = len(dataset_dict)
            print(f"[model] ✓ startup model ready")

    while True:
        try:
            snap = build_snapshot()

            with _lock:
                snapshots.append(snap)
                snap_list = list(snapshots)

            # Build odds for broadcast
            odds = []
            for i, mkt in enumerate(MARKETS):
                mid = snap.get(f"m{i:02d}_mid")
                spread = snap.get(f"m{i:02d}_spread")
                mom = snap.get(f"m{i:02d}_momentum")
                odds.append({
                    "question": mkt["question"],
                    "yes_mid":  round(mid, 4) if mid and not math.isnan(mid) else None,
                    "spread":   round(spread, 4) if spread and not math.isnan(spread) else None,
                    "momentum": round(mom, 4) if mom and not math.isnan(mom) else None,
                })

            btc = snap.get("btc_price")
            broadcast({
                "type":          "status",
                "btc_price":     btc,
                "peak_idx":      snap.get("curve_peak_idx"),
                "curve_entropy": snap.get("curve_entropy"),
                "n_books_live":  snap.get("n_books_live"),
                "model_rows":    model_rows,
                "odds":          odds,
                "ts":            int(time.time()),
            })

            btc_str = f"${btc:,.0f}" if btc else "—"
            print(f"[snap] btc={btc_str}  books={snap.get('n_books_live')}  "
                  f"entropy={snap.get('curve_entropy', 0):.3f}  dataset={len(dataset_dict)}")

            # Label new rows — only add rows we haven't seen before
            new_rows = assign_labels(snap_list)
            print(f"[label] snap_list={len(snap_list)}  new_rows={len(new_rows)}")
            added = 0
            for row in new_rows:
                ts = row.get("ts")
                if ts and ts not in dataset_dict:
                    dataset_dict[ts] = row
                    added += 1

            if added:
                print(f"[data] +{added} labeled rows → total={len(dataset_dict)}")

            n = len(dataset_dict)

            # Save every 60 seconds unconditionally
            if time.time() - last_save_time > 60 and n > 0:
                try:
                    rows = sorted(dataset_dict.values(), key=lambda x: x["ts"])[-5000:]
                    with open(DATASET_PATH, "w") as f:
                        json.dump(rows, f)
                    last_save_time = time.time()
                    print(f"[data] saved {n} rows → {DATASET_PATH}")
                except Exception as e:
                    print(f"[data] save error: {e}")

            # Retrain at milestones: 30, 60, 90, 120...
            next_milestone = max(MIN_TRAIN_ROWS, rows_at_last_train + RETRAIN_EVERY)

            if n >= next_milestone:
                print(f"[model] retraining on {n} rows (milestone={next_milestone})…")
                rows_for_train = sorted(dataset_dict.values(), key=lambda x: x["ts"])
                new_model = train_model(rows_for_train, feature_cols)
                if new_model:
                    with _lock:
                        model = new_model
                    model_rows = n
                    rows_at_last_train = n
                    print(f"[model] ✓ model updated")
                else:
                    print(f"[model] train_model returned None — check xgboost install")
            elif n < MIN_TRAIN_ROWS:
                print(f"[data] collecting… {n}/{MIN_TRAIN_ROWS}")

            # Infer
            with _lock:
                current_model = model

            if current_model:
                pred = infer(current_model, snap, feature_cols)
                with _lock:
                    last_pred = pred
                broadcast(pred)
                print(f"[pred] {pred['direction']}  P↑={pred['prob_up']:.2%}  "
                      f"edge={pred['edge']:.3f}  [{pred['confidence']}]")
            else:
                print(f"[model] no model yet — {n}/{MIN_TRAIN_ROWS} rows")

        except Exception as e:
            print(f"[snap] error: {e}")
            import traceback; traceback.print_exc()

        time.sleep(SNAP_INTERVAL)


# ══════════════════════════════════════════════════════════════════════════════
# BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

def broadcast(msg: dict):
    global main_loop
    if main_loop is None:
        return
    # Replace NaN/Infinity with null so JSON.parse works in the browser
    import math
    def clean(o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        if isinstance(o, dict):  return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list): return [clean(v) for v in o]
        return o
    asyncio.run_coroutine_threadsafe(_async_broadcast(json.dumps(clean(msg))), main_loop)


async def _async_broadcast(data: str):
    dead = set()
    for ws in list(clients):
        try:
            await ws.send(data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


# ══════════════════════════════════════════════════════════════════════════════
# WS SERVER + HTTP SERVER
# ══════════════════════════════════════════════════════════════════════════════

async def ws_handler(websocket):
    clients.add(websocket)
    print(f"[ws] client connected ({len(clients)} total)")
    try:
        with _lock:
            p = last_pred
        if p:
            await websocket.send(json.dumps(p))
        async for _ in websocket:
            pass
    except Exception:
        pass
    finally:
        clients.discard(websocket)
        print(f"[ws] client disconnected ({len(clients)} total)")


def start_http():
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "predictor_dashboard.html")
            try:
                with open(path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"predictor_dashboard.html not found")

        def log_message(self, *args):
            pass

    HTTPServer(("localhost", HTTP_PORT), Handler).serve_forever()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def async_main():
    global main_loop, MARKETS
    main_loop = asyncio.get_running_loop()

    MARKETS = load_markets()

    threading.Thread(target=snap_loop,  daemon=True, name="snap_loop").start()
    threading.Thread(target=start_http, daemon=True, name="http").start()

    print(f"[pred] HTTP  → http://localhost:{HTTP_PORT}")
    print(f"[pred] WS    → ws://localhost:{PRED_WS_PORT}")

    async with serve(ws_handler, "localhost", PRED_WS_PORT):
        await asyncio.gather(
            clob_ws_loop(),
            asyncio.Future(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n[predictor] stopped")
