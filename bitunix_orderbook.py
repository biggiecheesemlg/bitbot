"""
Bitunix Chart Server — DOGEUSDT

Fetches kline + orderbook data from Bitunix and serves it to the browser via WebSocket.
Computes EMA clouds + 5-tier absorption bubble signals and fires webhooks to localhost:5000.

Install:  pip install websocket-client websockets requests
Run:      python server.py
Open:     http://localhost:8080
"""

import asyncio
import json
import threading
import time
import math
import requests
import websocket
import websockets
from websockets import serve
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

# ── Config ────────────────────────────────────────────────────────────────────
BITUNIX_WS    = "wss://fapi.bitunix.com/public/"
BITUNIX_REST  = "https://fapi.bitunix.com/api/v1/futures/market/kline"
BITUNIX_DEPTH = "https://fapi.bitunix.com/api/v1/futures/market/depth"
LOCAL_PORT = 8765
HTTP_PORT  = 8080
SYMBOL     = "DOGEUSDT"
INTERVAL   = "1min"          # internal key; mapped to REST/WS formats below
WEBHOOK_URL    = "http://127.0.0.1:5000/webhook"
TRADE_AMOUNT   = 30
TRADE_LEVERAGE = 25

# ── Interval format maps ──────────────────────────────────────────────────────
# Toolbar sends keys like "1min", "5min", "15min", "1hour", "4hour", "1day".
# Bitunix REST uses:  1m  5m  15m  1h  4h  1d
# Bitunix WS uses:    market_kline_1min  market_kline_5min  market_kline_15min
#                     market_kline_60min  market_kline_4h  market_kline_1day

REST_INTERVAL = {
    "1min":  "1m",
    "5min":  "5m",
    "15min": "15m",
    "30min": "30m",
    "1hour": "1h",
    "2hour": "2h",
    "4hour": "4h",
    "6hour": "6h",
    "1day":  "1d",
    "1week": "1w",
}

WS_CHANNEL = {
    "1min":  "market_kline_1min",
    "5min":  "market_kline_5min",
    "15min": "market_kline_15min",
    "30min": "market_kline_30min",
    "1hour": "market_kline_60min",
    "2hour": "market_kline_2h",
    "4hour": "market_kline_4h",
    "6hour": "market_kline_6h",
    "1day":  "market_kline_1day",
    "1week": "market_kline_1week",
}

# ── Absorption Bubble Settings ────────────────────────────────────────────────
LOOKBACK           = 100
LIM_FACTOR         = 0.1
SHOW_BUBBLES       = True
CHECKLIST_LOOKBACK = 120
TIER_C_MIN = LIM_FACTOR + 2   # 2.1
TIER_D_MIN = LIM_FACTOR + 3   # 3.1
TIER_E_MIN = LIM_FACTOR + 6   # 6.1

# ── State ─────────────────────────────────────────────────────────────────────
current_symbol   = SYMBOL
current_interval = INTERVAL
state = {
    "candles": [],
    "bids":    {},
    "asks":    {},
}
clients       = set()
lock          = threading.Lock()
main_loop     = None

position_lock    = threading.Lock()
current_position = None   # None | "short"
last_sig_idx     = -1

# ── Broadcast ─────────────────────────────────────────────────────────────────
def broadcast(msg: dict):
    if main_loop is None:
        return
    data = json.dumps(msg)
    asyncio.run_coroutine_threadsafe(_broadcast(data), main_loop)

