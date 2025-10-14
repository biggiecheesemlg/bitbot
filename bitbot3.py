import easyocr
import pyautogui
import time
import pyperclip
import webbrowser
import os
from PIL import Image
import win32clipboard
import io
from rapidfuzz import fuzz  # Better fuzzy matching
import pygetwindow as gw



# Initialize EasyOCR reader globally
reader = easyocr.Reader(['en'], gpu=False)

def copy_image_to_clipboard(image_path):
    """Copy an image to the Windows clipboard (as DIB)."""
    image = Image.open(image_path)

    output = io.BytesIO()
    image.convert('RGB').save(output, 'BMP')
    data = output.getvalue()[14:]  # Skip BMP header
    output.close()

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    win32clipboard.CloseClipboard()

def copy_all_pngs_in_dir(dir_path):
    """Copy and paste all .png images from a directory using clipboard."""
    for file_name in os.listdir(dir_path):
        if file_name.lower().endswith('.png'):
            full_path = os.path.join(dir_path, file_name)
            print(f"Copying: {full_path}")
            copy_image_to_clipboard(full_path)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(1)  # Pause to allow paste before next image

def find_text_easyocr(text_to_find, region=None, click=True, similarity_threshold=0.6, debug=False):
    """
    Find text on the screen using EasyOCR with fuzzy matching.
    
    :param text_to_find: The target string to search for.
    :param region: Tuple (left, top, width, height) or None for full screen.
    :param click: Whether to click on the matched text.
    :param similarity_threshold: Minimum similarity ratio (0 to 1).
    :param debug: Whether to print all OCR results.
    :return: (x, y, matched_text, similarity_score) or None if not found.
    """
    screenshot = pyautogui.screenshot(region=region)
    screenshot_path = "temp.png"
    screenshot.save(screenshot_path)

    results = reader.readtext(screenshot_path)
    os.remove(screenshot_path)

    if debug:
        for (_, text, prob) in results:
            print(f"OCR: '{text}' with confidence {prob:.2f}")

    best_match = None
    best_score = 0

    for (bbox, text, prob) in results:
        score = fuzz.ratio(text.lower(), text_to_find.lower()) / 100.0
        if score > best_score:
            best_score = score
            best_match = (bbox, text, prob, score)

    if best_match and best_score >= similarity_threshold:
        bbox, text, prob, score = best_match
        center_x = int((bbox[0][0] + bbox[2][0]) / 2)
        center_y = int((bbox[0][1] + bbox[2][1]) / 2)

        if region:
            center_x += region[0]
            center_y += region[1]

        print(f"[MATCH] '{text}' ≈ '{text_to_find}' (score: {score:.2f}, prob: {prob:.2f}) at ({center_x}, {center_y})")

        if click:
            pyautogui.moveTo(center_x, center_y, duration=0.2)
            pyautogui.click()

        return center_x, center_y, text, score

    print(f"[NO MATCH] Closest match for '{text_to_find}' had score {best_score:.2f}")
    return None


# ========== Main Script Starts ==========

# Step 1: Open ChatGPT (or any URL)
webbrowser.open("https://chatgpt.com/")
time.sleep(3)
windows = gw.getAllTitles()

win = gw.getWindowsWithTitle('ChatGPT - Brave')[0]  # Replace with your window title
win.maximize()  # Maximizes the window



# Step 2: OCR search and click
find_text_easyocr("Stay logged out", debug=True)

# Step 3: Read from prompt.txt and copy to clipboard
with open(r'C:\Users\Ether\Desktop\bitbot\prompt.txt', 'r', encoding='utf-8') as file:
    text = file.read()

pyperclip.copy(text)
print("[INFO] Text copied to clipboard.")

# Step 4: Type the text into the browser
pyautogui.write(text)
time.sleep(5)

# Step 5: Copy and paste all screenshots (images)
copy_all_pngs_in_dir(r"C:\Users\Ether\Desktop\bitbot\Screenshots")

# Step 6: Send input with Ctrl+Enter
time.sleep(10)

pyautogui.hotkey('ctrl', 'enter')
time.sleep(20)
pyautogui.scroll(-5000)

find_text_easyocr("Copy ", debug=True)

time.sleep(40)


# ========== End ==========
