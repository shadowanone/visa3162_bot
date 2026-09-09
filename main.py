import os
import time
import json
import random
import pickle
import logging
import requests
import threading

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# أسماء الملفات المحفوظة
CONFIG_FILE = "config.json"
COOKIES_FILE = "bls_cookies.pkl"
SCREENSHOT_FILE = "bls_appointment.png"

data_lock = threading.Lock()

# الإعدادات الافتراضية
DEFAULT_CONFIG = {
    "is_running": False,
    "logs": [],
    "bot_token": "8852242734:AAEfwhcKbUFsixdp_uoRCpi_f64-5YloYPY",
    "chat_id": "8080040850",  # سيتم قبول الأوامر فقط من هذا المعرف للأمان
    "login_url": "https://algeria.blsspainvisa.com/algiers/",
    "app_url": "https://algeria.blsspainvisa.com/algiers/book-appointment",
    "email": "your_email@example.com",
    "password": "your_password",
    "headless": False,
    "check_interval_min": 180,
    "check_interval_max": 300
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                DEFAULT_CONFIG.update(saved)
        except Exception as e:
            print(f"خطأ في قراءة ملف الإعدادات: {e}")
    DEFAULT_CONFIG["is_running"] = False
    return DEFAULT_CONFIG

bot_status = load_config()

def save_config_to_file():
    with data_lock:
        to_save = {k: v for k, v in bot_status.items() if k not in ["is_running", "logs"]}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=4)

def add_log(message):
    timestamp = time.strftime("[%H:%M:%S] ")
    with data_lock:
        bot_status["logs"].append(timestamp + message)
        if len(bot_status["logs"]) > 100:
            bot_status["logs"].pop(0)

# --- إرسال رسائل وتغيير الواجهات في التلغرام ---

def send_telegram(msg, image_path=None, show_keyboard=True):
    """إرسال رسائل أو صور للتلغرام مع لوحة الأزرار."""
    token = bot_status.get("bot_token")
    chat_id = bot_status.get("chat_id")

    if not token or not chat_id:
        print("⚠️ بيانات التلغرام غير مكتملة.")
        return False

    # لوحة الأزرار التفاعلية أسفل المحادثة
    keyboard = {
        "keyboard": [
            [{"text": "▶️ بدء المراقبة"}, {"text": "⏹️ إيقاف المراقبة"}],
            [{"text": "⚡ فحص فوري الآن"}, {"text": "📊 حالة البوت والسجلات"}]
        ],
        "resize_keyboard": True,
        "persistent": True
    } if show_keyboard else None

    try:
        if image_path and os.path.exists(image_path):
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(image_path, "rb") as photo:
                payload = {"chat_id": chat_id, "caption": msg, "parse_mode": "Markdown"}
                if keyboard: payload["reply_markup"] = json.dumps(keyboard)
                res = requests.post(url, data=payload, files={"photo": photo}, timeout=15)
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
            if keyboard: payload["reply_markup"] = json.dumps(keyboard)
            res = requests.post(url, json=payload, timeout=10)
            
        return res.status_code == 200
    except Exception as e:
        add_log(f"خطأ إرسال تلغرام: {e}")
        return False

# --- محرك البحث وفتح المتصفح ---

def get_chromedriver():
    options = uc.ChromeOptions()
    if bot_status.get("headless", False):
        options.add_argument('--headless=new')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
    
    driver = uc.Chrome(options=options)
    driver.set_page_load_timeout(40)
    return driver

