import os, io, logging, re
from datetime import datetime

import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import pandas as pd
import numpy as np

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

def safe_num(x):
    try:
        v = float(x)
        return None if v != v else v
    except:
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

                # Detect columns
                qty_col    = find_col(df, ['الكمية','كمية','م3','m3','quantity'])
                grade_col  = find_col(df, ['نوع الكسر','الكسر','grade','كسر'])
                client_col = find_col(df, ['اسم العميل','العميل','client','زبون'])
                area_col   = find_col(df, ['المنطقة','منطقة','area','region'])
                type_col   = find_col(df, ['مكان الإخراج','نوع الصب','إخراج','type'])
                date_col   = find_col(df, ['وقت وتاريخ','وقت','تاريخ','date','time'])
                driver_col = find_col(df, ['اسم السائق','السائق','سائق','driver'])
                truck_col  = find_col(df, ['رقم السيارة','السيارة','سيارة','truck','plate'])
                ret_col    = find_col(df, ['الكمية الراجعة','الراجع','راجع','return'])
                bond_col   = find_col(df, ['رقم السند','السند','bond','order'])
                dur_col    = find_col(df, ['مدة','duration','دقيقة','minutes'])

                # Clean numeric columns
                if qty_col:
                    df[qty_col] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0)
                if ret_col:
                    df[ret_col] = pd.to_numeric(df[ret_col], errors='coerce').fillna(0)

                # Separate pump rows from production rows
                if qty_col and type_col:
                    mask_pump = df[type_col].astype(str).str.contains('مضخة|pump', case=False, na=False)
                    df_prod   = df[~mask_pump].copy()
                    df_pump   = df[mask_pump].copy()
                elif qty_col:
                    df_prod   = df[df[qty_col] > 0].copy()
                    df_pump   = pd.DataFrame()
                else:
                    df_prod   = df.copy()
                    df_pump   = pd.DataFrame()

                # ── 1. اجمالي الكمية المنتجة ──────────────────
                if qty_col:
                    total_prod = df_prod[qty_col].sum()
                    total_all  = df[qty_col].sum()
                    lines.append(f"\n--- الاجماليات ---")
                    lines.append(f"اجمالي الكمية المنتجة (بدون مضخة): {total_prod:.1f} م3")
                    lines.append(f"اجمالي الكمية الكلية (شامل مضخة): {total_all:.1f} م3")

                # ── 2. عدد الحركات ────────────────────────────
                    total_moves = len(df_prod)
                    lines.append(f"عدد الحركات الكلي: {total_moves}")

                # ── 3. متوسط الحمولة ──────────────────────────
                    if total_moves > 0:
                        avg_load = total_prod / total_moves
                        lines.append(f"متوسط الحمولة: {avg_load:.2f} م3")

                # ── 4. الحمولات الصغيرة ───────────────────────
                    lt10 = df_prod[df_prod[qty_col] < 10]
                    lt5  = df_prod[df_prod[qty_col] < 5]
                    lt10_count = len(lt10)
                    lt5_count  = len(lt5)
                    lt10_pct   = (lt10_count / total_moves * 100) if total_moves > 0 else 0
                    lt5_pct    = (lt5_count  / total_moves * 100) if total_moves > 0 else 0
                    lines.append(f"عدد الحمولات اقل من 10 م3: {lt10_count} ({lt10_pct:.1f}%)")
                    lines.append(f"عدد الحمولات اقل من 5 م3: {lt5_count} ({lt5_pct:.1f}%)")

                # ── 5. الهدر والارجاع ─────────────────────────
                if ret_col:
                    total_ret = df[ret_col].sum()
                    lines.append(f"كمية الهدر او الارجاع: {total_ret:.1f} م3")

                # ── 6. تحليل وقت الصب ────────────────────────
                if date_col and qty_col:
                    try:
                        df_time = df_prod.copy()
                        df_time[date_col] = pd.to_datetime(df_time[date_col], errors='coerce')
                        valid_time = df_time[df_time[date_col].notna()]
                        invalid_time = df_time[df_time[date_col].isna()]
                        lines.append(f"\n--- تحليل وقت الصب ---")
                        lines.append(f"عدد القراءات الدقيقة لوقت الصب: {len(valid_time)}")
                        lines.append(f"عدد الصفوف بدون وقت صب واضح: {len(invalid_time)}")
                    except Exception as e:
                        logger.warning(f"time parse: {e}")

                # ── 7. تحليل مدة السند (دقيقة/م3) ────────────
                if date_col and qty_col and client_col and bond_col:
                    try:
                        df_bond = df_prod.copy()
                        df_bond[date_col] = pd.to_datetime(df_bond[date_col], errors='coerce')
                        df_bond[qty_col]  = pd.to_numeric(df_bond[qty_col], errors='coerce').fillna(0)

                        bond_stats = []
                        for bond_id, grp in df_bond.groupby(bond_col):
                            grp_sorted = grp.sort_values(date_col)
                            t_start = grp_sorted[date_col].min()
                            t_end   = grp_sorted[date_col].max()
                            if pd.isna(t_start) or pd.isna(t_end): continue
                            duration_min = (t_end - t_start).total_seconds() / 60
                            total_qty = grp_sorted[qty_col].sum()
                            client_name = grp_sorted[client_col].iloc[0] if client_col else ''
                            if total_qty > 0 and duration_min >= 0:
                                rate = duration_min / total_qty
                                bond_stats.append({
                                    'bond': bond_id,
                                    'client': client_name,
                                    'qty': total_qty,
                                    'duration': duration_min,
                                    'rate': rate
                                })

                        if bond_stats:
                            df_bonds = pd.DataFrame(bond_stats)
                            avg_dur  = df_bonds['duration'].mean()
                            avg_rate = df_bonds['rate'].mean()
                            over120  = df_bonds[df_bonds['duration'] > 120]
                            over240  = df_bonds[df_bonds['duration'] > 240]
                            slow10   = df_bonds[df_bonds['rate'] > 10]

                            lines.append(f"\n--- تحليل مدة السندات ---")
                            lines.append(f"متوسط مدة السند: {avg_dur:.1f} دقيقة")
                            lines.append(f"متوسط دقيقة/م3: {avg_rate:.2f}")
                            lines.append(f"سندات تجاوزت 120 دقيقة (وليست اعلى من 240): {len(over120[over120['duration'] <= 240])}")
                            lines.append(f"سندات تجاوزت 240 دقيقة: {len(over240)}")
                            lines.append(f"سندات ابطا من 10 د/م3: {len(slow10)}")

                            # افضل 10 عملاء بالصب (اقل دقيقة/م3)
                            if client_col:
                                client_rate = df_bonds.groupby('client')['rate'].mean().sort_values()
                                lines.append(f"\nافضل 10 عملاء بالصب (اقل دقيقة/م3):")
                                for c, r in client_rate.head(10).items():
                                    lines.append(f"  {c}: {r:.2f} د/م3")

                                # ابطا 10 عملاء
                                lines.append(f"\nابطا 10 عملاء بالصب (اكثر دقيقة/م3):")
                                for c, r in client_rate.tail(10).sort_values(ascending=False).items():
                                    lines.append(f"  {c}: {r:.2f} د/م3")

                    except Exception as e:
                        logger.warning(f"bond time analysis: {e}")

                # ── 8. تحليل السيارات ─────────────────────────
                if truck_col and qty_col:
                    try:
                        lines.append(f"\n--- تحليل السيارات ---")
                        truck_grp = df_prod.groupby(truck_col)
                        truck_stats = []
                        for truck, grp in truck_grp:
                            total = grp[qty_col].sum()
                            moves = len(grp)
                            avg   = total / moves if moves > 0 else 0
                            lt10c = len(grp[grp[qty_col] < 10])
                            lt5c  = len(grp[grp[qty_col] < 5])
                            lt10p = lt10c / moves * 100 if moves > 0 else 0
                            truck_stats.append({
                                'truck': truck, 'total': total, 'moves': moves,
                                'avg': avg, 'lt10': lt10c, 'lt10p': lt10p, 'lt5': lt5c
                            })
                        df_trucks = pd.DataFrame(truck_stats).sort_values('total', ascending=False)
                        for _, row in df_trucks.iterrows():
                            lines.append(
                                f"  {row['truck']}: اجمالي={row['total']:.1f}م3 | "
                                f"حركات={int(row['moves'])} | متوسط={row['avg']:.2f}م3 | "
                                f"اقل10م3={int(row['lt10'])}({row['lt10p']:.1f}%) | "
                                f"اقل5م3={int(row['lt5'])}"
                            )
                    except Exception as e:
                        logger.warning(f"truck analysis: {e}")

                # ── 9. تحليل السائقين ─────────────────────────
                if driver_col and qty_col:
                    try:
                        lines.append(f"\n--- تحليل السائقين ---")
                        drv_grp = df_prod.groupby(driver_col)
                        drv_stats = []
                        for drv, grp in drv_grp:
                            total = grp[qty_col].sum()
                            moves = len(grp)
                            avg   = total / moves if moves > 0 else 0
                            lt10c = len(grp[grp[qty_col] < 10])
                            lt5c  = len(grp[grp[qty_col] < 5])
                            lt10p = lt10c / moves * 100 if moves > 0 else 0
                            drv_stats.append({
                                'driver': drv, 'total': total, 'moves': moves,
                                'avg': avg, 'lt10': lt10c, 'lt10p': lt10p, 'lt5': lt5c
                            })
                        df_drvs = pd.DataFrame(drv_stats).sort_values('total', ascending=False)
                        for _, row in df_drvs.iterrows():
                            lines.append(
                                f"  {row['driver']}: اجمالي={row['total']:.1f}م3 | "
                                f"حركات={int(row['moves'])} | متوسط={row['avg']:.2f}م3 | "
                                f"اقل10م3={int(row['lt10'])}({row['lt10p']:.1f}%) | "
                                f"اقل5م3={int(row['lt5'])}"
                            )
                    except Exception as e:
                        logger.warning(f"driver analysis: {e}")

                # ── 10. توزيع الكسرات ─────────────────────────
                if grade_col and qty_col:
                    try:
                        lines.append(f"\n--- توزيع الكسرات ---")
                        gs = df_prod.groupby(grade_col)[qty_col].sum().sort_values(ascending=False)
                        for g, v in gs.items():
                            if v > 0:
                                lines.append(f"  {g}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"grade: {e}")

                # ── 11. اكبر العملاء ──────────────────────────
                if client_col and qty_col:
                    try:
                        lines.append(f"\n--- اكبر العملاء استهلاكا ---")
                        cs = df_prod.groupby(client_col)[qty_col].sum().sort_values(ascending=False).head(30)
                        for c, v in cs.items():
                            if v > 0:
                                lines.append(f"  {c}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"clients: {e}")

                # ── 12. توزيع المناطق ─────────────────────────
                if area_col and qty_col:
                    try:
                        lines.append(f"\n--- توزيع المناطق ---")
                        ar = df_prod.groupby(area_col)[qty_col].sum().sort_values(ascending=False).head(20)
                        for a, v in ar.items():
                            if v > 0:
                                lines.append(f"  {a}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"area: {e}")

                # ── 13. الانتاج اليومي ────────────────────────
                if date_col and qty_col:
                    try:
                        df_daily = df_prod.copy()
                        df_daily[date_col] = pd.to_datetime(df_daily[date_col], errors='coerce')
                        df_daily['day'] = df_daily[date_col].dt.date
                        daily = df_daily.groupby('day')[qty_col].sum().sort_index()
                        lines.append(f"\n--- الانتاج اليومي ---")
                        for d, v in daily.items():
                            if v > 0:
                                lines.append(f"  {d}: {v:.1f} م3")
                    except Exception as e:
                        logger.warning(f"daily: {e}")

                # ── Raw data ──────────────────────────────────
                lines.append(f"\n--- البيانات الخام (اول 500 صف) ---")
                lines.append(df.head(500).to_string(index=False, max_cols=20))

            except Exception as e:
                logger.warning(f"Sheet '{sheet}': {e}")
                lines.append(f"SHEET {sheet} — خطا في القراءة: {e}")

        text = "\n".join(lines)
        return text[:120000]

    except Exception as e:
        return f"خطا في قراءة الملف: {e}"


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

# ── System Prompt ─────────────────────────────────────────
SYSTEM = """انت مساعد خبير في تحليل بيانات انتاج وتسليم الباطون الجاهز.

لديك بيانات التقارير الشهرية كاملة ومعالجة مسبقا.

قدراتك:
1. اجمالي الكمية المنتجة (م3) - بدون صفوف المضخة
2. عدد الحركات الكلي
3. متوسط الحمولة (م3)
4. عدد الحمولات اقل من 10 م3 ونسبتها
5. عدد الحمولات اقل من 5 م3 ونسبتها
6. كمية الهدر او الارجاع (م3)
7. عدد القراءات الدقيقة لوقت الصب
8. عدد الصفوف بدون وقت صب واضح
9. متوسط مدة السند بالدقيقة
10. متوسط دقيقة لكل م3
11. عدد السندات التي تجاوزت 120 دقيقة وليست اعلى من 240 دقيقة
12. عدد السندات التي تجاوزت 240 دقيقة
13. عدد السندات ابطا من 10 د/م3
14. افضل 10 عملاء بالصب (اقل دقيقة/م3)
15. ابطا 10 عملاء بالصب (اكثر دقيقة/م3)
16. تحليل جميع السيارات: اجمالي - حركات - متوسط حمولة - نسبة اقل من 10م3 - اقل من 5م3
17. تحليل جميع السائقين: نفس معايير السيارات
18. توزيع الكسرات والخلطات (C150/C210/C250/C300/C350/WP/Screed/TRAWLING)
19. اكبر العملاء استهلاكا
20. الانتاج اليومي
21. مقارنة بين الاشهر

قواعد صارمة:
- استخدم الارقام الدقيقة من البيانات - لا تخمن ابدا
- صفوف المضخة لا تحتسب في الانتاج
- اذكر دائما من اي شيت/شهر جاءت البيانات
- اذا البيانات غير موجودة قل ذلك صراحة
- في نهاية كل اجابة: البيانات من: [الشهر/الشيت]
- الاجابة بنفس لغة السؤال (عربي او انجليزي)

التقارير المتاحة: {reports_summary}"""

# ── Handlers ──────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("غير مصرح."); return
    reports = load_all_reports()
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "مساعد انتاج الباطون الجاهز\n\n"
        f"{len(reports)} تقرير محمّل\n\n"
        "اسأل أي شيء مثل:\n"
        "- كم م3 سلمنا هذا الشهر؟\n"
        "- شو متوسط الحمولة؟\n"
        "- كم سند تجاوز 240 دقيقة؟\n"
        "- افضل 10 عملاء بالصب؟\n"
        "- تحليل السيارات\n"
        "- تحليل السائقين\n"
        "- توزيع الكسرات\n"
        "- الانتاج اليومي\n"
        "- قارن بين الاشهر\n\n"
        "/reports - التقارير\n"
        "/clear - مسح المحادثة\n"
        "/help - المساعدة"
    )

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    reports = load_all_reports()
    if not reports:
        await update.message.reply_text("لا توجد تقارير. ارسل ملف .xlsx للبدء."); return
    text = "التقارير المحملة:\n\n"
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
        "الاوامر:\n"
        "/start - الترحيب\n"
        "/reports - التقارير المحملة\n"
        "/clear - مسح المحادثة\n"
        "/help - هذه الرسالة\n\n"
        "ارفع ملف .xlsx لاضافة تقرير\n"
        "اسأل بالعربي او الانجليزي"
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

    await update.message.reply_text("جاري معالجة التقرير... انتظر لحظة.")
    try:
        file       = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await file.download_as_bytearray())
        mk         = detect_month(doc.file_name)
        structured = extract_structured(file_bytes, doc.file_name)

        resp = client.messages.create(
            model="claude-haiku-4-5", max_tokens=800,
            messages=[{"role": "user", "content":
                "لخص تقرير انتاج الباطون هذا في 6-8 نقاط بالارقام الدقيقة بالعربية، "
                "اذكر: الاجمالي، الحركات، متوسط الحمولة، الحمولات الصغيرة، "
                "الهدر، ومدة الصب:\n\n" + structured[:10000]}])
        summary = resp.content[0].text

        save_report(mk, doc.file_name, structured, summary)
        await update.message.reply_text(
            f"تم حفظ التقرير: {doc.file_name}\n"
            f"الفترة: {mk}\n\n"
            f"ملخص:\n{summary}"
        )
    except Exception as e:
        logger.error(f"Document error: {e}")
        await update.message.reply_text(f"خطا في معالجة الملف: {e}")

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
