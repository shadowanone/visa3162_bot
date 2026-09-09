#!/usr/bin/env python3
import os
import sys
import logging
import sqlite3
import asyncio
import aiohttp
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand

# تحميل المتغيرات البيئية
load_dotenv()

# إعداد التسجيل (Logging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- إعداد خادم Flask لتجاوز فحص المنفذ على Render ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Telegram Visa Bot (Multi-User) is active!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# --- إعداد قاعدة بيانات SQLite ---
DB_NAME = "bot_users.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                country TEXT,
                city TEXT,
                frequency INTEGER DEFAULT 5,
                is_active INTEGER DEFAULT 0
            )
        ''')
        conn.commit()

def update_user(chat_id, **kwargs):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        for key, value in kwargs.items():
            cursor.execute(f"UPDATE users SET {key} = ? WHERE chat_id = ?", (value, chat_id))
        conn.commit()

def get_user(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT country, city, frequency, is_active FROM users WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        return {"country": row[0], "city": row[1], "frequency": row[2], "is_active": row[3]} if row else None

def get_all_active_users():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, country, city, frequency FROM users WHERE is_active = 1")
        return cursor.fetchall()

# --- الثوابت والإعدادات ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_URL = "https://api.schengenvisaappointments.com/api/visa-list/?format=json"

COUNTRIES = {
    'Spain': 'İspanya',
    'France': 'Fransa',
    'Netherlands': 'Hollanda',
    'Ireland': 'İrlanda',
    'Malta': 'Malta',
    'Sweden': 'İsveç',
    'Czechia': 'Çekya',
    'Croatia': 'Hırvatistan',
    'Bulgaria': 'Bulgaristan',
    'Finland': 'Finlandiya',
    'Slovenia': 'Slovenya',
    'Denmark': 'Danimarka',
    'Norway': 'Norveç',
    'Estonia': 'Estonya',
    'Lithuania': 'Litvanya',
    'Luxembourg': 'Lüksemburg',
    'Ukraine': 'Ukrayna',
    'Latvia': 'Letonya'
}

CITIES = ['Ankara', 'Istanbul', 'Izmir', 'Antalya', 'Gaziantep', 'Bursa', 'Edirne', 'Algiers', 'Oran']

# --- كلاس البوت الرئيسي ---
class VisaBot:
    def __init__(self):
        self.app = None
        self.active_tasks = {}  # {chat_id: asyncio.Task}
        init_db()

    # لوحات المفاتيح التفاعلية
    def create_frequency_keyboard(self):
        keyboard = [
            [InlineKeyboardButton(f"{i} Mins", callback_data=f"freq_{i}") for i in range(1, 6)]
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_country_keyboard(self):
        keyboard = []
        row = []
        for i, (eng_name, tr_name) in enumerate(COUNTRIES.items(), 1):
            row.append(InlineKeyboardButton(tr_name, callback_data=f"country_{eng_name}"))
            if i % 3 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return InlineKeyboardMarkup(keyboard)

    def create_city_keyboard(self):
        keyboard = []
        row = []
        for i, city in enumerate(CITIES, 1):
            row.append(InlineKeyboardButton(city, callback_data=f"city_{city}"))
            if i % 3 == 0:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        return InlineKeyboardMarkup(keyboard)

    # معالجات الأوامر
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        update_user(chat_id)
        welcome_msg = (
            "🌟 Welcome to the Schengen Visa Appointment Bot!\n\n"
            "/check - Start appointment check\n"
            "/stop - Stop active check\n"
            "/status - Show current search status\n"
            "/help - Command list"
        )
        await update.message.reply_text(welcome_msg)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📋 Commands:\n"
            "/check - Select country & city to monitor\n"
            "/stop - Cancel active monitoring\n"
            "/status - View your current monitoring settings"
        )
        await update.message.reply_text(help_text)

    async def check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🌍 Select target country:", reply_markup=self.create_country_keyboard())

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id in self.active_tasks:
            await self.stop_user_task(chat_id)
            await update.message.reply_text("🛑 Appointment monitoring stopped.")
        else:
            await update.message.reply_text("ℹ️ You have no active monitoring task.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user_data = get_user(chat_id)
        if user_data and user_data["is_active"]:
            country_tr = COUNTRIES.get(user_data['country'], user_data['country'])
            msg = (
                f"📊 **Active Monitoring Status**\n\n"
                f"📍 **Country:** {country_tr}\n"
                f"🏢 **City:** {user_data['city']}\n"
                f"⏱ **Interval:** {user_data['frequency']} minutes\n"
                f"✅ **Status:** Running"
            )
        else:
            msg = "ℹ️ No active monitoring task. Use /check to start."
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        data = query.data

        if data.startswith("country_"):
            country_eng = data.split("_", 1)[1]
            update_user(chat_id, country=country_eng)
            country_tr = COUNTRIES.get(country_eng, country_eng)
            await query.edit_message_text(
                f"✅ Country selected: {country_tr}\n🏢 Select target city:",
                reply_markup=self.create_city_keyboard()
            )

        elif data.startswith("city_"):
            city_selected = data.split("_", 1)[1]
            update_user(chat_id, city=city_selected)
            user_data = get_user(chat_id)
            if user_data and user_data["country"]:
                await query.edit_message_text(
                    f"📍 Selected: {COUNTRIES.get(user_data['country'])} - {city_selected}\n"
                    f"⏱ Select check frequency:",
                    reply_markup=self.create_frequency_keyboard()
                )
            else:
                await query.edit_message_text("❌ Please select a country first with /check.")

        elif data.startswith("freq_"):
            freq = int(data.split("_")[1])
            user_data = get_user(chat_id)
            if user_data and user_data["country"] and user_data["city"]:
                country = user_data["country"]
                city = user_data["city"]
                await self.start_user_task(chat_id, country, city, freq)
                country_tr = COUNTRIES.get(country, country)
                await query.edit_message_text(
                    f"🚀 **Monitoring Started!**\n\n"
                    f"📍 Country: {country_tr}\n"
                    f"🏢 City: {city}\n"
                    f"⏱ Interval: {freq} minutes\n\n"
                    f"You will receive a notification as soon as a slot opens up.",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text("❌ Missing configuration. Please restart with /check.")

    # إدارة المهام المنفصلة لكل مستخدم
    async def start_user_task(self, chat_id, country, city, frequency):
        await self.stop_user_task(chat_id)
        update_user(chat_id, country=country, city=city, frequency=frequency, is_active=1)
        task = asyncio.create_task(self.check_appointments_for_user(chat_id, country, city, frequency))
        self.active_tasks[chat_id] = task

    async def stop_user_task(self, chat_id):
        update_user(chat_id, is_active=0)
        if chat_id in self.active_tasks:
            self.active_tasks[chat_id].cancel()
            try:
                await self.active_tasks[chat_id]
            except asyncio.CancelledError:
                pass
            del self.active_tasks[chat_id]

    async def resume_active_searches(self):
        active_users = get_all_active_users()
        for chat_id, country, city, frequency in active_users:
            if country and city:
                task = asyncio.create_task(self.check_appointments_for_user(chat_id, country, city, frequency))
                self.active_tasks[chat_id] = task
        logger.info(f"Resumed active searches for {len(active_users)} users.")

    async def check_appointments_for_user(self, chat_id, country, city, frequency):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    logger.info(f"Checking for user {chat_id}: {country} - {city}")
                    async with session.get(API_URL, timeout=30) as response:
                        if response.status == 200:
                            data = await response.json()
                            for appointment in data:
                                source = appointment.get('source_country')
                                mission = appointment.get('mission_country', '')
                                center = appointment.get('center_name', '')

                                if (
                                    source in ['Turkiye', 'Algeria']
                                    and country == mission
                                    and center and city.lower() in center.lower()
                                ):
                                    appointment_date = appointment.get('appointment_date')
                                    if appointment_date:
                                        try:
                                            date_obj = datetime.fromisoformat(appointment_date.replace('Z', '+00:00'))
                                            tr_date = date_obj.astimezone(ZoneInfo('Europe/Istanbul'))
                                            formatted_date = tr_date.strftime('%d.%m.%Y %H:%M')
                                        except Exception:
                                            formatted_date = appointment_date
                                    else:
                                        formatted_date = 'No date info'

                                    msg = (
                                        f"🎉 **Appointment Found!**\n\n"
                                        f"📍 Country: {COUNTRIES.get(country, country)}\n"
                                        f"🏢 Center: {center}\n"
                                        f"📅 Date: {formatted_date}\n"
                                        f"📋 Category: {appointment.get('visa_category', 'Not specified')}\n"
                                        f"🔗 Link: {appointment.get('book_now_link', '#')}"
                                    )
                                    await self.app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error for user {chat_id}: {str(e)}")

                await asyncio.sleep(frequency * 60)

    async def run(self):
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        commands = [
            BotCommand("start", "Bot info"),
            BotCommand("help", "Help menu"),
            BotCommand("check", "Start check"),
            BotCommand("stop", "Stop check"),
            BotCommand("status", "Status info")
        ]

        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("check", self.check))
        self.app.add_handler(CommandHandler("stop", self.stop))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CallbackQueryHandler(self.button_callback))

        await self.app.initialize()
        await self.app.start()
        await self.app.bot.set_my_commands(commands)
        await self.app.updater.start_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

        # استعادة جميع الفحوصات النشطة من قاعدة البيانات
        await self.resume_active_searches()

        while True:
            await asyncio.sleep(1)

async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN is missing!")
        return

    threading.Thread(target=run_flask, daemon=True).start()

    bot = VisaBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