async def _broadcast(data: str):
    dead = set()
    for ws in list(clients):
        try:
            await ws.send(data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

# ── Indicators ────────────────────────────────────────────────────────────────
def calc_ema(candles: list, length: int) -> list:
    """EMA of (high+low)/2 — matches Pine's ta.ema on hlc2."""
    k = 2 / (length + 1)
    result = []
    e = None
    for c in candles:
        v = (c["h"] + c["l"]) / 2
        e = v if e is None else v * k + e * (1 - k)
        result.append(e)
    return result

def calc_std(candles: list) -> list:
    """Population stdev of volume over LOOKBACK — matches Pine's ta.stdev."""
    vols   = [c["v"] for c in candles]
    result = [float("nan")] * len(candles)
    for i in range(LOOKBACK - 1, len(candles)):
        window = vols[i - LOOKBACK + 1 : i + 1]
        mean   = sum(window) / LOOKBACK
        var    = sum((x - mean) ** 2 for x in window) / LOOKBACK
        result[i] = math.sqrt(var)
    return result

def classify_bubble(c: dict, sv: float):
    if not SHOW_BUBBLES or math.isnan(sv) or sv == 0:
        return None

    L = LIM_FACTOR
    if   sv >= L     and sv < L + 1: tier = "A"
    elif sv >= L + 1 and sv < L + 2: tier = "B"
    elif sv >= L + 2 and sv < L + 3: tier = "C"
    elif sv >= L + 3 and sv < L + 6: tier = "D"
    elif sv >= L + 6:                 tier = "E"
    else:
        return None

    mid      = (c["h"] + c["l"]) / 2
    top_body = max(c["o"], c["c"])
    low_body = min(c["o"], c["c"])

    upper_zone = top_body <= mid <= c["h"]
    lower_zone = c["l"]   <= mid <= low_body

    if not upper_zone and not lower_zone:
        return None

    zone = "upper" if upper_zone else "lower"
    return {
        "tier":            tier,
        "zone":            zone,
        "is_up":           zone == "lower",   # lower zone = bullish absorption = GREEN bubble
        "is_dn":           zone == "upper",   # upper zone = bearish absorption = RED bubble
        "signal_eligible": tier in ("C", "D", "E"),
    }

# ── Signal logic ──────────────────────────────────────────────────────────────
def calc_sigs(candles: list, emas: dict, stds: list) -> list:
    s          = [None] * len(candles)
    open_pos   = None
    body_sizes = [abs(c["c"] - c["o"]) for c in candles]

    for i in range(1, len(candles)):
        c  = candles[i]
        pc = candles[i - 1]
        sd = stds[i]
        if not sd or math.isnan(sd) or sd == 0:
            continue

        sv = c["v"] / sd

        e8,   e9   = emas[8][i],   emas[9][i]
        e5,   e12  = emas[5][i],   emas[12][i]
        e34,  e50  = emas[34][i],  emas[50][i]
        e72,  e89  = emas[72][i],  emas[89][i]
        e180, e200 = emas[180][i], emas[200][i]

        cloud1_top = max(e8, e9)
        ema_high   = max(e9, e12, e50, e89, e200)

        start        = max(0, i - LOOKBACK + 1)
        avg_body     = sum(body_sizes[start:i + 1]) / (i - start + 1)

        prev_body      = abs(pc["c"] - pc["o"])
        prev_avg_start = max(0, (i - 1) - LOOKBACK + 1)
        prev_avg_body  = sum(body_sizes[prev_avg_start:i]) / max(1, i - prev_avg_start)

        giant_green_prev = (pc["c"] > pc["o"]) and (prev_body > prev_avg_body * 3)

        bub = classify_bubble(c, sv)

        big_green_bubble = (
            bub is not None
            and bub["tier"] in ("D", "E")
            and bub["is_up"]   # lower_zone = mid in wick below body = GREEN bubble
        )

        above_all_clouds = c["c"] > ema_high

        if (
            open_pos is None
            and giant_green_prev
            and big_green_bubble
            and above_all_clouds
        ):
            s[i]     = ("S", TRADE_AMOUNT)
            open_pos = "short"

    return s

# ── Checklist ─────────────────────────────────────────────────────────────────
def build_checklist(candles: list, emas: dict, stds: list) -> list:
    current_i = len(candles) - 2
    if current_i < 1:
        return []

    body_sizes   = [abs(x["c"] - x["o"]) for x in candles]
    window_start = max(1, current_i - CHECKLIST_LOOKBACK + 1)

    giant_green_fired_ago  = None
    bubble_fired_ago       = None
    above_clouds_fired_ago = None
    bubble_best_tier       = None
    bubble_best_sv         = 0.0

    for j in range(window_start, current_i + 1):
        c_j      = candles[j]
        pc_j     = candles[j - 1]
        bars_ago = current_i - j

        # Giant green
        prev_body_j     = abs(pc_j["c"] - pc_j["o"])
        avg_start_j     = max(0, (j - 1) - CHECKLIST_LOOKBACK + 1)
        prev_avg_body_j = sum(body_sizes[avg_start_j:j]) / max(1, j - avg_start_j)
        if (pc_j["c"] > pc_j["o"]) and (prev_body_j > prev_avg_body_j * 3):
            if giant_green_fired_ago is None or bars_ago < giant_green_fired_ago:
                giant_green_fired_ago = bars_ago

        # Bubble
        vol_win = [candles[k]["v"] for k in range(max(0, j - CHECKLIST_LOOKBACK + 1), j + 1)]
        if len(vol_win) >= 2:
            mean_v = sum(vol_win) / len(vol_win)
            var_v  = sum((x - mean_v) ** 2 for x in vol_win) / len(vol_win)
            sd_j   = math.sqrt(var_v)
            sv_j   = c_j["v"] / sd_j if sd_j else 0
        else:
            sd_j = stds[j] if not math.isnan(stds[j]) else 0
            sv_j = c_j["v"] / sd_j if sd_j else 0

        bub_j = classify_bubble(c_j, sv_j)

        if bub_j and bub_j["tier"] in ("D", "E") and bub_j["is_up"] and j<20:
            if bubble_fired_ago is None or bars_ago < bubble_fired_ago:
                bubble_fired_ago = bars_ago
            if sv_j > bubble_best_sv:
                bubble_best_sv   = sv_j
                bubble_best_tier = bub_j["tier"]

        # Above all clouds
        ema_high_j = max(emas[9][j], emas[12][j], emas[50][j], emas[89][j], emas[200][j])
        if c_j["c"] > ema_high_j:
            if above_clouds_fired_ago is None or bars_ago < above_clouds_fired_ago:
                above_clouds_fired_ago = bars_ago

    def ago_str(n):
        return "current bar" if n == 0 else f"{n} bars ago"

    c_cur  = candles[current_i]
    pc_cur = candles[current_i - 1]

    prev_body_cur     = abs(pc_cur["c"] - pc_cur["o"])
    avg_start_cur     = max(0, (current_i - 1) - CHECKLIST_LOOKBACK + 1)
    prev_avg_body_cur = sum(body_sizes[avg_start_cur:current_i]) / max(1, current_i - avg_start_cur)

    gg_detail = f"body={round(prev_body_cur, 5)} avg={round(prev_avg_body_cur, 5)}"
    if giant_green_fired_ago is not None and giant_green_fired_ago > 0:
        gg_detail += f" | last fired {ago_str(giant_green_fired_ago)}"

    tier_label = f"Tier {bubble_best_tier}" if bubble_best_tier else "No bubble"
    bub_detail = f"best sv={round(bubble_best_sv, 2)} | {tier_label}"
    if bubble_fired_ago is not None and bubble_fired_ago > 0:
        bub_detail += f" | last fired {ago_str(bubble_fired_ago)}"

    ema_high_cur = max(emas[9][current_i], emas[12][current_i], emas[50][current_i],
                       emas[89][current_i], emas[200][current_i])
    ac_detail = f"close={round(c_cur['c'], 5)} emaHigh={round(ema_high_cur, 5)}"
    if above_clouds_fired_ago is not None and above_clouds_fired_ago > 0:
        ac_detail += f" | last fired {ago_str(above_clouds_fired_ago)}"

    return [
        {
            "label":  "Giant green prev candle (3× avg body)",
            "value":  giant_green_fired_ago is not None,
            "detail": gg_detail,
        },
        {
            "label":  "Tier D/E lower zone bubble",
            "value":  bubble_fired_ago is not None,
            "detail": bub_detail,
        },
        {
            "label":  "Price above all clouds",
            "value":  above_clouds_fired_ago is not None,
            "detail": ac_detail,
        },
    ]

# ── Recompute + webhook trigger ───────────────────────────────────────────────
def recompute_and_check():
    global last_sig_idx, current_position

    with lock:
        candles = list(state["candles"])

    print(f"[check] candles={len(candles)} pos={current_position} last_sig_idx={last_sig_idx}")

    if len(candles) < CHECKLIST_LOOKBACK + 2:
        print(f"[check] not enough candles yet ({len(candles)} < {CHECKLIST_LOOKBACK + 2})")
        return

    emas      = {l: calc_ema(candles, l) for l in [5, 8, 9, 12, 34, 50, 72, 89, 180, 200]}
    stds      = calc_std(candles)
    checklist = build_checklist(candles, emas, stds)
    broadcast({"type": "checklist", "items": checklist})

    passed = [item["value"] for item in checklist]
    labels = [item["label"] for item in checklist]
    print(f"[check] checklist: {list(zip(labels, passed))}")

    i = len(candles) - 2
    if i <= last_sig_idx:
        print(f"[check] skipping — already processed idx {i}")
        return

    if not all(passed):
        print(f"[check] conditions not met, no signal")
        return

    last_sig_idx = i
    symbol = current_symbol.replace("USDT", "")

    with position_lock:
        payload = None
        if current_position != "short":
            payload = {
                "symbol":   symbol,
                "action":   "open_short",
                "amount":   TRADE_AMOUNT,
                "leverage": TRADE_LEVERAGE,
            }
            current_position = "short"
        else:
            print(f"[check] already in short, skipping")

    if payload:
        print(f"[signal] S | all 3 checklist latches active | sending webhook…")
        send_webhook(payload)

    # ── CLOSE SIGNAL / WEBHOOK DISABLED ───────────────────────────────────────
    # Close is triggered manually via the browser CLOSE SHORT button only.
    # ── END CLOSE SIGNAL / WEBHOOK DISABLED ───────────────────────────────────

def send_webhook(payload: dict):
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print(f"[webhook] {payload} → {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[webhook] error: {e}")

# ── Depth polling ─────────────────────────────────────────────────────────────
def depth_poll_loop():
    while True:
        try:
            r = requests.get(
                BITUNIX_DEPTH,
                params={"symbol": current_symbol, "limit": "max"},
                timeout=5,
            )
            data = r.json()
            if not isinstance(data, dict):
                time.sleep(2)
                continue
            raw = data.get("data") or data
            if not isinstance(raw, dict):
                time.sleep(2)
                continue

            raw_asks = raw.get("asks") or raw.get("a") or []
            raw_bids = raw.get("bids") or raw.get("b") or []

            with lock:
                state["asks"].clear()
                state["bids"].clear()
                for entry in raw_asks:
                    if isinstance(entry, list):
                        p, q = entry[0], entry[1]
                    else:
                        p = str(entry.get("p", entry.get("price", "0")))
                        q = str(entry.get("v", entry.get("volume", "0")))
                    state["asks"][p] = float(q)
                for entry in raw_bids:
                    if isinstance(entry, list):
                        p, q = entry[0], entry[1]
                    else:
                        p = str(entry.get("p", entry.get("price", "0")))
                        q = str(entry.get("v", entry.get("volume", "0")))
                    state["bids"][p] = float(q)

                sorted_asks = sorted(state["asks"].items(), key=lambda x: float(x[0]))
                sorted_bids = sorted(state["bids"].items(), key=lambda x: float(x[0]), reverse=True)

            broadcast({
                "type": "depth",
                "asks": [[p, q] for p, q in sorted_asks],
                "bids": [[p, q] for p, q in sorted_bids],
            })
        except Exception as e:
            print(f"[!] Depth poll error: {e}")
        time.sleep(1)

# ── Historical candles ────────────────────────────────────────────────────────
def fetch_history():
    """Fetch up to 200 candles of history from the REST endpoint."""
    now_ms = int(time.time() * 1000)

    # Compute start time based on interval so we always request ~200 bars
    minutes_per_bar = {
        "1min": 1, "5min": 5, "15min": 15, "30min": 30,
        "1hour": 60, "2hour": 120, "4hour": 240, "6hour": 360,
        "1day": 1440, "1week": 10080,
    }
    mins = minutes_per_bar.get(current_interval, 1)
    start = now_ms - 200 * mins * 60 * 1000

    rest_iv = REST_INTERVAL.get(current_interval, "1m")

    try:
        r = requests.get(BITUNIX_REST, params={
            "symbol":    current_symbol,
            "interval":  rest_iv,
            "startTime": start,
            "endTime":   now_ms,
            "limit":     200,
        }, timeout=10)

        data    = r.json()
        candles = []
        raw     = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(raw, dict):
            raw = raw.get("list", raw.get("klines", raw.get("data", [])))

        for item in raw:
            if isinstance(item, list):
                candles.append({
                    "t": int(item[0]),
                    "o": float(item[1]),
                    "h": float(item[2]),
                    "l": float(item[3]),
                    "c": float(item[4]),
                    "v": float(item[5]) if len(item) > 5 else 0,
                })
            elif isinstance(item, dict):
                candles.append({
                    "t": int(item.get("time", item.get("ts", item.get("t", 0)))),
                    "o": float(item.get("open",  item.get("o", 0))),
                    "h": float(item.get("high",  item.get("h", 0))),
                    "l": float(item.get("low",   item.get("l", 0))),
                    "c": float(item.get("close", item.get("c", 0))),
                    "v": float(item.get("quoteVol", item.get("b", item.get("volume", item.get("v", 0))))),
                })

        with lock:
            state["candles"] = sorted(candles, key=lambda x: x["t"])

        print(f"[+] Loaded {len(state['candles'])} candles for {current_symbol} {current_interval} (REST interval: {rest_iv})")
        if state["candles"]:
            print(f"[+] Sample volumes: {[round(c['v'], 2) for c in state['candles'][:5]]}")

    except Exception as e:
        print(f"[!] History fetch failed: {e}")
        import traceback; traceback.print_exc()

# ── Bitunix WebSocket ─────────────────────────────────────────────────────────
def on_open(ws):
    ch = WS_CHANNEL.get(current_interval, "market_kline_1min")
    print(f"[+] Connected to Bitunix WS — subscribing {current_symbol} {ch}")
    ws.send(json.dumps({
        "op":   "subscribe",
        "args": [{"symbol": current_symbol, "ch": ch}]
    }))
    def ping():
        while True:
            time.sleep(15)
            try:
                ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))
            except Exception:
                break
    threading.Thread(target=ping, daemon=True).start()

