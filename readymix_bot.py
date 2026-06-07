import os, io, logging, re
from datetime import datetime

import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import pandas as pd

logging.basicConfig(format='%(asctime)s | %(levelname)s | %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
ALLOWED_USERS  = [int(x) for x in os.environ.get("ALLOWED_USER_IDS","").split(",") if x.strip()]
ADMIN_USER_ID  = int(os.environ.get("ADMIN_USER_ID","0"))
DATABASE_URL   = os.environ.get("DATABASE_URL","")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── DB ────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    month_key   TEXT PRIMARY KEY,
                    filename    TEXT,
                    structured  TEXT,
                    summary     TEXT,
                    uploaded_at TEXT
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    user_id    BIGINT,
                    role       TEXT,
                    content    TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
        conn.commit()

def save_report(mk, fn, structured, summary):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO reports (month_key, filename, structured, summary, uploaded_at)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (month_key) DO UPDATE SET
                    filename    = EXCLUDED.filename,
                    structured  = EXCLUDED.structured,
                    summary     = EXCLUDED.summary,
                    uploaded_at = EXCLUDED.uploaded_at
            """, (mk, fn, structured, summary, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()

def load_all_reports():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM reports ORDER BY month_key")
            return {r['month_key']: dict(r) for r in cur.fetchall()}

def save_msg(uid, role, content):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (user_id, role, content) VALUES (%s,%s,%s)",
                (uid, role, content)
            )
        conn.commit()

def load_history(uid, limit=10):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT role, content FROM conversations
                WHERE user_id=%s ORDER BY created_at DESC LIMIT %s
            """, (uid, limit))
            return [{"role": r["role"], "content": r["content"]}
                    for r in reversed(cur.fetchall())]

def clear_db(uid):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE user_id=%s", (uid,))
        conn.commit()

# ── Excel Parser ──────────────────────────────────────────
def find_col(df, keywords):
    for col in df.columns:
        c = str(col).strip()
        for kw in keywords:
            if kw in c:
                return col
    return None

