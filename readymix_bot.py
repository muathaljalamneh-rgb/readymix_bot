import os
import io
import logging
import re
from datetime import datetime

import anthropic
import pandas as pd
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USERS  = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── In-memory store ───────────────────────────────────────
reports: dict = {}           # month_key -> {filename, raw_text, summary, uploaded_at}
conversation_history: dict = {}   # user_id -> list of messages

# ── System prompt ─────────────────────────────────────────
SYSTEM_PROMPT = """أنت مساعد خبير في تحليل بيانات إنتاج وتسليم الباطون الجاهز (الخرسانة الجاهزة) لمصنع باطون.

لديك صلاحية الوصول إلى تقارير التسليم الشهرية المرفوعة من الفريق.

دورك:
- الإجابة على أسئلة الإنتاج والتسليم (الكميات، الكسرات، العملاء، السائقين، المناطق، الأوقات)
- حساب الإجماليات والمعدلات اليومية والشهرية
- مقارنة البيانات بين الأشهر عند توفر أكثر من تقرير
- رصد أكبر العملاء استهلاكاً
- تحليل توزيع الكسرات (C150, C210, C250, C300, C350, WP, Screed)
- الإجابة باللغة التي يسألك بها المستخدم (عربي أو إنجليزي)

التقارير المتاحة:
{reports_summary}

هيكل البيانات (أعمدة كل تقرير):
| العمود              | الوصف                                         |
|---------------------|-----------------------------------------------|
| رقم السند           | رقم أمر التسليم                               |
| اسم العميل          | اسم الزبون                                    |
| المنطقة             | منطقة الموقع                                  |
| رقم السيارة         | لوحة الشاحنة                                  |
| اسم السائق          | اسم السائق                                    |
| نوع الكسر           | نوع الخرسانة (C150/C210/C250/C300/C350/WP/Screed) |
| الكمية              | م³ لهذه الصبة (الخلاط = 10-12 م³، المضخة = 0) |
| مكان الإخراج        | طريقة الصب: خلاط / مضخة / قواعد / جدران / أعمدة / عقدة |
| عدد الصبات          | رقم الصبة ضمن الأمر (0=مضخة، 1،2،3...=خلاطات) |
| نوع السيارة         | AM-1616 أو AM-SH                              |
| رقم الهاتف          | هاتف العميل                                   |
| وقت وتاريخ الصب     | التاريخ والوقت (صباحاً/مساءً)                 |
| الكمية الراجعة      | م³ رجعت للمصنع                                |

ملاحظة مهمة: صفوف "مضخة" كميتها 0 — لا تحتسبها في إجمالي الإنتاج.
الكمية الفعلية فقط من صفوف "خلاط".

قواعد الإجابة الصارمة:
1. اجمع الأرقام بنفسك من البيانات — لا تخمّن أي رقم.
2. للإجمالي اليومي: اجمع عمود الكمية حيث مكان الإخراج ≠ "مضخة".
3. للإجمالي حسب الكسر: قسّم حسب "نوع الكسر" واجمع الكميات.
4. للعملاء: اجمع حسب "اسم العميل" واعرض الأكبر.
5. اذكر دائماً من أي شيت/شهر جاءت البيانات.
6. إذا كانت البيانات غير موجودة، قل ذلك صراحةً.

في نهاية كل إجابة أضف: '✅ البيانات من: [اسم الشيت/الشهر]'"""


def get_reports_summary() -> str:
    if not reports:
        return "لا توجد تقارير مرفوعة بعد."
    lines = []
    for month, data in sorted(reports.items()):
        lines.append(f"- {month}: {data['filename']} (رُفع {data['uploaded_at']})")
    return "\n".join(lines)


