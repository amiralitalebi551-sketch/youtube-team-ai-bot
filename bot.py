import os
import logging
import html
import asyncio
import re
import PIL.Image
import json
from collections import deque
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

# --- CONFIGURATION (Environment Variables) ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    logger.error("CRITICAL: TELEGRAM_BOT_TOKEN or GEMINI_API_KEY not set!")
    # Exit or raise exception if critical environment variables are missing
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)
# Using gemini-2.0-flash as gemini-1.5-flash was not found and 2.0-flash is available.
model = genai.GenerativeModel('gemini-flash-latest')

MAX_MEMORY = 10
CHAT_HISTORY_FILE = "chat_histories.json" # File for persistent storage

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
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Chat history file is corrupted, starting with empty history.")
                return {}
    return {}

def save_chat_histories(chat_histories):
    with open(CHAT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(chat_histories, f, ensure_ascii=False, indent=4)

chat_histories = load_chat_histories()

def is_youtube_url(text):
    if not text: return False
    youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    return re.search(youtube_regex, text)

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

    if is_youtube_url(new_message):
        content_parts[0] += "\n\n[تحلیل ویدیو یوتیوب]: لطفاً محتوای این لینک رو تحلیل کن و نکات کلیدی/ایده‌های مشابه رو بگو."

    try:
        # Add previous conversation history to the prompt
        for entry in history:
            content_parts.append(entry)

        response = model.generate_content(content_parts)
        ai_reply = response.text
        
        # Update history and save
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
    is_photo = bool(message.photo)

    if is_private or is_mention or is_reply:
        await message.reply_chat_action("typing")
        
        photo_path = None
        if is_photo:
            try:
                photo_file = await message.photo[-1].get_file()
                photo_path = f"/tmp/{photo_file.file_id}.jpg"
                await photo_file.download_to_drive(photo_path)
            except Exception as e:
                logger.error(f"Error downloading photo: {e}")
                await message.reply_text("رفیق، نتونستم عکس رو دانلود کنم. یه بار دیگه امتحان می‌کنی؟")
                return

        response = await get_ai_response(chat_id, user_info, text, photo_path)
        
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)

        try:
            await message.reply_text(response, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Error sending HTML formatted message: {e}")
            await message.reply_text(response) # Send without HTML if parsing fails

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>سلام رفیق!</b> 🚀\nمن جواد، حمید و طالب رو می‌شناسم. حالا عکس و یوتیوب هم می‌فهمم! بگو چطور کمک کنم؟", parse_mode='HTML')

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_histories
    chat_id = update.effective_chat.id
    if str(chat_id) in chat_histories:
        del chat_histories[str(chat_id)]
        save_chat_histories(chat_histories)
    await update.message.reply_text("<b>حافظه پاک شد!</b> 🧹", parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>راهنمای من:</b>\n\n"
        "من یه ربات هوش مصنوعی برای تیم یوتیوب شما هستم که تو حوزه توسعه فردی فعالیت می‌کنه.\n"
        "می‌تونم عکس‌ها و لینک‌های یوتیوب رو تحلیل کنم و باهاتون فارسی محاوره‌ای و صمیمی صحبت کنم.\n\n"
        "<b>دستورات:</b>\n"
        "/start - شروع مکالمه و معرفی من\n"
        "/reset - پاک کردن حافظه گفتگوی فعلی\n"
        "/help - نمایش این راهنما\n\n"
        "<b>قابلیت‌ها:</b>\n"
        "- <b>تحلیل عکس:</b> کافیه یه عکس بفرستی تا من تحلیلش کنم.\n"
        "- <b>تحلیل لینک یوتیوب:</b> لینک ویدیو یوتیوب رو بفرست تا محتواش رو بررسی کنم و ایده بدم.\n"
        "- <b>گفتگوی هوشمند:</b> باهام حرف بزن، سوال بپرس، ایده بگیر!\n\n"
        "من همیشه آماده‌ام تا به تیم شما کمک کنم! 🚀"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')


if __name__ == '__main__':
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("CRITICAL ERROR: Environment variables missing.")
        exit(1)
    else:
        request = HTTPXRequest(connect_timeout=60, read_timeout=60)
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("reset", reset_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_content))
        print("Multimodal Deployable Bot Starting...")
        application.run_polling(drop_pending_updates=True)