def run_visa_check():
    driver = None
    try:
        add_log("🔍 جاري فحص موقع BLS Spain...")
        driver = get_chromedriver()
        wait = WebDriverWait(driver, 20)

        driver.get(bot_status["login_url"])
        time.sleep(3)

        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, "rb") as f:
                    for c in pickle.load(f):
                        driver.add_cookie(c)
                driver.refresh()
                time.sleep(3)
                add_log("تم استعادة الجلسة بنجاح.")
            except Exception as e:
                add_log(f"خطأ في استعادة الكوكيز: {e}")

        driver.get(bot_status["app_url"])
        time.sleep(5)

        try:
            with open(COOKIES_FILE, "wb") as f:
                pickle.dump(driver.get_cookies(), f)
        except Exception:
            pass

        available_slots = driver.find_elements(By.XPATH, "//td[contains(@class, 'day') and not(contains(@class, 'disabled'))]")
        if not available_slots:
            available_slots = driver.find_elements(By.CLASS_NAME, "available-slot")

        if len(available_slots) > 0:
            msg = f"🚨 *تم العثور على مواعيد متاحة!*\nالعدد: {len(available_slots)}\nالرابط: {bot_status['app_url']}"
            add_log(msg)
            
            driver.save_screenshot(SCREENSHOT_FILE)
            send_telegram(msg, image_path=SCREENSHOT_FILE)
        else:
            add_log("لا توجد مواعيد متاحة حالياً.")

    except Exception as e:
        add_log(f"حدث خطأ أثناء الفحص: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def bot_loop():
    send_telegram("🚀 *تم تفعيل المراقبة التلقائية للمواعيد!*")
    
    while bot_status["is_running"]:
        run_visa_check()
        
        if not bot_status["is_running"]:
            break
            
        delay = random.uniform(bot_status.get("check_interval_min", 180), bot_status.get("check_interval_max", 300))
        add_log(f"انتظار {int(delay)} ثانية للجولة القادمة...")
        
        for _ in range(int(delay)):
            if not bot_status["is_running"]:
                break
            time.sleep(1)

    send_telegram("🛑 *تم إيقاف المراقبة التلقائية.*")

# --- الاستماع لأوامر التلغرام (Telegram Listener) ---

def handle_command(text, sender_id):
    """معالجة الأوامر الواردة من التلغرام."""
    # التأكد من أن الأمر قادم من صاحب البوت المصرح له فقط
    if str(sender_id) != str(bot_status["chat_id"]):
        print(f"محاولة وصول غير مصرح بها من Chat ID: {sender_id}")
        return

    text = text.strip()

    if text in ["/start", "أهلا", "مرحبا"]:
        send_telegram("👋 *مرحباً بك في بوت مراقبة مواعيد BLS Spain!*\nاختر إجراءً من الأزرار أدناه:")

    elif text in ["▶️ بدء المراقبة", "/start_bot"]:
        if not bot_status["is_running"]:
            bot_status["is_running"] = True
            threading.Thread(target=bot_loop, daemon=True).start()
        else:
            send_telegram("⚠️ البوت يعمل بالفعل ويراقب المواعيد حالياً.")

    elif text in ["⏹️ إيقاف المراقبة", "/stop_bot"]:
        if bot_status["is_running"]:
            bot_status["is_running"] = False
            send_telegram("⏳ جاري إيقاف المراقبة...")
        else:
            send_telegram("⚠️ البوت متوقف بالفعل.")

    elif text in ["⚡ فحص فوري الآن", "/check"]:
        send_telegram("⚡ جاري تنفيذ فحص فوري الآن...")
        threading.Thread(target=run_visa_check, daemon=True).start()

    elif text in ["📊 حالة البوت والسجلات", "/status"]:
        status_txt = "✅ يعمل ويراقب" if bot_status["is_running"] else "🛑 متوقف"
        recent_logs = "\n".join(bot_status["logs"][-6:]) if bot_status["logs"] else "لا توجد سجلات بعد."
        
        msg = f"📌 *حالة البوت:* {status_txt}\n\n📝 *آخر السجلات:*\n```\n{recent_logs}\n```"
        send_telegram(msg)

def listen_telegram_updates():
    """الاستماع للرسائل الواردة عبر Telegram Long-Polling."""
    token = bot_status["bot_token"]
    offset = 0
    print("🤖 بدأ البوت بالاستماع لأوامر التلغرام...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20"
            res = requests.get(url, timeout=25).json()

            if res.get("ok"):
                for update in res.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "")
                    sender_id = message.get("chat", {}).get("id")

                    if text and sender_id:
                        handle_command(text, sender_id)

        except Exception as e:
            time.sleep(3)

if __name__ == '__main__':
    # إرسال أزرار التحكم فور تشغيل السكريبت
    send_telegram("🤖 *تم تشغيل السكريبت بنجاح!* استخدم الأزرار أدناه للتحكم:")
    
    # تشغيل الاستماع في الخيط الرئيسي
    listen_telegram_updates()
