# ===========================================================
# BITUNIX BOT TEMPLATE — With Working Slider Drag (SAFE MODE)
# ===========================================================

from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException

import time
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
app = Flask(__name__)

driver = None
wait = None


# -----------------------------------------
# Helper: wait for CSS selector
# -----------------------------------------
def wait_css(selector, text=None, timeout=20):
    def condition(d):
        els = d.find_elements(By.CSS_SELECTOR, selector)
        if not els:
            return False
        if text:
            for e in els:
                if text in e.text.strip():
                    return e
            return False
        return els[0]
    return WebDriverWait(driver, timeout).until(condition)

def wait_xpath(xpath, timeout=20):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )








def coin_name(name):
    try:
        search_btn = wait_xpath("//button[contains(@class,'cursor-pointer') and .//span[contains(.,'USDT')]]")
        search_btn.click()

        search_input = wait_xpath("//input[contains(@class,'arco-input arco-input-size-large')]")
        search_input.clear()
        search_input.send_keys(name + "USDT")

        first_result = wait_xpath("//div[contains(@class,'arco-list-item')][1]", timeout=8)
        first_result.click()
        return True

    except TimeoutException:
        print(f"[COIN] {name} not found or UI not ready")
        return False

#ajust leverage
def ajust_leverage():
    leverage_button = wait_xpath("//div[contains(@class,'flex items-center justify-center fs-12 color-text-1 h30 flex-1 cursor-pointer fm-medium gap-4')]")
    leverage_button.click()
    Ajust_Simultaneously = wait_css("label.toggle")
    time.sleep(2)

    if not Ajust_Simultaneously.is_selected():
        Ajust_Simultaneously.click()


    # Wait for the element

    # Wait for the input element
    # Wait for the leverage input
    leverage_input = wait_css("div.leverage-input input.arco-input")

    # Click to focus
    leverage_input.click()

    # Select all text and overwrite
    leverage_input.send_keys(Keys.CONTROL, 'a')  # Ctrl+A to select all
    leverage_input.send_keys("25")  # type new leverage


    confirm_btn = wait_css("button.arco-btn-primary", text="Confirm")
    confirm_btn.click()



# -----------------------------------------
# Switch tabs
# -----------------------------------------
def click_tab(name):
    el = wait_css(
        "div.flex-1.align-center.cursor-pointer.text-center.p-t6.p-b6.fm-medium",
        name
    )
    bg = el.value_of_css_property("background-color")
    if "rgb(36, 193, 141)" not in bg:
        el.click()
        time.sleep(0.8)
    print("[TAB]", name)


# -----------------------------------------
# Enter amount into input box
# -----------------------------------------
def enter_amount(v):
    field = wait_css(
        "input.arco-input[placeholder*='Quantity'], input.arco-input[placeholder*='Cost']",
        timeout=10
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
    driver.execute_script("arguments[0].value='';", field)
    driver.execute_script(f"arguments[0].value='{v}';", field)
    driver.execute_script("""
        arguments[0].dispatchEvent(new Event('input', {bubbles:true}));
        arguments[0].dispatchEvent(new Event('change', {bubbles:true}));
    """, field)
    print("[AMOUNT]", v)


# -----------------------------------------
# REAL POINTER-EVENT SLIDER DRAG (WORKING)
# -----------------------------------------
def slide_close_js(percentage):
    try:
        handle = wait_xpath("//div[contains(@class, 'arco-slider-btn')]")
        track = wait_xpath("//div[contains(@class, 'arco-slider-track')]")
        rect = driver.execute_script("""
        const r = arguments[0].getBoundingClientRect();
        return {left:r.left, width:r.width, top:r.top, height:r.height};
        """, track)
        start_x = rect["left"] + 10
        end_x = rect["left"] + rect["width"] * max(0, min(100, percentage)) / 100
        y = rect["top"] + rect["height"] / 2
        driver.execute_script("""
        const h = arguments[0];
        const sx = arguments[1], sy = arguments[2], ex = arguments[3];
        function fire(type, x, y){
        h.dispatchEvent(new MouseEvent(type, {
        bubbles:true, cancelable:true,
        clientX:x, clientY:y,
        buttons:1
                }));
            }
        fire('mousedown', sx, sy);
        fire('mousemove', ex, sy);
        fire('mouseup', ex, sy);
        """, handle, start_x, y, end_x)
        print(f"[SLIDER] Dragged → {percentage}%")
        time.sleep(0.2)
    except Exception as e:
        print(f"[SLIDER ERROR] {e}")



# -----------------------------------------
# Click trade button (SAFE ENCAPSULATED)
# -----------------------------------------
def click_trade_button(label):
    btn_label = wait_css("button.arco-btn div.fm-bold", label, timeout=10)
    btn = btn_label.find_element(By.XPATH, "./ancestor::button")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    print("[TRADE BUTTON LOCATED]", label)
    # ============================
    #  SAFE MODE: ACTION REMOVED
    #  (Insert your actual click below)
    # ============================
    btn.click()
    return True


# -----------------------------------------
# Execute trade (SAFE TEMPLATE)
# -----------------------------------------
def execute_trade(action, amount=None):
    print("\nEXEC:", action, amount or "FULL")
    if action not in ["open_long", "open_short", "close_long", "close_short"]:
        print("[ERROR] Bad action.")
        return
    # switch tab
    click_tab("Open" if "open" in action else "Close")
    time.sleep(0.6)
    # OPEN = enter amount
    if "open" in action:
        if amount is None:
            print("[SAFE] No default amount.")
        else:
            slide_close_js(amount)
    # CLOSE = slide 100%
    else:
        slide_close_js(100)
        time.sleep(0.2)
    click_trade_button(
        {"open_long": "Open long",
         "open_short": "Open short",
         "close_long": "Close long",
         "close_short": "Close short"}[action]
    )
    # reset slider after close
    if "close" in action:
        time.sleep(2)
        slide_close_js(0)


# -----------------------------------------
# Webhook
# -----------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    coin = data.get("symbol")
    action = data.get("action")
    #amount = data.get("amount")
    coin_name(coin)
    ajust_leverage()
    time.sleep(1.2)
    execute_trade(action, 15)
    time.sleep(6)
    driver.get("https://www.bitunix.com")




# -----------------------------------------
# Main
# -----------------------------------------
if __name__ == "__main__":

    options = Options()
    options.binary_location = "/usr/bin/firefox"  # path to your installed Firefox
    wait = WebDriverWait(driver, 20)
    service = Service(executable_path="/usr/local/bin/geckodriver")

    driver = webdriver.Firefox(service=service, options=options)
    driver.get("https://www.bitunix.com")
    input("\nLogin → Open panel visible → Press ENTER\n")

    print("Listening...")
    app.run(host="0.0.0.0", port=5000, threaded=False, use_reloader=False)

