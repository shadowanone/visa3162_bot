import os
import time
import json
import random
import pickle
import logging
import requests
import threading
from flask import Flask, render_template_string, jsonify, request

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

# أسماء الملفات المحلية
CONFIG_FILE = "config.json"
COOKIES_FILE = "bls_cookies.pkl"
SCREENSHOT_FILE = "bls_appointment.png"

# قفل التزامن بين الخيوط
data_lock = threading.Lock()

# الإعدادات الافتراضية الخاصة بـ BLS Spain - الجزائر
DEFAULT_CONFIG = {
    "is_running": False,
    "logs": [],
    "bot_token": "8852242734:AAEfwhcKbUFsixdp_uoRCpi_f64-5YloYPY",
    "chat_id": "8080040850",
    "login_url": "https://algeria.blsspainvisa.com/algiers/",
    "app_url": "https://algeria.blsspainvisa.com/algiers/book-appointment",
    "email": "your_email@example.com",
    "password": "your_password",
    "headless": False,  # يُوصى بـ False لموقع BLS لتجاوز Cloudflare
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

def send_telegram(msg, image_path=None):
    token = bot_status.get("bot_token")
    chat_id = bot_status.get("chat_id")

    if not token or not chat_id:
        add_log("⚠️ بيانات التلغرام غير مكتملة.")
        return False

    try:
        if image_path and os.path.exists(image_path):
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(image_path, "rb") as photo:
                res = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": msg, "parse_mode": "Markdown"},
                    files={"photo": photo},
                    timeout=15
                )
        else:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            res = requests.post(
                url,
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10
            )
        return res.status_code == 200
    except Exception as e:
        add_log(f"خطأ إرسال تلغرام: {e}")
        return False

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
        add_log("🔍 جاري فتح موقع BLS Spain (الجزائر)...")
        driver = get_chromedriver()
        wait = WebDriverWait(driver, 20)

        # 1. فتح الصفحة الرئيسية
        driver.get(bot_status["login_url"])
        time.sleep(3)

        # تطبيق الكوكيز السابقة إن وجدت
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, "rb") as f:
                    for c in pickle.load(f):
                        driver.add_cookie(c)
                driver.refresh()
                time.sleep(3)
                add_log("تم تحميل الجلسة المحفوظة عبر الكوكيز.")
            except Exception as e:
                add_log(f"تعذر استعادة الكوكيز: {e}")

        # 2. الانتقال إلى صفحة المواعيد
        driver.get(bot_status["app_url"])
        time.sleep(5)

        # حفظ الكوكيز الحالية لاستمرار الجلسة
        try:
            with open(COOKIES_FILE, "wb") as f:
                pickle.dump(driver.get_cookies(), f)
        except Exception:
            pass

        # 3. فحص خانات المواعيد المتاحة
        # البحث عن عناصر المواعيد المتاحة أو خانات الاختيار النشطة في BLS
        available_slots = driver.find_elements(By.XPATH, "//td[contains(@class, 'day') and not(contains(@class, 'disabled'))]")
        if not available_slots:
            available_slots = driver.find_elements(By.CLASS_NAME, "available-slot")

        if len(available_slots) > 0:
            msg = f"🚨 *تم العثور على مواعيد متاحة في BLS Spain (الجزائر)!*\nعدد الخانات المتاحة: {len(available_slots)}\nرابط الموقع: {bot_status['app_url']}"
            add_log(msg)
            
            # التقاط صورة وإرسالها تلغرام
            driver.save_screenshot(SCREENSHOT_FILE)
            send_telegram(msg, image_path=SCREENSHOT_FILE)
        else:
            add_log("لا توجد مواعيد متاحة حالياً على موقع BLS.")

    except Exception as e:
        add_log(f"حدث خطأ أثناء الفحص: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def bot_loop():
    send_telegram("🌐 *تم تشغيل البوت لمراقبة مواعيد BLS Spain (الجزائر)!*")
    
    while bot_status["is_running"]:
        run_visa_check()
        
        if not bot_status["is_running"]:
            break
            
        delay = random.uniform(bot_status.get("check_interval_min", 180), bot_status.get("check_interval_max", 300))
        add_log(f"انتظار {int(delay)} ثانية حتى الجولة القادمة...")
        
        for _ in range(int(delay)):
            if not bot_status["is_running"]:
                break
            time.sleep(1)

    send_telegram("🛑 *تم إيقاف تشغيل بوت المواعيد.*")

# --- الواجهة الخاصة بالسيرفر ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>لوحة بوت مواعيد BLS Spain - الجزائر</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
    <style>
        body { background-color: #f4f6f9; font-family: system-ui, -apple-system, sans-serif; }
        .log-box { background: #1e1e1e; color: #00ff66; font-family: monospace; height: 260px; overflow-y: scroll; padding: 12px; border-radius: 6px; font-size: 13px; line-height: 1.5; }
        .card { border-radius: 10px; border: none; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
    </style>
</head>
<body class="p-2 p-md-4">
    <div class="container card p-3 p-md-4 bg-white" style="max-width: 600px;">
        <h4 class="text-center mb-3 text-danger">🇪🇸 لوحة بوت مواعيد BLS Spain (الجزائر)</h4>
        
        <div class="alert text-center fw-bold" id="statusBadge">جاري التحميل...</div>

        <div class="row g-2 mb-3">
            <div class="col-6">
                <button class="btn btn-success w-100 btn-lg" onclick="controlBot('start')">▶ بدء المراقبة</button>
            </div>
            <div class="col-6">
                <button class="btn btn-danger w-100 btn-lg" onclick="controlBot('stop')">⏹ إيقاف البوت</button>
            </div>
            <div class="col-6">
                <button class="btn btn-warning w-100 text-dark" onclick="controlBot('check_now')">⚡ فحص فوري الآن</button>
            </div>
            <div class="col-6">
                <button class="btn btn-info w-100 text-white" onclick="testTelegram()">📩 اختبار التلغرام</button>
            </div>
        </div>

        <form id="configForm" class="mb-3">
            <div class="mb-2">
                <label class="form-label fw-bold">رابط الموقع (BLS Home):</label>
                <input type="url" class="form-control" name="login_url" value="{{ config.login_url }}" required>
            </div>
            <div class="mb-2">
                <label class="form-label fw-bold">رابط صفحة حجز المواعيد (Appointments URL):</label>
                <input type="url" class="form-control" name="app_url" value="{{ config.app_url }}" required>
            </div>
            <hr>
            <div class="row g-2 mb-2">
                <div class="col-6">
                    <label class="form-label">البريد الإلكتروني:</label>
                    <input type="email" class="form-control" name="email" value="{{ config.email }}">
                </div>
                <div class="col-6">
                    <label class="form-label">كلمة المرور:</label>
                    <input type="password" class="form-control" name="password" value="{{ config.password }}">
                </div>
            </div>
            <div class="row g-2 mb-2">
                <div class="col-6">
                    <label class="form-label">Telegram Token:</label>
                    <input type="text" class="form-control" name="bot_token" value="{{ config.bot_token }}">
                </div>
                <div class="col-6">
                    <label class="form-label">Chat ID:</label>
                    <input type="text" class="form-control" name="chat_id" value="{{ config.chat_id }}">
                </div>
            </div>
            <div class="form-check form-switch my-3">
                <input class="form-check-input" type="checkbox" name="headless" id="headlessSwitch" {% if config.headless %}checked{% endif %}>
                <label class="form-check-label fw-bold" for="headlessSwitch">التشغيل المخفي بدون نافذة (غير موصى به مع BLS)</label>
            </div>
            <button type="button" class="btn btn-primary w-100 mt-2 fw-bold" onclick="saveConfig()">💾 حفظ الإعدادات</button>
        </form>

        <div class="d-flex justify-content-between align-items-center mb-2">
            <h6 class="m-0">سجل العمليات (Logs)</h6>
            <button class="btn btn-sm btn-outline-secondary" onclick="clearLogs()">مسح السجل</button>
        </div>
        <div class="log-box" id="logBox"></div>
    </div>

    <script>
        function updateUI() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('statusBadge');
                    if(data.is_running) {
                        badge.className = 'alert alert-success text-center fw-bold';
                        badge.innerText = 'الحالة: يعمل ويراقب...';
                    } else {
                        badge.className = 'alert alert-danger text-center fw-bold';
                        badge.innerText = 'الحالة: متوقف';
                    }
                    const logBox = document.getElementById('logBox');
                    logBox.innerHTML = data.logs.join('<br>');
                    logBox.scrollTop = logBox.scrollHeight;
                });
        }

        function controlBot(action) {
            fetch('/api/' + action, {method: 'POST'}).then(() => updateUI());
        }

        function testTelegram() {
            fetch('/api/test_telegram', {method: 'POST'})
                .then(r => r.json())
                .then(d => alert(d.message));
        }

        function clearLogs() {
            fetch('/api/clear_logs', {method: 'POST'}).then(() => updateUI());
        }

        function saveConfig() {
            const formData = new FormData(document.getElementById('configForm'));
            formData.set('headless', document.getElementById('headlessSwitch').checked);
            fetch('/api/save', {method: 'POST', body: formData})
                .then(() => alert('تم حفظ بيانات BLS بنجاح!'));
        }

        setInterval(updateUI, 3000);
        updateUI();
    </script>
</body>
</html>
"""

# --- مارات API ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, config=bot_status)

@app.route('/api/status')
def get_status():
    return jsonify({"is_running": bot_status["is_running"], "logs": bot_status["logs"]})

@app.route('/api/start', methods=['POST'])
def start_bot():
    if not bot_status["is_running"]:
        bot_status["is_running"] = True
        threading.Thread(target=bot_loop, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    bot_status["is_running"] = False
    return jsonify({"success": True})

@app.route('/api/check_now', methods=['POST'])
def check_now():
    threading.Thread(target=run_visa_check, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/test_telegram', methods=['POST'])
def test_telegram_route():
    ok = send_telegram("🧪 *رسالة تجريبية من بوت مواعيد BLS Spain.*")
    msg = "تم إرسال الرسالة بنجاح!" if ok else "فشل الإرسال، تحقق من بيانات التلغرام."
    return jsonify({"message": msg})

@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    with data_lock:
        bot_status["logs"] = []
    return jsonify({"success": True})

@app.route('/api/save', methods=['POST'])
def save_config():
    bot_status["login_url"] = request.form.get("login_url")
    bot_status["app_url"] = request.form.get("app_url")
    bot_status["email"] = request.form.get("email")
    bot_status["password"] = request.form.get("password")
    bot_status["bot_token"] = request.form.get("bot_token")
    bot_status["chat_id"] = request.form.get("chat_id")
    bot_status["headless"] = request.form.get("headless") == 'true'
    
    save_config_to_file()
    return jsonify({"success": True})

if __name__ == '__main__':
    add_log("تم تشغيل لوحة التحكم لبوت BLS Spain.")
    app.run(host='0.0.0.0', port=5000)
