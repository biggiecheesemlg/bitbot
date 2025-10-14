:loop
set list= XRPUSDT SUIUSDT ADAUSDT ALGOUSDT DOTUSDT LINKUSDT RENDERUSDT FETUSDT IMXUSDT AVAUSDT

for %%a in (%list%) do (
	python C:\Users\Ether\Desktop\bitbot\bitbot2.py

	python C:\Users\Ether\Desktop\bitbot\bitbot.py ^ %%a
	python C:\Users\Ether\Desktop\bitbot\bitbot3.py
	1python C:\Users\Ether\Desktop\bitbot\bitbot4.py
)
goto :loop
