# append_clipboard_once.py
import pyperclip
from datetime import datetime

LOG_FILE = r"C:\Users\Ether\Desktop\bitbot\Data.txt"
INCLUDE_TIMESTAMP = True

def append_now():
    text = pyperclip.paste()
    if not text:
        print("Clipboard is empty.")
        return
    timestamp = f"[{datetime.now().isoformat(sep=' ', timespec='seconds')}] " if INCLUDE_TIMESTAMP else ""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(timestamp + text + "\n")
    print("Appended clipboard to", LOG_FILE)

if __name__ == "__main__":
    append_now()
