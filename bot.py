import os
import logging
import html
import asyncio
import re
import PIL.Image
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from telegram.request import HTTPXRequest
import google.generativeai as genai

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.error("CRITICAL: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY not set!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-flash-latest')

MAX_MEMORY = 10
CHAT_HISTORY_FILE = "chat_histories.json"

TEAM_MEMBERS = {
    "thetoll_man": {"name": "جواد", "role": "ادیتور حرفه‌ای Premiere و استراتژیست محتوا"},
    "hadim848": {"name": "حمید", "role": "ایده‌پرداز خلاق و تحلیلگر ارشد"},
    "far_boo": {"name": "طالب", "role": "متخصص AI و معمار سیستم‌های هوشمند"}
}

SYSTEM_PROMPT = (
    "تو یک هوش مصنوعی فوق‌العاده باهوش، عاقل و رفیق صمیمی برای یک تیم ۳ نفره یوتیوبی هستی. "
    "این تیم شامل جواد (@TheToll_man)، حمید (@Hadim848) و طالب (@Far_Boo) است. "
    "\n\nقوانین حیاتی تعامل:"
    "\n۱. برابری مطلق: هر سه عضو تیم (جواد، حمید، طالب) برای تو به یک اندازه بسیار مهم و محترم هستند. "
    "\n۲. چاشنی شخصیتی (Vito Flavor): لحنی ملایم شبیه به ویتو پارسا داشته باش؛ یعنی 'Real Talk' (صحبت واقعی)، انگیزشی اما واقع‌گرا، و متمرکز بر 'Level-up' کردن. رفیق‌وار و الهام‌بخش باش اما از کلیشه دوری کن. "
    "\n۳. تخصص در برندینگ و محتوا: در بحث‌های تخصصی، مثل یک استراتژیست ارشد روی پرسونال برندینگ، برند گروهی، قلاب (Hook)، عنوان‌های جذاب و استراتژی رشد یوتیوب تمرکز کن. "
    "\n۴. تشخیص هوشمند لحن: "
    "\n   - چت دوستانه: کوتاه، گرم و رفاقتی. "
    "\n   - بحث تخصصی/پروژه: عمیق، ساختارمند، کاربردی و با دیدِ استراتژیک. "
    "\n۵. کیفیت و اختصار: فارسی را بسیار تمیز (با استفاده از نیم‌فاصله) و بدون پرحرفی بنویس. بهترین جواب در کمترین کلمات. "
    "\n۶. قالب‌بندی: حتماً از HTML (<b>, <i>, <code>) استفاده کن و تگ‌ها را ببند."
)

def load_chat_histories():
    if os.path.exists(CHAT_HISTORY_FILE):
        with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return {}
    return {}

def save_chat_histories(chat_histories):
    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(chat_histories, f, ensure_ascii=False, indent=4)

chat_histories = load_chat_histories()

async def get_ai_response(chat_id, user_info, new_message, photo_path=None):
    global chat_histories
    if str(chat_id) not in chat_histories:
        chat_histories[str(chat_id)] = []
    history = chat_histories[str(chat_id)]
    
    content_parts = [f"{SYSTEM_PROMPT}\n\nSender Identity: {user_info}\nUser Message: {new_message}"]
    if photo_path:
        try:
            img = PIL.Image.open(photo_path)
            content_parts.append(img)
        except Exception as e:
            logger.error(f"Image Error: {e}")

    try:
        response = await asyncio.to_thread(model.generate_content, content_parts)
        ai_reply = response.text
        history.append({"role": "user", "parts": [f"From {user_info}: {new_message}"]})
        history.append({"role": "model", "parts": [ai_reply]})
        if len(history) > MAX_MEMORY * 2:
            chat_histories[str(chat_id)] = history[-(MAX_MEMORY * 2):]
        save_chat_histories(chat_histories)
        return ai_reply
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "رفیق، یه مشکلی پیش اومد. دوباره بفرست بررسی کنم. 🙏"

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return
    user = message.from_user
    username = user.username.lower() if user.username else None
    user_info = f"{user.first_name} (ID: {user.id})"
    if username and username in TEAM_MEMBERS:
        user_info = f"{TEAM_MEMBERS[username]['name']} (@{username})"

    text = message.text or message.caption or ""
    chat_id = update.effective_chat.id
    bot_user = await context.bot.get_me()
    
    is_private = message.chat.type == "private"
    is_mention = f"@{bot_user.username}" in text
    is_reply = message.reply_to_message and message.reply_to_message.from_user.id == bot_user.id
    
    if is_private or is_mention or is_reply:
        await message.reply_chat_action("typing")
        photo_path = None
        if message.photo:
            try:
                photo_file = await message.photo[-1].get_file()
                photo_path = f"/tmp/{photo_file.file_id}.jpg"
                await photo_file.download_to_drive(photo_path)
            except Exception as e:
                logger.error(f"Photo download error: {e}")

        response = await get_ai_response(chat_id, user_info, text, photo_path)
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)
        try:
            await message.reply_text(response, parse_mode='HTML')
        except:
            await message.reply_text(response)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>سلام به تیم خفن یوتیوب!</b> 🚀\nجواد، حمید و طالب عزیز، من آماده‌ام برای یه 'Real Talk' واقعی و لول‌آپ کردن برندتون. چطور می‌تونم کمک کنم؟", parse_mode='HTML')

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_histories
    chat_id = update.effective_chat.id
    if str(chat_id) in chat_histories:
        del chat_histories[str(chat_id)]
        save_chat_histories(chat_histories)
    await update.message.reply_text("<b>حافظه با موفقیت پاک شد.</b> 🧹", parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "<b>راهنمای سریع:</b>\n\n/start - شروع\n/reset - پاک کردن تاریخچه\n/help - راهنما\n\nمن اینجام تا توی استراتژی محتوا و برندینگ بهتون کمک کنم."
    await update.message.reply_text(help_text, parse_mode='HTML')

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

def run_health_check_server():
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"Health check server running on port {PORT}")
    httpd.serve_forever()

if __name__ == '__main__':
    threading.Thread(target=run_health_check_server, daemon=True).start()
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_content))
    logger.info("Bot Starting with Polling...")
    application.run_polling(drop_pending_updates=True)
