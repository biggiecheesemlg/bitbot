# ===========================================================
# BITUNIX BOT — Webhook Server
# ===========================================================

from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
from selenium.common.exceptions import TimeoutException
import time

app = Flask(__name__)
CORS(app)

driver = None

# -----------------------------------------
# Dismiss any blocking modal
# -----------------------------------------
def dismiss_modal():
    try:
        modals = driver.find_elements(By.CSS_SELECTOR, ".arco-modal-wrapper")
        for modal in modals:
            if modal.is_displayed():
                # try confirm/ok button first
                btns = modal.find_elements(By.CSS_SELECTOR, ".arco-btn-primary")
                for btn in btns:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.5)
                        print("[MODAL] dismissed via primary btn")
                        return
                # try close X button
                close_btns = modal.find_elements(By.CSS_SELECTOR, ".arco-modal-close-btn")
                for btn in close_btns:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.5)
                        print("[MODAL] dismissed via close btn")
                        return
                # last resort — click outside modal
                driver.execute_script("""
                    document.querySelector('.arco-modal-wrapper').style.display='none';
                """)
                time.sleep(0.3)
                print("[MODAL] force hidden")
    except Exception as e:
        print(f"[MODAL] no modal or error: {e}")

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

# -----------------------------------------
# Switch coin
# -----------------------------------------
def coin_name(name):
    try:
        dismiss_modal()

        search_btn = wait_xpath(
            "//button[contains(@class,'cursor-pointer') and .//span[contains(.,'USDT')]]"
        )
        driver.execute_script("arguments[0].click();", search_btn)
        time.sleep(0.5)

        search_input = wait_xpath("//input[contains(@class,'arco-input arco-input-size-large')]")
        search_input.clear()
        search_input.send_keys(name + "USDT")
        time.sleep(0.5)

        first_result = wait_xpath("//div[contains(@class,'arco-list-item')][1]", timeout=8)
        driver.execute_script("arguments[0].click();", first_result)
        time.sleep(0.5)
        print(f"[COIN] switched to {name}")
        return True
    except TimeoutException:
        print(f"[COIN] {name} not found or UI not ready")
        return False
    except Exception as e:
        print(f"[COIN ERROR] {e}")
        return False

# -----------------------------------------
# Adjust leverage
# -----------------------------------------
def ajust_leverage(leverage=25):
    try:
        dismiss_modal()
        time.sleep(0.5)

        leverage_button = wait_xpath(
            "//div[contains(@class,'flex items-center justify-center fs-12 color-text-1 h30 flex-1 cursor-pointer fm-medium gap-4')]"
        )
        driver.execute_script("arguments[0].click();", leverage_button)
        time.sleep(1)

        # Toggle simultaneously
        try:
            toggle_input = wait_css("label.toggle input[type='checkbox']", timeout=5)
            is_checked = driver.execute_script("return arguments[0].checked;", toggle_input)
            if not is_checked:
                toggle_label = wait_css("label.toggle", timeout=5)
                driver.execute_script("arguments[0].click();", toggle_label)
                time.sleep(0.5)
                is_checked = driver.execute_script("return arguments[0].checked;", toggle_input)
                print(f"[TOGGLE] checked={is_checked}")
        except Exception as e:
            print(f"[TOGGLE] skipped: {e}")

        # Set leverage value
        leverage_input = wait_css("div.leverage-input input.arco-input", timeout=10)
        driver.execute_script("arguments[0].click();", leverage_input)
        time.sleep(0.3)
        leverage_input.send_keys(Keys.CONTROL, 'a')
        leverage_input.send_keys(Keys.DELETE)
        leverage_input.send_keys(str(leverage))
        time.sleep(0.3)

        # Confirm
        confirm_btn = wait_xpath(
            "//button[contains(@class,'arco-btn-primary') and normalize-space()='Confirm']"
        )
        driver.execute_script("arguments[0].click();", confirm_btn)
        time.sleep(0.5)
        print(f"[LEVERAGE] set to {leverage}x")

    except Exception as e:
        print(f"[LEVERAGE ERROR] {e}")