def build_sheet_summary(df: pd.DataFrame, sheet_name: str) -> str:
    """Build a structured summary from a sheet's DataFrame."""
    summary = f"\n{'='*60}\n[شيت] {sheet_name}\n{'='*60}\n"
    summary += f"إجمالي الصفوف: {len(df)}\n"
    summary += f"الأعمدة: {list(df.columns)}\n\n"

    # Detect columns by partial match (Arabic)
    def find_col(df, keywords):
        for col in df.columns:
            c = str(col).strip()
            for kw in keywords:
                if kw in c:
                    return col
        return None

    qty_col    = find_col(df, ['الكمية', 'كمية', 'م3', 'm3', 'quantity'])
    grade_col  = find_col(df, ['نوع الكسر', 'الكسر', 'grade', 'كسر'])
    client_col = find_col(df, ['اسم العميل', 'العميل', 'client', 'زبون'])
    area_col   = find_col(df, ['المنطقة', 'منطقة', 'area', 'region'])
    type_col   = find_col(df, ['مكان الإخراج', 'نوع الصب', 'إخراج', 'type'])
    date_col   = find_col(df, ['وقت', 'تاريخ', 'date', 'time'])
    driver_col = find_col(df, ['السائق', 'سائق', 'driver'])
    truck_col  = find_col(df, ['السيارة', 'سيارة', 'truck', 'plate'])
    ret_col    = find_col(df, ['الراجع', 'راجع', 'return'])

    # Numeric cleanup
    if qty_col:
        df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)

    # Filter out pump rows (zero quantity) for production totals
    if qty_col and type_col:
        mask_pump = df[type_col].astype(str).str.contains('مضخة|pump', case=False, na=False)
        df_prod = df[~mask_pump].copy()
    elif qty_col:
        df_prod = df[df[qty_col] > 0].copy()
    else:
        df_prod = df.copy()

    if qty_col:
        total_all  = df[qty_col].sum()
        total_prod = df_prod[qty_col].sum()
        summary += f"إجمالي الكمية الكلية (شامل المضخة): {total_all:.1f} م³\n"
        summary += f"إجمالي الإنتاج الفعلي (بدون صفوف المضخة): {total_prod:.1f} م³\n\n"

    # Grade breakdown
    if grade_col and qty_col:
        try:
            grade_sum = df_prod.groupby(grade_col)[qty_col].sum().sort_values(ascending=False)
            summary += "توزيع حسب نوع الكسر:\n"
            for g, v in grade_sum.items():
                if v > 0:
                    summary += f"  {g}: {v:.1f} م³\n"
            summary += "\n"
        except Exception as e:
            logger.warning(f"grade breakdown: {e}")

    # Top clients
    if client_col and qty_col:
        try:
            cli_sum = df_prod.groupby(client_col)[qty_col].sum().sort_values(ascending=False).head(25)
            summary += "أكبر 25 عميل (حسب الكمية):\n"
            for c, v in cli_sum.items():
                if v > 0:
                    summary += f"  {c}: {v:.1f} م³\n"
            summary += "\n"
        except Exception as e:
            logger.warning(f"client summary: {e}")

    # Area breakdown
    if area_col and qty_col:
        try:
            area_sum = df_prod.groupby(area_col)[qty_col].sum().sort_values(ascending=False).head(15)
            summary += "توزيع حسب المنطقة:\n"
            for a, v in area_sum.items():
                if v > 0:
                    summary += f"  {a}: {v:.1f} م³\n"
            summary += "\n"
        except Exception as e:
            logger.warning(f"area summary: {e}")

    # Daily production
    if date_col and qty_col:
        try:
            daily = df_prod.groupby(date_col)[qty_col].sum().sort_index()
            summary += "الإنتاج اليومي:\n"
            for d, v in daily.items():
                if v > 0:
                    summary += f"  {d}: {v:.1f} م³\n"
            summary += "\n"
        except Exception as e:
            logger.warning(f"daily summary: {e}")

    # Raw data (first 300 rows)
    summary += "البيانات الخام (أول 300 صف):\n"
    summary += df.head(300).to_string(index=False, max_cols=20)
    summary += "\n"

    return summary


def extract_excel_data(file_bytes: bytes, filename: str) -> str:
    """Extract all sheets from a ReadyMix Excel report."""
    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        all_sheets = xl.sheet_names

        header = (
            f"ملف التقرير: {filename}\n"
            f"الشيتات ({len(all_sheets)}): {', '.join(all_sheets)}\n"
            "ملاحظة: كل شيت = شهر واحد من بيانات التسليم.\n\n"
        )

        sheets_text = []
        for sheet in all_sheets:
            try:
                df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
                df.dropna(how='all', inplace=True)
                df.reset_index(drop=True, inplace=True)
                sheets_text.append(build_sheet_summary(df, sheet))
            except Exception as e:
                logger.warning(f"خطأ في شيت '{sheet}': {e}")
                sheets_text.append(f"\n[شيت] {sheet} — خطأ في القراءة: {e}\n")

        full_text = header + "\n".join(sheets_text)

        if len(full_text) > 150_000:
            full_text = full_text[:150_000] + "\n... [مقتطع — الملف يتجاوز حد السياق]"

        return full_text

    except Exception as e:
        return f"خطأ في قراءة ملف Excel: {str(e)}"


