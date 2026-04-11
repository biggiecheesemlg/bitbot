import asyncio
import websockets
import json


async def connect_clob(asset_ids: list[str]):
    uri = "wss://ws-subscriptions-frontend-clob.polymarket.com/ws/market"
    headers = {
        "Origin": "https://polymarket.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    }
    async with websockets.connect(uri, additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "markets", "assets_ids": asset_ids}))
        print(f"Subscribed to ...{asset_ids[0][-10:]} / ...{asset_ids[1][-10:]}")

        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            items = data if isinstance(data, list) else [data]
            for item in items:
                print(json.dumps(item))


async def main():
    with open("markets_cache.json") as f:
        cache = json.load(f)

    # Read clobTokenIds directly from the raw event markets
    for mkt in cache["event"]["markets"]:
        question = mkt["question"]
        ids = json.loads(mkt["clobTokenIds"])  # JSON string e.g. "[\"abc\", \"def\"]"
        print(f"\n--- {question} ---")
        try:
            await asyncio.wait_for(connect_clob(ids), timeout=10)
        except asyncio.TimeoutError:
            print("(timeout, moving on)")
        except Exception as e:
            print(f"Error: {e}")


asyncio.run(main())