# -----------------------------------------
# Switch tabs
# -----------------------------------------
def click_tab(name):
    try:
        el = wait_css(
            "div.flex-1.align-center.cursor-pointer.text-center.p-t6.p-b6.fm-medium",
            name
        )
        bg = el.value_of_css_property("background-color")
        if "rgb(36, 193, 141)" not in bg:
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.8)
        print("[TAB]", name)
    except Exception as e:
        print(f"[TAB ERROR] {e}")

# -----------------------------------------
# Slider drag
# -----------------------------------------
def slide_close_js(percentage):
    try:
        handle = wait_xpath("//div[contains(@class, 'arco-slider-btn')]")
        track  = wait_xpath("//div[contains(@class, 'arco-slider-track')]")

        rect = driver.execute_script("""
            const r = arguments[0].getBoundingClientRect();
            return {left:r.left, width:r.width, top:r.top, height:r.height};
        """, track)

        start_x = rect["left"] + 10
        end_x   = rect["left"] + rect["width"] * max(0, min(100, percentage)) / 100
        y       = rect["top"] + rect["height"] / 2

        driver.execute_script("""
            const h = arguments[0];
            const sx = arguments[1], sy = arguments[2], ex = arguments[3];
            function fire(type, x, y){
                h.dispatchEvent(new MouseEvent(type, {
                    bubbles:true, cancelable:true,
                    clientX:x, clientY:y, buttons:1
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
# Click trade button
# -----------------------------------------
def click_trade_button(label):
    try:
        btn_label = wait_css("button.arco-btn div.fm-bold", label, timeout=10)
        btn = btn_label.find_element(By.XPATH, "./ancestor::button")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        print("[TRADE BUTTON]", label)
        return True
    except Exception as e:
        print(f"[TRADE BUTTON ERROR] {e}")
        return False

# -----------------------------------------
# Execute trade
# -----------------------------------------
def execute_trade(action, amount=None):
    print("\nEXEC:", action, amount or "FULL")

    if action not in ["open_long", "open_short", "close_long", "close_short"]:
        print("[ERROR] Bad action.")
        return

    dismiss_modal()
    click_tab("Open" if "open" in action else "Close")
    time.sleep(0.6)

    if "open" in action:
        if amount is not None:
            slide_close_js(amount)
    else:
        slide_close_js(100)
        time.sleep(0.2)

    click_trade_button({
        "open_long":   "Open long",
        "open_short":  "Open short",
        "close_long":  "Close long",
        "close_short": "Close short"
    }[action])

    if "close" in action:
        time.sleep(2)
        slide_close_js(0)

# -----------------------------------------
# Webhook
# -----------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data     = request.get_json(force=True)
        coin     = data.get("symbol")
        action   = data.get("action")
        amount   = data.get("amount", 15)
        leverage = data.get("leverage", 25)

        print(f"[webhook] {action} {coin} amount={amount} leverage={leverage}")

        dismiss_modal()
        coin_name(coin)
        ajust_leverage(leverage)
        time.sleep(1.2)
        execute_trade(action, amount)
        time.sleep(6)

        driver.get("https://www.bitunix.com/futures-trade/BTCUSDT")

        return jsonify({"status": "ok", "action": action, "symbol": coin,
                        "amount": amount, "leverage": leverage})

    except Exception as e:
        print(f"[webhook error] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# -----------------------------------------
# Main
# -----------------------------------------
if __name__ == "__main__":
    driver = webdriver.Firefox()
    driver.get("https://www.bitunix.com/futures-trade/BTCUSDT")
    input("\nLogin → Open futures panel → Press ENTER\n")
    print("Listening...")
    app.run(host="0.0.0.0", port=5000, threaded=False, use_reloader=False)
