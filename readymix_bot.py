import os, logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(format='%(asctime)s | %(levelname)s | %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USERS  = [int(x) for x in os.environ.get("ALLOWED_USER_IDS","").split(",") if x.strip()]
N8N_WEBHOOK    = os.environ.get("N8N_WEBHOOK", "https://muathj.app.n8n.cloud/webhook/readymix")

def is_allowed(uid): return not ALLOWED_USERS or uid in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "مساعد انتاج الباطون الجاهز\n\n"
        "اسأل مثلا:\n"
        "- كم م3 انتجنا في مايو؟\n"
        "- شو متوسط الحمولة في مارس؟\n"
        "- اكبر 10 عملاء في ابريل؟\n"
        "- تحليل السيارات\n"
        "- تحليل السائقين\n"
        "- توزيع الكسرات\n"
        "- تقرير كامل مايو\n\n"
        "اذكر الشهر بسؤالك للحصول على البيانات الصحيحة"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        "الاوامر:\n"
        "/start - الترحيب\n"
        "/help - المساعدة\n\n"
        "اسأل اي سؤال عن الانتاج\n"
        "مثال: كم م3 في مايو 2026؟"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid): return

    user_text = update.message.text
    chat_id   = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = requests.post(
            N8N_WEBHOOK,
            json={
                "body": {
                    "message": user_text,
                    "chat_id": str(chat_id),
                    "user_id": str(uid),
                    "username": update.effective_user.first_name or ""
                }
            },
            timeout=60
        )

        if response.status_code == 200:
            data = response.json()
            # استخرج الجواب من الـ response
            if isinstance(data, dict):
                answer = (data.get('text') or 
                         data.get('message') or 
                         data.get('output') or 
                         data.get('response') or
                         str(data))
            elif isinstance(data, list) and len(data) > 0:
                first = data[0]
                answer = (first.get('text') or 
                         first.get('message') or 
                         first.get('output') or
                         str(first))
            else:
                answer = str(data)

            # إرسال الجواب — تقسيم إذا طويل
            if len(answer) > 4000:
                for i in range(0, len(answer), 4000):
                    await update.message.reply_text(answer[i:i+4000])
            else:
                await update.message.reply_text(answer)
        else:
            await update.message.reply_text(f"خطا في الاتصال بـ n8n: {response.status_code}")

    except requests.exceptions.Timeout:
        await update.message.reply_text("انتهت مهلة الاتصال — n8n بيعالج البيانات، جرب مرة ثانية")
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"خطا: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Telegram Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
