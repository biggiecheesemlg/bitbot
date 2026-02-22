posts to web hook should be in this json format


'{"symbol": "BTC", "action": "open_long"}'

try it out with:
curl -X POST http://127.0.0.1:5000/webhook -H "Content-Type: application/json" -d '{"symbol": "BTC", "action": "open_long"}'

youll need to run a gnrok server if you want to send it over the internet like when using tradingview.


bugs:
1.for some reason it only works when your on the main page: https://www.bitunix.com~
i made it where it revert back there after the trade is placed but thats a temperary fix
 
2.Has vars set for setting leverage and amount per trade but should honestly be placed by the webhook~
vars are on lines 228 for setting the percentage slider, and ig there isnt really a param for the leverage ajuster but thats set
on line 91