def on_message(ws, message):
    try:
        data = json.loads(message)
    except Exception:
        return

    ch = data.get("ch", "")
    if "kline" not in ch:
        return

    payload = data.get("data", {})
    if not payload:
        return

    candle = {
        "t": int(data.get("ts", data.get("t", 0))),
        "o": float(payload.get("o", 0)),
        "h": float(payload.get("h", 0)),
        "l": float(payload.get("l", 0)),
        "c": float(payload.get("c", 0)),
        "v": float(payload.get("q", 0)),
    }

    with lock:
        candles = state["candles"]
        if candles and candles[-1]["t"] == candle["t"]:
            candles[-1] = candle
            new_candle  = False
        else:
            candles.append(candle)
            if len(candles) > 500:
                candles.pop(0)
            new_candle = True

    broadcast({"type": "candle", "data": candle})
    if new_candle:
        threading.Thread(target=recompute_and_check, daemon=True).start()

def on_error(ws, error):
    print(f"[!] Bitunix WS error: {error}")

def on_close(ws, code, msg):
    print(f"[~] Bitunix WS closed: {code}")

bitunixws = None

def start_bitunix_ws():
    global bitunixws
    bitunixws = websocket.WebSocketApp(
        BITUNIX_WS,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    threading.Thread(target=bitunixws.run_forever, daemon=True).start()

def restart_bitunix_ws():
    global bitunixws
    if bitunixws:
        try:
            bitunixws.close()
        except Exception:
            pass
    time.sleep(0.5)
    start_bitunix_ws()

# ── HTTP server ───────────────────────────────────────────────────────────────
class ChartHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chart.html")
        try:
            with open(html_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"chart.html not found next to server.py")

    def log_message(self, format, *args):
        pass

def start_http_server():
    httpd = HTTPServer(("localhost", HTTP_PORT), ChartHTTPHandler)
    httpd.serve_forever()

# ── Local WebSocket server ────────────────────────────────────────────────────
async def handler(websocket):
    global current_symbol, current_interval
    clients.add(websocket)
    print(f"[+] Browser connected ({len(clients)} total)")

    with lock:
        await websocket.send(json.dumps({
            "type":     "init",
            "candles":  state["candles"],
            "asks":     sorted([[p, q] for p, q in state["asks"].items()], key=lambda x: float(x[0])),
            "bids":     sorted([[p, q] for p, q in state["bids"].items()], key=lambda x: float(x[0]), reverse=True),
            "symbol":   current_symbol,
            "interval": current_interval,
        }))

    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
                if msg.get("type") == "setInterval":
                    current_interval = msg["interval"]
                    print(f"[~] Interval -> {current_interval} (REST: {REST_INTERVAL.get(current_interval)}, WS: {WS_CHANNEL.get(current_interval)})")
                    state["candles"].clear()
                    threading.Thread(target=fetch_history, daemon=True).start()
                    restart_bitunix_ws()
                elif msg.get("type") == "setSymbol":
                    current_symbol = msg["symbol"].upper()
                    print(f"[~] Symbol -> {current_symbol}")
                    state["candles"].clear()
                    state["asks"].clear()
                    state["bids"].clear()
                    threading.Thread(target=fetch_history, daemon=True).start()
                    restart_bitunix_ws()
                    broadcast({"type": "symbolChanged", "symbol": current_symbol})
            except Exception as e:
                print(f"[!] Message error: {e}")
    except Exception:
        pass
    finally:
        clients.discard(websocket)
        print(f"[-] Browser disconnected ({len(clients)} total)")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    global main_loop
    main_loop = asyncio.get_running_loop()

    print(f"[~] Starting HTTP server on http://localhost:{HTTP_PORT}")
    threading.Thread(target=start_http_server, daemon=True).start()

    print(f"[~] Fetching historical candles for {SYMBOL}…")
    fetch_history()

    print(f"[~] Starting depth polling (REST)…")
    threading.Thread(target=depth_poll_loop, daemon=True).start()

    print(f"[~] Connecting to Bitunix WebSocket (klines)…")
    start_bitunix_ws()

    print(f"[~] WebSocket server on ws://localhost:{LOCAL_PORT}")
    print(f"[~] Open http://localhost:{HTTP_PORT} in your browser!")
    print(f"[~] Webhook target: {WEBHOOK_URL}\n")

    async with serve(handler, "localhost", LOCAL_PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
