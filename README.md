Bitunix Chart Server (DOGEUSDT)
Lightweight Python server that streams market data, computes signals, and serves a browser chart.
Features
Live kline (candles) via WebSocket
REST-polled orderbook depth
EMA cloud calculations
Absorption bubble (Tier A–E) detection
Auto short signal + webhook trigger
Simple browser UI (chart.html)


Install
pip install websocket-client websockets requests
Run
python server.py


Open
http://localhost:8080


Config (top of file)
SYMBOL – trading pair (default: DOGEUSDT)
INTERVAL – timeframe (1min, 5min, etc.)
WEBHOOK_URL – signal endpoint
TRADE_AMOUNT, TRADE_LEVERAGE


How it works
Fetches ~200 historical candles (REST)
Subscribes to live candles (WebSocket)
Polls orderbook depth (REST loop)
Computes:
EMA clouds (5 → 200)
Volume std deviation
Absorption bubbles (Tier A–E)
Triggers signal when:
Giant green candle (3× avg body)
Tier D/E lower bubble
Price above all EMA clouds


Sends webhook:
{
  "symbol": "DOGE",
  "action": "open_short",
  "amount": 30,
  "leverage": 25
}

Notes
Only open short is automated and excuted with bitunix_orderbook.py

WebSocket server: ws://localhost:8765
