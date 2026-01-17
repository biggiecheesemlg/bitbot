import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException

driver =webdriver.Firefox()
driver.get('https://web.telegram.org')
input("login and press enter ")



messages_xp = "//div[contains(@class, 'bubble channel-post with-beside-button hide-name is-in can-have-tail is-group-first is-group-last')]"
message_time_xp = ".//span[contains(@class,'time')]"

messages = [msg for msg in driver.find_elements('xpath',messages_xp)]


input("ready for scrape?")




def refine_text(msg_data_item):
    text = msg_data_item['text']
    
    data = {
        "symbol": None,
        "type": msg_data_item['type'],
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
            type = "CALL"
        elif emojis.count("👇") >= 3:
            type = "PUT"
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





for data in msg_data:
    print(refine_text(data))