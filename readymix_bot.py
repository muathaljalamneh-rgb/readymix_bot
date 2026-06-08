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
                (uid, role, content))
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
                lines.append(f"SHEET: {sheet}  |  rows={len(df)}")
                lines.append(f"COLUMNS: {list(df.columns)}")
                lines.append(f"{'='*60}")

                qty_col    = find_col(df, ['الكمية','كمية','م3','m3','quantity'])
                grade_col  = find_col(df, ['نوع الكسر','الكسر','grade','كسر'])
                client_col = find_col(df, ['اسم العميل','العميل','client','زبون'])
                area_col   = find_col(df, ['المنطقة','منطقة','area','region'])
                driver_col = find_col(df, ['اسم السائق','السائق','سائق','driver'])
                truck_col  = find_col(df, ['رقم السيارة','السيارة','سيارة','truck','plate'])
                ret1_col   = find_col(df, ['الكمية الراجعة التي لم يطالب','لم يطالب'])
                ret2_col   = find_col(df, ['الكمية الراجعة طالب','طالب بها'])
                bond_col   = find_col(df, ['رقم السند','السند','bond','order'])

                if qty_col:
                    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
                if ret1_col:
                    df[ret1_col] = pd.to_numeric(df[ret1_col], errors='coerce').fillna(0)
                if ret2_col:
                    df[ret2_col] = pd.to_numeric(df[ret2_col], errors='coerce').fillna(0)

                # المضخة = كمية 0، الخلاط = كمية > 0
                if qty_col:
                    df_prod = df[df[qty_col] > 0].copy()
                else:
                    df_prod = df.copy()

                # ── الاجماليات ────────────────────────────────
                if qty_col:
                    total_prod  = df_prod[qty_col].sum()
                    total_moves = len(df_prod)
                    avg_load    = total_prod / total_moves if total_moves > 0 else 0
                    lt10_count  = len(df_prod[df_prod[qty_col] < 10])
                    lt5_count   = len(df_prod[df_prod[qty_col] < 5])
                    lt10_pct    = lt10_count / total_moves * 100 if total_moves > 0 else 0
                    lt5_pct     = lt5_count  / total_moves * 100 if total_moves > 0 else 0

                    lines.append(f"\n--- الاجماليات ---")
                    lines.append(f"اجمالي الكمية المنتجة: {total_prod:.1f} م3")
                    lines.append(f"عدد الحركات: {total_moves}")
                    lines.append(f"متوسط الحمولة: {avg_load:.2f} م3")
                    lines.append(f"حمولات اقل من 10م3: {lt10_count} ({lt10_pct:.1f}%)")
                    lines.append(f"حمولات اقل من 5م3: {lt5_count} ({lt5_pct:.1f}%)")

                # ── الهدر ─────────────────────────────────────
                lines.append(f"\n--- الهدر والارجاع ---")
                if ret1_col:
                    lines.append(f"راجعة لم يطالب بها العميل: {df[ret1_col].sum():.1f} م3")
                if ret2_col:
                    lines.append(f"راجعة طالب بها العميل: {df[ret2_col].sum():.1f} م3")

                # ── الكسرات ───────────────────────────────────
                if grade_col and qty_col:
                    try:
                        lines.append(f"\n--- توزيع الكسرات ---")
                        gs = df_prod.groupby(grade_col)[qty_col].sum().sort_values(ascending=False)
                        for g, v in gs.items():
                            if v > 0:
                                mv = len(df_prod[df_prod[grade_col] == g])
                                av = v / mv if mv > 0 else 0
                                lines.append(f"  {g}: {v:.1f}م3 | {mv} حركة | متوسط {av:.2f}م3")
                    except Exception as e:
                        logger.warning(f"grades: {e}")

                # ── العملاء ───────────────────────────────────
                if client_col and qty_col:
                    try:
                        lines.append(f"\n--- اكبر العملاء ---")
                        cs = df_prod.groupby(client_col)[qty_col].sum().sort_values(ascending=False).head(30)
                        for c, v in cs.items():
                            if v > 0:
                                lines.append(f"  {c}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"clients: {e}")

                # ── المناطق ───────────────────────────────────
                if area_col and qty_col:
                    try:
                        lines.append(f"\n--- المناطق ---")
                        ar = df_prod.groupby(area_col)[qty_col].sum().sort_values(ascending=False).head(20)
                        for a, v in ar.items():
                            if v > 0:
                                lines.append(f"  {a}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"areas: {e}")

                # ── السيارات ──────────────────────────────────
                if truck_col and qty_col:
                    try:
                        lines.append(f"\n--- تحليل السيارات ---")
                        stats = []
                        for t, g in df_prod.groupby(truck_col):
                            tot = g[qty_col].sum()
                            mv  = len(g)
                            av  = tot / mv if mv > 0 else 0
                            l10 = len(g[g[qty_col] < 10])
                            l5  = len(g[g[qty_col] < 5])
                            p10 = l10 / mv * 100 if mv > 0 else 0
                            stats.append({'t':t,'tot':tot,'mv':mv,'av':av,'l10':l10,'p10':p10,'l5':l5})
                        for r in sorted(stats, key=lambda x: x['tot'], reverse=True):
                            lines.append(f"  {r['t']}: {r['tot']:.1f}م3 | {r['mv']} حركة | متوسط {r['av']:.2f}م3 | اقل10م3={r['l10']}({r['p10']:.1f}%) | اقل5م3={r['l5']}")
                    except Exception as e:
                        logger.warning(f"trucks: {e}")

                # ── السائقين ──────────────────────────────────
                if driver_col and qty_col:
                    try:
                        lines.append(f"\n--- تحليل السائقين ---")
                        stats = []
                        for d, g in df_prod.groupby(driver_col):
                            tot = g[qty_col].sum()
                            mv  = len(g)
                            av  = tot / mv if mv > 0 else 0
                            l10 = len(g[g[qty_col] < 10])
                            l5  = len(g[g[qty_col] < 5])
                            p10 = l10 / mv * 100 if mv > 0 else 0
                            stats.append({'d':d,'tot':tot,'mv':mv,'av':av,'l10':l10,'p10':p10,'l5':l5})
                        for r in sorted(stats, key=lambda x: x['tot'], reverse=True):
                            lines.append(f"  {r['d']}: {r['tot']:.1f}م3 | {r['mv']} حركة | متوسط {r['av']:.2f}م3 | اقل10م3={r['l10']}({r['p10']:.1f}%) | اقل5م3={r['l5']}")
                    except Exception as e:
                        logger.warning(f"drivers: {e}")

                # ── السندات ───────────────────────────────────
                if bond_col and qty_col and client_col:
                    try:
                        lines.append(f"\n--- تحليل السندات ---")
                        stats = []
                        for bid, g in df_prod.groupby(bond_col):
                            stats.append({
                                'bond': bid,
                                'client': g[client_col].iloc[0],
                                'qty': g[qty_col].sum(),
                                'moves': len(g)
                            })
                        df_b = pd.DataFrame(stats).sort_values('qty', ascending=False)
                        lines.append(f"عدد السندات: {len(df_b)}")
                        lines.append(f"متوسط كمية السند: {df_b['qty'].mean():.1f} م3")
                        lines.append(f"اكبر 10 سندات:")
                        for _, r in df_b.head(10).iterrows():
                            lines.append(f"  سند {r['bond']} | {r['client']} | {r['qty']:.1f}م3 | {r['moves']} خلاط")
                    except Exception as e:
                        logger.warning(f"bonds: {e}")

                # ── البيانات الخام ────────────────────────────
                lines.append(f"\n--- البيانات الخام (اول 500 صف) ---")
                lines.append(df.head(500).to_string(index=False, max_cols=20))

            except Exception as e:
                logger.warning(f"Sheet '{sheet}': {e}")
                lines.append(f"SHEET {sheet} - خطا: {e}")

        text = "\n".join(lines)
        return text[:120000]

    except Exception as e:
        return f"خطا في قراءة الملف: {e}"


def build_quick_summary(file_bytes: bytes) -> str:
    """يحسب الأرقام الحقيقية مباشرة ويبني ملخص دقيق."""
    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        summary_lines = []

        for sheet in xl.sheet_names:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
            df.dropna(how='all', inplace=True)

            qty_col    = find_col(df, ['الكمية','كمية','م3','m3','quantity'])
            grade_col  = find_col(df, ['نوع الكسر','الكسر','grade','كسر'])
            client_col = find_col(df, ['اسم العميل','العميل','client','زبون'])
            ret1_col   = find_col(df, ['الكمية الراجعة التي لم يطالب','لم يطالب'])
            ret2_col   = find_col(df, ['الكمية الراجعة طالب','طالب بها'])

            if not qty_col:
                continue

            df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
            df_prod = df[df[qty_col] > 0].copy()

            total   = df_prod[qty_col].sum()
            moves   = len(df_prod)
            avg     = total / moves if moves > 0 else 0
            lt10    = len(df_prod[df_prod[qty_col] < 10])
            lt5     = len(df_prod[df_prod[qty_col] < 5])
            lt10p   = lt10 / moves * 100 if moves > 0 else 0
            lt5p    = lt5  / moves * 100 if moves > 0 else 0

            summary_lines.append(f"شيت: {sheet}")
            summary_lines.append(f"- اجمالي الانتاج: {total:.1f} م3")
            summary_lines.append(f"- عدد الحركات: {moves}")
            summary_lines.append(f"- متوسط الحمولة: {avg:.2f} م3")
            summary_lines.append(f"- حمولات اقل من 10م3: {lt10} ({lt10p:.1f}%)")
            summary_lines.append(f"- حمولات اقل من 5م3: {lt5} ({lt5p:.1f}%)")

            if ret1_col:
                df[ret1_col] = pd.to_numeric(df[ret1_col], errors='coerce').fillna(0)
                summary_lines.append(f"- راجعة لم يطالب بها: {df[ret1_col].sum():.1f} م3")
            if ret2_col:
                df[ret2_col] = pd.to_numeric(df[ret2_col], errors='coerce').fillna(0)
                summary_lines.append(f"- راجعة طالب بها: {df[ret2_col].sum():.1f} م3")

            if grade_col:
                gs = df_prod.groupby(grade_col)[qty_col].sum().sort_values(ascending=False).head(5)
                summary_lines.append(f"- اكثر الكسرات: " + " | ".join([f"{g}:{v:.0f}م3" for g,v in gs.items()]))

            if client_col:
                cs = df_prod.groupby(client_col)[qty_col].sum().sort_values(ascending=False).head(3)
                summary_lines.append(f"- اكبر عملاء: " + " | ".join([f"{c}:{v:.0f}م3" for c,v in cs.items()]))

        return "\n".join(summary_lines)

    except Exception as e:
        return f"خطا في بناء الملخص: {e}"


def detect_month(fn: str) -> str:
    months = {
        'jan':'01','feb':'02','mar':'03','apr':'04','may':'05','jun':'06',
        'jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12',
        'يناير':'01','فبراير':'02','مارس':'03','ابريل':'04','أبريل':'04',
        'مايو':'05','يونيو':'06','يوليو':'07','اغسطس':'08','أغسطس':'08',
        'سبتمبر':'09','اكتوبر':'10','أكتوبر':'10','نوفمبر':'11','ديسمبر':'12',
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

SYSTEM = """انت مساعد خبير في تحليل بيانات انتاج وتسليم الباطون الجاهز.

البيانات محسوبة ومعالجة مسبقا — استخدم الارقام الموجودة فقط ولا تخمن.

تقدر تجيب على:
1. اجمالي الكمية المنتجة (م3)
2. عدد الحركات ومتوسط الحمولة
3. الحمولات اقل من 10م3 و 5م3
4. الهدر والارجاع
5. توزيع الكسرات
6. اكبر العملاء
7. توزيع المناطق
8. تحليل كل سيارة وسائق
9. تحليل السندات
10. مقارنة بين الاشهر

قواعد:
- الارقام الدقيقة فقط من البيانات
- اذكر من اي شيت/شهر
- اذا غير موجود قل ذلك
- في نهاية كل اجابة: البيانات من: [الشهر]
- نفس لغة السؤال

التقارير: {reports_summary}"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("غير مصرح."); return
    reports = load_all_reports()
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "مساعد انتاج الباطون الجاهز\n\n"
        f"{len(reports)} تقرير محمّل\n\n"
        "اسأل مثلا:\n"
        "- كم م3 انتجنا؟\n"
        "- متوسط الحمولة؟\n"
        "- تحليل السيارات\n"
        "- تحليل السائقين\n"
        "- توزيع الكسرات\n"
        "- اكبر 10 عملاء\n"
        "- قارن بين الاشهر\n\n"
        "/reports - التقارير\n"
        "/clear - مسح المحادثة"
    )

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    reports = load_all_reports()
    if not reports:
        await update.message.reply_text("لا توجد تقارير."); return
    text = "التقارير:\n\n"
    for m, d in sorted(reports.items()):
        text += f"{m} - {d['filename']}\n{d['uploaded_at']}\n\n"
    await update.message.reply_text(text)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    clear_db(update.effective_user.id)
    await update.message.reply_text("تم مسح المحادثة!")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        "/start - الترحيب\n"
        "/reports - التقارير\n"
        "/clear - مسح المحادثة\n"
        "/help - المساعدة\n\n"
        "ارفع ملف .xlsx لاضافة تقرير"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("غير مصرح."); return
    if not is_admin(uid):
        await update.message.reply_text("رفع الملفات للمدير فقط."); return

    doc = update.message.document
    if not doc.file_name.lower().endswith('.xlsx'):
        await update.message.reply_text("ارسل ملف .xlsx فقط."); return

    await update.message.reply_text("جاري معالجة التقرير...")
    try:
        file       = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await file.download_as_bytearray())
        mk         = detect_month(doc.file_name)
        structured = extract_structured(file_bytes, doc.file_name)

        # الملخص محسوب مباشرة من البيانات — لا يعتمد على Claude
        summary = build_quick_summary(file_bytes)

        save_report(mk, doc.file_name, structured, summary)
        await update.message.reply_text(
            f"تم حفظ التقرير: {doc.file_name}\n"
            f"الفترة: {mk}\n\n"
            f"ملخص:\n{summary}"
        )
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text(f"خطا: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text("غير مصرح."); return

    reports = load_all_reports()
    if not reports:
        await update.message.reply_text("لا توجد تقارير. ارسل ملف .xlsx اولا."); return

    reports_data = ""
    for month, data in sorted(reports.items()):
        content = data.get('structured') or ''
        reports_data += f"\n\n{'='*50}\nREPORT: {month} - {data['filename']}\n{'='*50}\n"
        reports_data += content[:40000]

    system = SYSTEM.format(
        reports_summary="\n".join([f"- {m}: {d['filename']}" for m, d in sorted(reports.items())])
    ) + f"\n\n{reports_data}"

    history = load_history(uid)
    history.append({"role": "user", "content": update.message.text})
    save_msg(uid, "user", update.message.text)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        response = client.messages.create(
            model="claude-haiku-4-5", max_tokens=1500,
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
        await update.message.reply_text(f"خطا: {e}")

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
