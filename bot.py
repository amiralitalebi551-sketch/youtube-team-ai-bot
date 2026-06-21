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
PORT = int(os.environ.get("PORT", 10000)) # Render provides PORT env var

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.error("CRITICAL: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY not set!")
    exit(1)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-flash-latest')

MAX_MEMORY = 10
CHAT_HISTORY_FILE = "chat_histories.json"

TEAM_MEMBERS = {
    "thetoll_man": {"name": "جواد", "role": "ادیتور حرفه‌ای Premiere، تحلیلگر قوی، اطلاعات عمومی سطح ۱"},
    "hadim848": {"name": "حمید", "role": "ایده‌پرداز دقیق، تحلیلگر عالی، اطلاعات عمومی سطح ۲"},
    "far_boo": {"name": "طالب", "role": "برنامه‌نویس و متخصص AI (INTJ)، اطلاعات عمومی سطح ۳"}
}

SYSTEM_PROMPT = (
    "تو یک نویسنده حرفه‌ای، استراتژیست ارشد یوتیوب و رفیق صمیمی برای یک تیم ۳ نفره باهوش هستی. "
    "پروژه یوتیوب این تیم در حوزه توسعه فردی (با الهام از ویتو پارسا) است. "
    "\n\nاطلاعات تیم:"
    "\n- جواد (@TheToll_man): ادیتور Premiere. حمید (@Hadim848): ایده‌پرداز. طالب (@Far_Boo): متخصص AI."
    "\n- هر سه اهل توسعه فردی، باشگاه بدنسازی و لایف‌استایل منظم هستند. "
    "\n\nدستورالعمل تعامل:"
    "\n۱. هویت‌شناسی: وقتی پیام از طرف یکی از اعضاست، او را با اسم واقعی بشناس و صمیمی باش. "
    "\n۲. لحن: فارسی محاوره‌ای (شکسته)، گرم، رفاقتی و حرفه‌ای. "
    "\n۳. تحلیل: ایده/سوال/عکس/ویدیو = تحلیل عمیق و ساختارمند با HTML (<b>, <i>, <code>). "
    "\n۴. قانون طلایی: فقط فارسی. تمام تگ‌های HTML را ببند."
)

# --- Persistent Chat History Functions ---
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

    if any(x in new_message for x in ["youtube.com", "youtu.be"]):
        content_parts[0] += "\n\n[تحلیل ویدیو یوتیوب]: لطفاً محتوای این لینک رو تحلیل کن و نکات کلیدی/ایده‌های مشابه رو بگو."

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
        return "رفیق، انگار مغزم یه لحظه داغ کرد! 😂 دوباره بفرست."

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
    await update.message.reply_text("<b>سلام رفیق!</b> 🚀\nمن جواد، حمید و طالب رو می‌شناسم. بگو چطور کمک کنم؟", parse_mode='HTML')

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_histories
    chat_id = update.effective_chat.id
    if str(chat_id) in chat_histories:
        del chat_histories[str(chat_id)]
        save_chat_histories(chat_histories)
    await update.message.reply_text("<b>حافظه پاک شد!</b> 🧹", parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "<b>راهنمای من:</b>\n\nمن یه ربات هوش مصنوعی برای تیم یوتیوب شما هستم.\n/start - شروع\n/reset - پاک کردن حافظه\n/help - راهنما"
    await update.message.reply_text(help_text, parse_mode='HTML')

# --- HEALTH CHECK SERVER ---
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
    # Start health check server in a separate thread
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    # Start Telegram Bot Polling
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_content))
    
    logger.info("Bot Starting with Polling...")
    application.run_polling(drop_pending_updates=True)