def detect_month_from_filename(filename: str) -> str:
    months_map = {
        'jan':'01','feb':'02','mar':'03','apr':'04','may':'05','jun':'06',
        'jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12',
        'يناير':'01','فبراير':'02','مارس':'03','أبريل':'04','ابريل':'04',
        'مايو':'05','يونيو':'06','يوليو':'07','أغسطس':'08','اغسطس':'08',
        'سبتمبر':'09','أكتوبر':'10','اكتوبر':'10','نوفمبر':'11','ديسمبر':'12',
    }
    fn = filename.lower()
    m = re.search(r'(\d{4})[_\-](\d{2})', fn)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    for name, num in months_map.items():
        if name in fn:
            yr = re.search(r'(\d{4})', fn)
            if yr:
                return f"{yr.group(1)}-{num}"
    return datetime.now().strftime("%Y-%m")


async def check_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ غير مصرح. تواصل مع المدير.")
        return False
    return True


# ── Handlers ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return
    name = update.effective_user.first_name
    text = (
        f"👋 أهلاً {name}!\n\n"
        "أنا مساعدك لتحليل بيانات **إنتاج الباطون الجاهز** 🏗️\n\n"
        "📊 *ما أقدر أساعدك فيه:*\n"
        "• إجمالي الإنتاج اليومي والشهري\n"
        "• توزيع الكسرات (C150 / C250 / C300 / ...)\n"
        "• أكبر العملاء استهلاكاً\n"
        "• إنتاج حسب المنطقة أو السائق\n"
        "• مقارنة بين الأشهر\n\n"
        "📁 *للبدء:* ارفع ملف Excel لبيانات الإنتاج (.xlsx)\n\n"
        "💬 *أمثلة على الأسئلة:*\n"
        "• كم م³ سلّمنا في شهر مارس؟\n"
        "• شو أكثر عميل استهلك في أبريل؟\n"
        "• كم م³ كسر C300 الشهر الماضي؟\n"
        "• اعطني الإنتاج اليومي لشهر مايو\n"
        "• شو نسبة كل كسر من الإجمالي؟\n\n"
        "/reports — التقارير المرفوعة\n"
        "/clear — مسح المحادثة"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return
    if not reports:
        await update.message.reply_text("📭 لا توجد تقارير مرفوعة.\n\nأرسل لي ملف Excel للبدء.")
        return
    text = "📋 *التقارير المرفوعة:*\n\n"
    for month, data in sorted(reports.items()):
        text += f"📅 `{month}` — {data['filename']}\n"
        text += f"   رُفع: {data['uploaded_at']}\n\n"
    await update.message.reply_text(text, parse_mode='Markdown')


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return
    uid = update.effective_user.id
    conversation_history[uid] = []
    await update.message.reply_text("🗑️ تم مسح تاريخ المحادثة. نبدأ من جديد!")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return
    text = (
        "🔧 *الأوامر المتاحة:*\n"
        "/start — رسالة الترحيب\n"
        "/reports — عرض التقارير المرفوعة\n"
        "/clear — مسح تاريخ المحادثة\n"
        "/help — هذه الرسالة\n\n"
        "📁 *رفع ملف:* أرسل أي ملف .xlsx لبيانات الإنتاج\n\n"
        "💬 *اسأل* أي سؤال عن البيانات — بالعربي أو الإنجليزي"
    )
    await update.message.reply_text(text, parse_mode='Markdown')


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return
    doc = update.message.document

    if not doc.file_name.lower().endswith('.xlsx'):
        await update.message.reply_text("⚠️ الرجاء إرسال ملف Excel بصيغة .xlsx فقط.")
        return

    await update.message.reply_text("⏳ جاري معالجة التقرير... انتظر لحظة.")

    try:
        file       = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await file.download_as_bytearray())

        month_key = detect_month_from_filename(doc.file_name)
        raw_text  = extract_excel_data(file_bytes, doc.file_name)

        # Generate Arabic summary via Claude
        summary_resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=700,
            messages=[{
                "role": "user",
                "content": (
                    "لخّص تقرير إنتاج الباطون هذا في 5-6 نقاط أساسية بالأرقام فقط، "
                    "باللغة العربية:\n\n" + raw_text[:8000]
                )
            }]
        )
        summary = summary_resp.content[0].text

        reports[month_key] = {
            "filename":    doc.file_name,
            "raw_text":    raw_text,
            "summary":     summary,
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        reply = (
            f"✅ *تم رفع التقرير:* `{doc.file_name}`\n"
            f"📅 *الفترة:* {month_key}\n\n"
            f"*ملخص سريع:*\n{summary}\n\n"
            "يمكنك الآن سؤالي عن أي بيانات في هذا التقرير!"
        )
        await update.message.reply_text(reply, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"خطأ في معالجة الملف: {e}")
        await update.message.reply_text(f"❌ خطأ في معالجة الملف: {str(e)}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update): return

    uid       = update.effective_user.id
    user_text = update.message.text

    if not reports:
        await update.message.reply_text(
            "📭 لا توجد تقارير مرفوعة بعد.\n\nالرجاء إرسال ملف Excel لبيانات الإنتاج أولاً."
        )
        return

    # Build full context from all reports
    reports_context = ""
    for month, data in sorted(reports.items()):
        reports_context += f"\n\n{'='*60}\nالتقرير: {month} ({data['filename']})\n{'='*60}\n"
        reports_context += data['raw_text'][:20000]   # per-report limit

    system = SYSTEM_PROMPT.format(reports_summary=get_reports_summary())
    system += f"\n\n{reports_context}"

    if uid not in conversation_history:
        conversation_history[uid] = []

    conversation_history[uid].append({"role": "user", "content": user_text})

    if len(conversation_history[uid]) > 20:
        conversation_history[uid] = conversation_history[uid][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # ── Step 1: Draft answer ──────────────────────────────
        draft_messages = conversation_history[uid] + [{
            "role": "user",
            "content": (
                "[خطوة داخلية 1 — لا ترسل هذا للمستخدم]\n"
                "ضع مسودة إجابة مفصلة للسؤال أعلاه. "
                "اجمع الأرقام من البيانات الخام والملخصات. "
                "لا تتحقق الآن — فقط ضع المسودة."
            )
        }]

        draft_resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=system,
            messages=draft_messages
        )
        draft = draft_resp.content[0].text

        # ── Step 2: Self-verify ───────────────────────────────
        verify_messages = conversation_history[uid] + [
            {"role": "assistant", "content": draft},
            {
                "role": "user",
                "content": (
                    "[خطوة داخلية 2 — لا تُضمّن هذا التعليم في ردك]\n"
                    "الآن تحقق من مسودتك:\n"
                    "• أعد جمع الأرقام من البيانات الخام وقارنها بالملخصات.\n"
                    "• تأكد أنك استثنيت صفوف 'مضخة' من إجمالي الإنتاج.\n"
                    "• إذا وجدت خطأ، صحّحه.\n"
                    "• قدّم الإجابة النهائية المتحقق منها للمستخدم "
                    "بنفس اللغة التي استخدمها.\n"
                    "• أضف في النهاية: '✅ البيانات من: [اسم الشيت/الشهر]'"
                )
            }
        ]

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        verify_resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=system,
            messages=verify_messages
        )
        answer = verify_resp.content[0].text

        conversation_history[uid].append({"role": "assistant", "content": answer})

        # Telegram max length 4096 chars
        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                await update.message.reply_text(answer[i:i+4000])
        else:
            await update.message.reply_text(answer)

    except Exception as e:
        logger.error(f"خطأ في Claude API: {e}")
        await update.message.reply_text(f"❌ خطأ في الحصول على الإجابة: {str(e)}")


# ── Main ──────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("reports", list_reports))
    app.add_handler(CommandHandler("clear",   clear_history))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 ReadyMix Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
