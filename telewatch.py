import json
import os
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
import requests
import time


driver =webdriver.Firefox()
driver.get('https://web.telegram.org')


FILE_PATH = "trades.json"

def load_trades():
    if not os.path.exists(FILE_PATH):
        return []
    try:
        with open(FILE_PATH, "r") as f:
            content = f.read().strip()
            if not content:
                return []
            return json.loads(content)
    except json.JSONDecodeError:
        return []
    



def save_trades(trades):
    with open(FILE_PATH, "w") as f:
        json.dump(trades, f, indent=2)


input("ready for scrape?")



messages_xp = "//div[contains(@class, 'bubble channel-post with-beside-button hide-name is-in can-have-tail is-group-first is-group-last')]"
message_time_xp = ".//span[contains(@class,'time')]"







def refine_text(msg_data_item):
    text = msg_data_item['text']
    
    data = {
        "symbol": None,
        "action": msg_data_item['type'],
        "sl": None,
        "tp": [],
        "time":msg_data_item["time"]
    }
    # SYMBOL
    m = re.search(r"\$([A-Z0-9]+)", text)
    if m:
        data["symbol"] = m.group(1)
    # STOP LOSS
    m = re.search(r"\bSL[:\s]*([\d.]+)", text, re.IGNORECASE)
    if m:
        data["sl"] = float(m.group(1))
    # TAKE PROFITS
    m = re.search(r"\bTP[:\s]*([0-9.\s\-]+)", text, re.IGNORECASE)
    if m:
        data["tp"] = [float(x) for x in re.split(r"\s*-\s*", m.group(1))]
    # RISK NOTE
    return data

while True:
    messages = [msg for msg in driver.find_elements('xpath',messages_xp)]
    msg_data = []
    for idx, msg in enumerate(messages):
        try:
            # time
            time_el = msg.find_element(By.XPATH, message_time_xp).text
            m = re.search(r"(\d{1,2}:\d{2})$", time_el.strip())
            msg_time = m.group(1) if m else time_el.strip()
            # emojis
            emoji_elements = msg.find_elements(
                By.CSS_SELECTOR,
                "custom-emoji-element[data-sticker-emoji]"
            )
            emojis = [e.get_attribute("data-sticker-emoji") for e in emoji_elements]
            # message text
            text = msg.text
            # determine CALL or PUT inline
            if emojis.count("🚀") >= 3:
                type = "open_long"
            elif emojis.count("👇") >= 3:
                type = "open_short"
            else:
                type = None
            # store everything
            msg_data.append({
                "idx": idx,
                "time": msg_time,
                "type": type,
                "text": text
            })
        except StaleElementReferenceException:
            continue





    refined = [refine_text(refine) for refine in msg_data]


    for trade in refined:
        if trade["symbol"] and trade["action"]:
            payload = json.dumps(trade)
            curl_cmd = f"curl -X POST {'http://127.0.0.1:5000/webhook'} -H 'Content-Type: application/json' -d '{payload}'"
            print(curl_cmd)



    def trade_key(t):
        return (t["symbol"], t["action"], t["time"])


    existing_trades = load_trades()
    existing_keys = {trade_key(t) for t in existing_trades}



    url = "http://127.0.0.1:5000/webhook"

    def post_json(data):
        if trade.get("symbol") and trade.get("action"):
            resp = requests.post(url, json=trade)
            print(resp.status_code, resp.text)





    for trade in refined:
        if not trade["symbol"] or not trade["action"]:
            continue
        if trade_key(trade) not in existing_keys:
            existing_trades.append(trade)
            existing_keys.add(trade_key(trade))
            post_json(trade)
        else:
            print("Duplicate trade skipped")

    save_trades(existing_trades)
    time.sleep(60)




