# Polymarket BTC April 2026 — Pro Pipeline

## Architecture

```
Gamma API ──► clobTokenIds ──► WebSocket ──► SQLite/Postgres ──► Dash Dashboard
   Step 1         Step 2         Step 3          Step 4              Bonus
```

## Quick Start

```bash
pip install -r requirements.txt

# Step 1 & 2: Pull event + extract token IDs → markets_cache.json
python step1_gamma_pull.py

# Step 3 & 4: Subscribe WebSocket + stream to DB (keep running)
python step3_4_websocket_stream.py

# Dashboard (separate terminal)
python dashboard.py
# Open http://localhost:8050
```

## Files

| File | Purpose |
|------|---------|
| `step1_gamma_pull.py` | Gamma API → extract all 20 outcome token IDs |
| `step3_4_websocket_stream.py` | WS subscribe → book/trade/price → SQLite + scanner |
| `dashboard.py` | Dash app: heatmap, spreads, price lines, arb alerts |
| `requirements.txt` | Dependencies |

## Postgres (Production)

The `POSTGRES_SCHEMA` string in `step3_4_websocket_stream.py` has the
full DDL. Replace the `sqlite3` calls with `asyncpg` for production:

```python
import asyncpg
pool = await asyncpg.create_pool("postgresql://user:pass@host/polymarket")
```

## Scanner Logic

**YES/NO Arbitrage:**
- `YES_bid + NO_bid < 0.97` → buy both, guaranteed $1 payout for <$0.97 cost
- `YES_ask + NO_ask > 1.03` → sell both, receive >$1.03 for $1 guaranteed loss

**Mispricing:**
- `|YES_mid + NO_mid - 1.0| > 0.04` → sum deviates >4¢ from $1 = wide spread/illiquid

## API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `https://gamma-api.polymarket.com/events?slug=...` | Event + market metadata |
| `https://clob.polymarket.com/order-book/{token_id}` | REST order book |
| `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Live stream |

## Rate Limits

| API | Limit |
|-----|-------|
| Gamma /events | 500 req / 10s |
| Gamma /markets | 300 req / 10s |
| CLOB REST | ~1000 req / 10s |
| WebSocket | No polling needed |