def extract_structured(file_bytes: bytes, filename: str) -> str:
    try:
        xl    = pd.ExcelFile(io.BytesIO(file_bytes))
        lines = [f"REPORT: {filename}", f"SHEETS: {', '.join(xl.sheet_names)}"]

        for sheet in xl.sheet_names:
            try:
                df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
                df.dropna(how='all', inplace=True)
                df.reset_index(drop=True, inplace=True)

                lines.append(f"\n{'='*60}")
                lines.append(f"SHEET: {sheet}  |  rows={len(df)}  |  cols={list(df.columns)}")
                lines.append(f"{'='*60}")

                qty_col    = find_col(df, ['الكمية','كمية','م3','m3','quantity'])
                grade_col  = find_col(df, ['نوع الكسر','الكسر','grade','كسر'])
                client_col = find_col(df, ['اسم العميل','العميل','client','زبون'])
                area_col   = find_col(df, ['المنطقة','منطقة','area','region'])
                type_col   = find_col(df, ['مكان الإخراج','نوع الصب','إخراج','type'])
                date_col   = find_col(df, ['وقت','تاريخ','date','time'])
                driver_col = find_col(df, ['السائق','سائق','driver'])
                ret_col    = find_col(df, ['الراجع','راجع','return'])

                if qty_col:
                    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)

                if qty_col and type_col:
                    mask_pump = df[type_col].astype(str).str.contains('مضخة|pump', case=False, na=False)
                    df_prod   = df[~mask_pump].copy()
                elif qty_col:
                    df_prod   = df[df[qty_col] > 0].copy()
                else:
                    df_prod   = df.copy()

                if qty_col:
                    total_all  = df[qty_col].sum()
                    total_prod = df_prod[qty_col].sum()
                    lines.append(f"TOTAL_ALL={total_all:.1f}m3  |  TOTAL_PRODUCTION(no pump)={total_prod:.1f}m3")

                if ret_col:
                    df[ret_col] = pd.to_numeric(df[ret_col], errors='coerce').fillna(0)
                    lines.append(f"TOTAL_RETURNED={df[ret_col].sum():.1f}m3")

                if grade_col and qty_col:
                    gs = df_prod.groupby(grade_col)[qty_col].sum().sort_values(ascending=False)
                    lines.append("GRADE_BREAKDOWN:")
                    for g, v in gs.items():
                        if v > 0:
                            lines.append(f"  {g}={v:.1f}m3")

                if client_col and qty_col:
                    cs = df_prod.groupby(client_col)[qty_col].sum().sort_values(ascending=False).head(30)
                    lines.append("TOP_CLIENTS:")
                    for c, v in cs.items():
                        if v > 0:
                            lines.append(f"  {c}={v:.1f}m3")

                if area_col and qty_col:
                    ar = df_prod.groupby(area_col)[qty_col].sum().sort_values(ascending=False).head(20)
                    lines.append("AREA_BREAKDOWN:")
                    for a, v in ar.items():
                        if v > 0:
                            lines.append(f"  {a}={v:.1f}m3")

                if date_col and qty_col:
                    try:
                        daily = df_prod.groupby(date_col)[qty_col].sum().sort_index()
                        lines.append("DAILY_PRODUCTION:")
                        for d, v in daily.items():
                            if v > 0:
                                lines.append(f"  {d}={v:.1f}m3")
                    except Exception as e:
                        logger.warning(f"daily: {e}")

                if driver_col and qty_col:
                    dr = df_prod.groupby(driver_col)[qty_col].sum().sort_values(ascending=False).head(20)
                    lines.append("DRIVER_PRODUCTION:")
                    for d, v in dr.items():
                        if v > 0:
                            lines.append(f"  {d}={v:.1f}m3")

                lines.append("RAW_DATA (first 400 rows):")
                lines.append(df.head(400).to_string(index=False, max_cols=20))

            except Exception as e:
                logger.warning(f"Sheet '{sheet}': {e}")
                lines.append(f"SHEET {sheet} — read error: {e}")

        text = "\n".join(lines)
        return text[:100000]

    except Exception as e:
        return f"ERROR reading Excel: {e}"


def detect_month(fn: str) -> str:
    months = {
        'jan':'01','feb':'02','mar':'03','apr':'04','may':'05','jun':'06',
        'jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12',
        'يناير':'01','فبراير':'02','مارس':'03','أبريل':'04','ابريل':'04',
        'مايو':'05','يونيو':'06','يوليو':'07','أغسطس':'08','اغسطس':'08',
        'سبتمبر':'09','أكتوبر':'10','اكتوبر':'10','نوفمبر':'11','ديسمبر':'12',
    }
    f = fn.lower()
    m = re.search(r'(\d{4})[_\-](\d{2})', f)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    for name, num in months.items():
        if name in f:
            yr = re.search(r'(\d{4})', f)
            if yr:
                return f"{yr.group(1)}-{num}"
    return datetime.now().strftime("%Y-%m")

def is_allowed(uid): return not ALLOWED_USERS or uid in ALLOWED_USERS
def is_admin(uid):   return uid == ADMIN_USER_ID

# ── System Prompt ─────────────────────────────────────────
SYSTEM = """أنت مساعد خبير في تحليل بيانات إنتاج وتسليم الباطون الجاهز (الخرسانة الجاهزة).

لديك بيانات التقارير الشهرية كاملة.

دورك:
- الإجابة على أسئلة الإنتاج والتسليم بالأرقام الدقيقة
- حساب الإجماليات والمعدلات اليومية والشهرية
- مقارنة البيانات بين الأشهر
- رصد أكبر العملاء استهلاكاً
- تحليل توزيع الكسرات (C150/C210/C250/C300/C350/WP/Screed)
- الإجابة باللغة التي يسألك بها المستخدم (عربي أو إنجليزي)

قواعد صارمة:
1. استخدم الأرقام الدقيقة من البيانات — لا تخمّن أبداً
2. صفوف "مضخة" كميتها 0 — لا تحتسبها في الإنتاج
3. اذكر دائماً من أي شيت/شهر جاءت البيانات
4. إذا البيانات غير موجودة قل ذلك صراحةً
5. في نهاية كل إجابة: البيانات من: [الشهر/الشيت]

التقارير المتاحة: {reports_summary}"""

