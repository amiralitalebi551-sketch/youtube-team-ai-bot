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
    logger.error("CRITICAL: Environment variables missing!")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

MAX_MEMORY = 10
CHAT_HISTORY_FILE = "chat_histories.json"

TEAM_MEMBERS = {
    "thetoll_man": {"name": "جواد", "role": "ادیتور حرفه‌ای Premiere و استراتژیست محتوا"},
    "hadim848": {"name": "حمید", "role": "ایده‌پرداز خلاق و تحلیلگر ارشد"},
    "far_boo": {"name": "طالب", "role": "متخصص AI و معمار سیستم‌های هوشمند"}
}

SYSTEM_PROMPT = (
    "تو یک هوش مصنوعی فوق‌العاده باهوش، عاقل، نوآور و رفیق صمیمی برای یک تیم ۳ نفره یوتیوبی هستی. "
    "این تیم شامل جواد (@TheToll_man)، حمید (@Hadim848) و طالب (@Far_Boo) است. "
    "\n\nقوانین حیاتی تعامل:"
    "\n۱. برابری مطلق: جواد، حمید و طالب هر سه به یک اندازه بسیار مهم هستند. "
    "\n۲. چاشنی شخصیتی (Vito Flavor): لحنی ملایم شبیه ویتو پارسا (Real Talk، انگیزشی واقع‌گرا، لول‌آپ). "
    "\n۳. نوآوری و انعطاف: ایده‌های نو بده، اما اگر تیم خواستند لحن را عوض کنی، فوراً بپذیر. "
    "\n۴. ظاهر و فرمت پیام (بسیار مهم):"
    "\n   - از ایموجی‌های مناسب و به‌جا استفاده کن تا پیام زنده و صمیمی باشد."
    "\n   - پاسخ‌ها کوتاه، گرم و خوانا باشند."
    "\n   - به هیچ وجه از علامت‌های ** برای بولد کردن استفاده نکن. "
    "\n   - برای بولد کردن فقط و فقط از تگ‌های <b> و </b> استفاده کن. "
    "\n   - تمام تگ‌های HTML را حتماً ببند."
    "\n۵. تشخیص هوشمند لحن: چت صمیمی = کوتاه و گرم. بحث کاری = استراتژیک و عمیق. "
    "\n۶. کیفیت و اختصار: فارسی تمیز با نیم‌فاصله، بدون پرحرفی."
)

def load_chat_histories():
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def save_chat_histories(chat_histories):
    try:
        with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(chat_histories, f, ensure_ascii=False, indent=4)
    except: pass

chat_histories = load_chat_histories()

def clean_html_tags(text):
    """
    Cleans and converts potential markdown-style bolding to HTML tags 
    and ensures the string is safe for Telegram HTML parse_mode.
    """
    # Replace markdown **bold** with HTML <b>bold</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # Escape special HTML characters except the ones we want to use
    # First, protect our <b> and </b> tags
    text = text.replace('<b>', '___B_OPEN___').replace('</b>', '___B_CLOSE___')
    text = text.replace('<i>', '___I_OPEN___').replace('</i>', '___I_CLOSE___')
    text = text.replace('<code>', '___C_OPEN___').replace('</code>', '___C_CLOSE___')
    
    text = html.escape(text)
    
    # Restore tags
    text = text.replace('___B_OPEN___', '<b>').replace('___B_CLOSE___', '</b>')
    text = text.replace('___I_OPEN___', '<i>').replace('___I_CLOSE___', '</i>')
    text = text.replace('___C_OPEN___', '<code>').replace('___C_CLOSE___', '</code>')
    
    return text

