#!/usr/bin/env python3
import os
import sys
import logging
import json
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

# إعدادات التسجيل (Logging)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# خادم Flask لإبقاء الخدمة نشطة على Render
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "✅ Telegram Visa Bot is running smoothly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# جلب الإعدادات من البيئة
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = "https://api.schengenvisaappointments.com/api/visa-list/?format=json"

# قائمة الدول المتاحة
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

class VisaBot:
    def __init__(self):
        self.app = None
        self.running = False
        self.current_check = None
        self.country = None
        self.city = None
        self.frequency = 5
        self.user_selections = {}

    def create_frequency_keyboard(self):
        keyboard = [
            [InlineKeyboardButton(f"{i} Minutes", callback_data=f"freq_{i}") for i in range(1, 6)]
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

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            await query.answer()
            user_id = str(update.effective_user.id)

            if user_id not in self.user_selections:
                self.user_selections[user_id] = {}

            data = query.data
            await query.edit_message_text("⏳ Processing... Please wait.")

            if data.startswith("freq_"):
                self.frequency = int(data.split("_")[1])
                if self.running:
                    await self.stop_checking()
                    self.running = True
                    self.current_check = asyncio.create_task(self.check_appointments())
                await query.edit_message_text(f"✅ Check frequency set to {self.frequency} minutes.")

            elif data.startswith("country_"):
                selected_country_eng = data.split("_", 1)[1]
                if selected_country_eng in COUNTRIES:
                    selected_country_tr = COUNTRIES[selected_country_eng]
                    self.user_selections[user_id] = {"country": selected_country_eng}
                    self.country = selected_country_eng
                    await query.edit_message_text(
                        f"✅ {selected_country_tr} selected.\n🏢 Please select a city:",
                        reply_markup=self.create_city_keyboard()
                    )

            elif data.startswith("city_"):
                selected_city = data.split("_", 1)[1]
                self.user_selections[user_id]["city"] = selected_city
                if "country" in self.user_selections[user_id]:
                    selected_country = self.user_selections[user_id]["country"]
                    await self.start_check_with_selections(update, selected_country, selected_city)
                else:
                    await query.edit_message_text("❌ Please select a country first.")

        except Exception as e:
            logger.error(f"Callback processing error: {str(e)}")

    async def start_check_with_selections(self, update, country, city):
        if self.running:
            await self.stop_checking()

        self.country = country
        self.city = city
        self.running = True
        country_tr = COUNTRIES.get(country, country)

        message = (
            f"✅ Appointment check started for {country_tr} in {city}.\n"
            f"⏱ Select the check frequency:"
        )

        if hasattr(update, "callback_query"):
            await update.callback_query.edit_message_text(message, reply_markup=self.create_frequency_keyboard())
        else:
            await update.message.reply_text(message, reply_markup=self.create_frequency_keyboard())

        self.current_check = asyncio.create_task(self.check_appointments())

        if TELEGRAM_CHAT_ID:
            try:
                start_message = (
                    f"🔄 Appointment check started\n"
                    f"📍 Country: {country_tr}\n"
                    f"🏢 City: {city}\n"
                    f"⏱ Check frequency: {self.frequency} minutes\n"
                    f"⏰ Start: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
                await self.app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=start_message)
            except Exception as e:
                logger.error(f"Error sending start message: {str(e)}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_message = (
            "🌟 Welcome to the Schengen Visa Appointment Check Bot!\n\n"
            "/start - Bot information\n"
            "/check - Start appointment check\n"
            "/stop - Stop active check\n"
            "/status - Current status information\n"
            "/help - Help menu"
        )
        await update.message.reply_text(welcome_message)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📋 Command List:\n"
            "/check - Start Appointment Check\n"
            "/stop - Stop Check\n"
            "/status - Check Status"
        )
        await update.message.reply_text(help_text)

    async def check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🌍 Please select a country:", reply_markup=self.create_country_keyboard())

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.running:
            await update.message.reply_text("ℹ️ No active check.")
            return
        await self.stop_checking()
        await update.message.reply_text("✅ Appointment check stopped.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.running:
            await update.message.reply_text("ℹ️ No active check.")
            return
        status_message = (
            f"📍 Country: {self.country}\n"
            f"🏢 City: {self.city}\n"
            f"⏱ Frequency: {self.frequency} mins\n"
            "✅ Status: Active"
        )
        await update.message.reply_text(status_message)

    async def stop_checking(self):
        self.running = False
        if self.current_check:
            self.current_check.cancel()
            try:
                await self.current_check
            except asyncio.CancelledError:
                pass
        self.current_check = None

    async def check_appointments(self):
        check_count = 0
        async with aiohttp.ClientSession() as session:
            while self.running:
                check_count += 1
                try:
                    logger.info(f"Checking appointments for {self.country} - {self.city} (#{check_count})")
                    async with session.get(API_URL, timeout=30) as response:
                        if response.status == 200:
                            data = await response.json()
                            available_appointments = []

                            for appointment in data:
                                source = appointment.get('source_country')
                                mission = appointment.get('mission_country', '')
                                center = appointment.get('center_name', '')

                                # دعم تركيا والجزائر
                                if (
                                    source in ['Turkiye', 'Algeria']
                                    and self.country == mission
                                    and center and self.city and self.city.lower() in center.lower()
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

                                    available_appointments.append({
                                        'date': formatted_date,
                                        'center': center,
                                        'category': appointment.get('visa_category', 'Not specified'),
                                        'link': appointment.get('book_now_link', '#')
                                    })

                            if available_appointments and TELEGRAM_CHAT_ID:
                                for appt in available_appointments:
                                    msg = (
                                        f"🎉 Appointment found for {self.country}!\n\n"
                                        f"📍 Center: {appt['center']}\n"
                                        f"📅 Date: {appt['date']}\n"
                                        f"📋 Category: {appt['category']}\n"
                                        f"🔗 Link:\n{appt['link']}"
                                    )
                                    await self.app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error during check: {str(e)}")

                await asyncio.sleep(self.frequency * 60)

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

        while True:
            await asyncio.sleep(1)

async def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN is missing!")
        return

    # تشغيل سيرفر Flask في Thread منفصل لفتح منفذ HTTP لـ Render
    threading.Thread(target=run_flask, daemon=True).start()

    bot = VisaBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
