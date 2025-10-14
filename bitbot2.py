import os
import sqlite3
import shutil
import psutil
import time

def close_brave():
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] and 'brave' in proc.info['name'].lower():
                print(f"Terminating: {proc.info['name']} (PID {proc.info['pid']})")
                psutil.Process(proc.info['pid']).terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

# Step 1: Close Brave
close_brave()
time.sleep(3)  # Give it a few seconds to shut down completely

# Path to Brave's modern cookie DB
cookie_db_path = os.path.expanduser(
    r"C:\Users\Ether\AppData\Local\BraveSoftware\Brave-Browser\User Data\Default\Network\Cookies"
)

# Check if file exists
if not os.path.exists(cookie_db_path):
    print("Cookie database not found.")
    exit(1)

# Optional: Backup the database
backup_path = cookie_db_path + ".bak"
shutil.copy2(cookie_db_path, backup_path)
print(f"Backup created at: {backup_path}")

# Step 2: Delete cookies (only if 'cookies' table exists)
try:
    conn = sqlite3.connect(cookie_db_path)
    cursor = conn.cursor()
    # Check if 'cookies' table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cookies';")
    if not cursor.fetchone():
        print("No 'cookies' table found in the database.")
    else:
        cursor.execute("DELETE FROM cookies")
        conn.commit()
        print("Cookies cleared successfully.")
    conn.close()
except sqlite3.OperationalError as e:
    print("Error: Could not access the cookie database.")
    print(e)