# ── Handlers ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("غير مصرح."); return
    reports = load_all_reports()
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        f"مساعد إنتاج الباطون الجاهز\n\n"
        f"{len(reports)} تقرير محمّل\n\n"
        "اسأل أي شيء:\n"
        "- كم م³ سلّمنا في شهر مارس؟\n"
        "- شو أكثر عميل استهلك في أبريل؟\n"
        "- كم م³ كسر C300؟\n"
        "- اعطني الإنتاج اليومي لشهر مايو\n"
        "- قارن الإنتاج بين الأشهر\n\n"
        "/reports — التقارير\n"
        "/clear — مسح المحادثة\n"
        "/help — المساعدة"
    )

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    reports = load_all_reports()
    if not reports:
        await update.message.reply_text("لا توجد تقارير. أرسل ملف .xlsx للبدء."); return
    text = "التقارير المحمّلة:\n\n"
    for m, d in sorted(reports.items()):
        text += f"{m} — {d['filename']}\n{d['uploaded_at']}\n\n"
    await update.message.reply_text(text)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    clear_db(update.effective_user.id)
    await update.message.reply_text("تم مسح المحادثة!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        "الأوامر:\n"
        "/start — الترحيب\n"
        "/reports — التقارير المحمّلة\n"
        "/clear — مسح المحادثة\n"
        "/help — هذه الرسالة\n\n"
        "ارفع ملف .xlsx لإضافة تقرير\n"
        "اسأل بالعربي أو الإنجليزي"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("غير مصرح."); return
    if not is_admin(uid):
        await update.message.reply_text("رفع الملفات للمدير فقط."); return

    doc = update.message.document
    if not doc.file_name.lower().endswith('.xlsx'):
        await update.message.reply_text("أرسل ملف .xlsx فقط."); return

    await update.message.reply_text("جاري معالجة التقرير...")
    try:
        file       = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await file.download_as_bytearray())
        mk         = detect_month(doc.file_name)
        structured = extract_structured(file_bytes, doc.file_name)

        resp = client.messages.create(
            model="claude-haiku-4-5", max_tokens=600,
            messages=[{"role": "user", "content":
                f"لخّص تقرير إنتاج الباطون في 5-6 نقاط بالأرقام الدقيقة بالعربية:\n\n{structured[:8000]}"}])
        summary = resp.content[0].text

        save_report(mk, doc.file_name, structured, summary)
        await update.message.reply_text(
            f"تم حفظ التقرير: {doc.file_name}\n"
            f"الفترة: {mk}\n\n"
            f"ملخص:\n{summary}"
        )
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text(f"خطأ: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("غير مصرح."); return

    reports = load_all_reports()
    if not reports:
        await update.message.reply_text("لا توجد تقارير. أرسل ملف .xlsx أولاً."); return

    reports_data = ""
    for month, data in sorted(reports.items()):
        content = data.get('structured') or ''
        reports_data += f"\n\n{'='*50}\nREPORT: {month} — {data['filename']}\n{'='*50}\n"
        reports_data += content[:35000]

    system = SYSTEM.format(
        reports_summary="\n".join([f"- {m}: {d['filename']}" for m, d in sorted(reports.items())])
    ) + f"\n\n{reports_data}"

    history = load_history(uid)
    history.append({"role": "user", "content": update.message.text})
    save_msg(uid, "user", update.message.text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5", max_tokens=1200,
            system=system, messages=history)
        answer = response.content[0].text
        save_msg(uid, "assistant", answer)

        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                await update.message.reply_text(answer[i:i+4000])
        else:
            await update.message.reply_text(answer)
    except Exception as e:
        logger.error(f"Claude error: {e}")
        await update.message.reply_text(f"خطأ: {e}")

# ── Main ──────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("reports", list_reports))
    app.add_handler(CommandHandler("clear",   clear_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