async def get_ai_response(chat_id, user_info, new_message, media_path=None, is_audio=False, mode=None):
    global chat_histories
    if str(chat_id) not in chat_histories:
        chat_histories[str(chat_id)] = []
    history = chat_histories[str(chat_id)]
    
    context_prefix = ""
    if mode == "summary": context_prefix = "[حالت خلاصه‌سازی]: محتوا را خلاصه و کاربردی بیان کن.\n"
    elif mode == "plan": context_prefix = "[حالت برنامه‌ریزی]: یک برنامه محتوایی هفتگی پیشنهاد بده.\n"
    elif mode == "rewrite": context_prefix = "[حالت بازنویسی]: این متن را با لحن برند تیم ما بازنویسی کن.\n"
    elif mode == "brainstorm": context_prefix = "[حالت طوفان فکری]: با سوالات هدفمند به پختن این ایده کمک کن.\n"
    elif mode == "task": context_prefix = "[حالت تقسیم کار]: وظایف را بین جواد، حمید و طالب تقسیم کن.\n"

    content_parts = [f"{SYSTEM_PROMPT}\n\n{context_prefix}Sender Identity: {user_info}\nUser Message: {new_message}"]
    
    if media_path and os.path.exists(media_path):
        try:
            if is_audio:
                audio_data = {"mime_type": "audio/ogg", "data": open(media_path, "rb").read()}
                content_parts.append(audio_data)
                content_parts[0] += "\n[این یک فایل صوتی است. آن را پیاده‌سازی و تحلیل کن.]"
            else:
                img = PIL.Image.open(media_path)
                content_parts.append(img)
                content_parts[0] += "\n[این یک تصویر است. آن را نقد یا تحلیل کن.]"
        except Exception as e:
            logger.error(f"Media Processing Error: {e}")

    try:
        response = await asyncio.to_thread(model.generate_content, content_parts)
        ai_reply = clean_html_tags(response.text)
        # Save to history
        history.append({"role": "user", "parts": [f"From {user_info}: {new_message}"]})
        history.append({"role": "model", "parts": [ai_reply]})
        if len(history) > MAX_MEMORY * 2:
            chat_histories[str(chat_id)] = history[-(MAX_MEMORY * 2):]
        save_chat_histories(chat_histories)
        return ai_reply
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return "رفیق، انگار سیستم یه لحظه هنگ کرد! 😅 دوباره بفرست."

async def handle_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    message = update.message
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
    is_audio = bool(message.voice or message.audio)
    is_photo = bool(message.photo)

    if is_private or is_mention or is_reply or is_audio:
        await message.reply_chat_action("typing")
        media_path = None
        try:
            if is_photo:
                photo_file = await message.photo[-1].get_file()
                media_path = f"/tmp/{photo_file.file_id}.jpg"
                await photo_file.download_to_drive(media_path)
            elif is_audio:
                audio_file = await (message.voice or message.audio).get_file()
                media_path = f"/tmp/{audio_file.file_id}.ogg"
                await audio_file.download_to_drive(media_path)
        except Exception as e:
            logger.error(f"Download Error: {e}")

        response = await get_ai_response(chat_id, user_info, text, media_path, is_audio)
        if media_path and os.path.exists(media_path): os.remove(media_path)
        
        try:
            await message.reply_text(response, parse_mode='HTML')
        except Exception as e:
            logger.error(f"HTML Parse Error: {e}")
            # Fallback to plain text if HTML fails
            clean_text = re.sub(r'<[^>]+>', '', response)
            await message.reply_text(clean_text)

# Command Handlers
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>سلام به تیم خفن یوتیوب!</b> 👋✨\nمن آنلاین و آماده‌ام تا با هم بترکونیم. برای راهنما /help رو بزن. 🚀", parse_mode='HTML')

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_histories
    chat_id = update.effective_chat.id
    if str(chat_id) in chat_histories:
        del chat_histories[str(chat_id)]
        save_chat_histories(chat_histories)
    await update.message.reply_text("<b>حافظه با موفقیت پاک شد!</b> 🧹✨", parse_mode='HTML')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>لیست قابلیت‌های هوشمند من:</b> 🛠✨\n\n"
        "💡 /idea - ایده‌های نوآورانه محتوا\n"
        "📝 /summary - خلاصه‌سازی متن یا لینک\n"
        "📅 /plan - برنامه انتشار محتوا\n"
        "✍️ /rewrite - بهبود متن و کپشن\n"
        "🌪 /brainstorm - طوفان فکری\n"
        "👥 /task - پیشنهاد تقسیم کار تیمی\n"
        "🎙 <b>ارسال ویس:</b> تحلیل صوت و متن\n"
        "🖼 <b>ارسال عکس:</b> نقد تامبنیل\n"
        "🧹 /reset - پاک کردن حافظه\n"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def generic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0][1:]
    await update.message.reply_chat_action("typing")
    response = await get_ai_response(update.effective_chat.id, update.message.from_user.first_name, " ".join(context.args) or f"اجرای دستور {cmd}", mode=cmd)
    try:
        await update.message.reply_text(response, parse_mode='HTML')
    except:
        clean_text = re.sub(r'<[^>]+>', '', response)
        await update.message.reply_text(clean_text)

# Health Check Server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

def run_server():
    httpd = HTTPServer(('', PORT), HealthHandler)
    httpd.serve_forever()

if __name__ == '__main__':
    threading.Thread(target=run_server, daemon=True).start()
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    for cmd in ["idea", "summary", "plan", "rewrite", "brainstorm", "task"]:
        app.add_handler(CommandHandler(cmd, generic_command))
    
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO) & (~filters.COMMAND), handle_content))
    
    logger.info("Bot Starting...")
    app.run_polling(drop_pending_updates=True)
