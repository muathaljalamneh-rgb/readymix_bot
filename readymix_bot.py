import os, asyncio, logging
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(format='%(asctime)s | %(levelname)s | %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USERS  = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]
N8N_WEBHOOK    = os.environ.get("N8N_WEBHOOK", "https://muathj.app.n8n.cloud/webhook/readymix")
TIMEOUT        = int(os.environ.get("N8N_TIMEOUT", "180"))

MAX_LEN = 3900


def is_allowed(uid):
    return not ALLOWED_USERS or uid in ALLOWED_USERS


def split_message(text, limit=MAX_LEN):
    """يقسم الرسالة على حدود الأسطر مش بنص الكلمة"""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        # سطر أطول من الحد لحاله
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


async def send_answer(update, text):
    """يرسل الجواب مقسّم، مع محاولة Markdown ورجوع لنص عادي لو فشل"""
    for chunk in split_message(text):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)
        await asyncio.sleep(0.3)


async def keep_typing(bot, chat_id, stop_event):
    """يضل يظهر 'يكتب...' لحد ما يجي الرد"""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "مساعد انتاج الباطون الجاهز\n\n"
        "*امثلة على الاسئلة:*\n"
        "- كم م3 انتجنا في مايو؟\n"
        "- شو متوسط الحمولة في مارس؟\n"
        "- اكبر 10 عملاء في ابريل؟\n"
        "- تحليل السيارات / تحليل السائقين\n"
        "- توزيع الكسرات\n"
        "- الهدر المطالب وغير المطالب\n"
        "- تحليل البوندات / توزيع المناطق\n\n"
        "*للتقرير الكامل:* /report او اكتب `تقرير كامل مايو`\n\n"
        "_اذكر الشهر بسؤالك للحصول على البيانات الصحيحة_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "*الاوامر:*\n"
        "/start - الترحيب\n"
        "/help - المساعدة\n"
        "/report - تقرير كامل\n\n"
        "او اسأل اي سؤال مباشرة\n"
        "مثال: `كم م3 في مايو 2026؟`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def ask_n8n(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(context.bot, chat_id, stop_event))

    try:
        response = await asyncio.to_thread(
            requests.post,
            N8N_WEBHOOK,
            json={
                "message": text,
                "chat_id": str(chat_id),
                "user_id": str(uid),
                "username": update.effective_user.first_name or "",
            },
            timeout=TIMEOUT,
        )

        if response.status_code == 200:
            answer = response.text.strip()
            await send_answer(update, answer or "لم يتم الحصول على رد من n8n")
        else:
            logger.error(f"n8n {response.status_code}: {response.text[:300]}")
            await update.message.reply_text(
                f"خطا في الاتصال بـ n8n (كود {response.status_code})\n"
                "تحقق من الـ workflow في n8n → Executions"
            )

    except requests.exceptions.Timeout:
        await update.message.reply_text(
            f"انتهت المهلة ({TIMEOUT} ثانية).\n"
            "غالبا الـ workflow بيقرأ بيانات كثيرة — راجع Executions في n8n"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"خطا: {e}")
    finally:
        stop_event.set()
        typing_task.cancel()


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    extra = " ".join(context.args) if context.args else ""
    await update.message.reply_text("جاري تجهيز التقرير الكامل، ممكن ياخذ دقيقة...")
    await ask_n8n(update, context, f"تقرير كامل {extra}".strip())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await ask_n8n(update, context, update.message.text)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Telegram Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
