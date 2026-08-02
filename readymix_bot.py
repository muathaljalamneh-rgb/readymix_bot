"""
ReadyMix Production Bot — نسخة مستقلة بدون n8n
يقرأ من Google Sheets مباشرة، يحسب التحاليل بـ pandas، ويجاوب عبر Claude.
"""

import os
import re
import json
import time
import asyncio
import logging
import datetime as dt

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ──────────────────────────── الإعدادات ────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]          # محتوى ملف الـ JSON
SHEET_NAME = os.environ.get("SHEET_NAME", "ReadyMix_Production_Data")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))     # ثانية
ALLOWED_USERS = [
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
MAX_LEN = 3900

anthropic_client = Anthropic(api_key=ANTHROPIC_KEY)
_cache = {}


def is_allowed(uid: int) -> bool:
    return not ALLOWED_USERS or uid in ALLOWED_USERS


# ──────────────────────── قراءة Google Sheets ────────────────────────

def _gspread_client():
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS), scopes=SCOPES
    )
    return gspread.authorize(creds)


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def to_num(v) -> float:
    """يحوّل أي قيمة لرقم — يتعامل مع الفواصل والأرقام العربية"""
    if v is None:
        return 0.0
    s = str(v).translate(ARABIC_DIGITS).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else 0.0


def _dedupe(headers):
    seen, out = {}, []
    for h in headers:
        h = (h or "").strip() or "عمود"
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def load_sheet(tab: str) -> pd.DataFrame:
    """يجيب التبويب مع كاش زمني"""
    now = time.time()
    if tab in _cache and now - _cache[tab][0] < CACHE_TTL:
        return _cache[tab][1]

    ws = _gspread_client().open(SHEET_NAME).worksheet(tab)
    values = ws.get_all_values()
    if not values:
        raise ValueError(f"التبويب {tab} فاضي")

    df = pd.DataFrame(values[1:], columns=_dedupe(values[0]))
    _cache[tab] = (now, df)
    logger.info(f"loaded {tab}: {len(df)} rows")
    return df


# ──────────────────────── التعرف على الأعمدة ────────────────────────

COLUMN_HINTS = {
    "qty":    ["الكمية", "كمية"],
    "grade":  ["نوع الكسر", "الكسر", "الخلطة", "الرتبة"],
    "client": ["اسم العميل", "العميل", "الزبون"],
    "truck":  ["رقم السيارة", "السيارة", "المركبة", "الخلاط"],
    "driver": ["اسم السائق", "السائق"],
    "area":   ["المنطقة", "الموقع", "المشروع"],
    "bond":   ["رقم البوند", "البوند", "السند", "الطلبية"],
    "waste":  ["الهدر", "المرتجع", "الراجع", "الرجيع"],
    "claim":  ["مطالب", "المطالبة"],
    "date":   ["التاريخ", "تاريخ"],
}


def detect_columns(df: pd.DataFrame) -> dict:
    cols = {}
    for key, hints in COLUMN_HINTS.items():
        found = None
        for hint in hints:
            for c in df.columns:
                if hint in str(c):
                    found = c
                    break
            if found:
                break
        cols[key] = found
    return cols


# ──────────────────────────── تحديد الشهر ────────────────────────────

ARABIC_MONTHS = {
    1: ["يناير", "كانون الثاني", "كانون ثاني", "january", "jan"],
    2: ["فبراير", "شباط", "february", "feb"],
    3: ["مارس", "اذار", "آذار", "march", "mar"],
    4: ["ابريل", "أبريل", "نيسان", "april", "apr"],
    5: ["مايو", "ايار", "أيار", "may"],
    6: ["يونيو", "حزيران", "june", "jun"],
    7: ["يوليو", "تموز", "july", "jul"],
    8: ["اغسطس", "أغسطس", "آب", "august", "aug"],
    9: ["سبتمبر", "ايلول", "أيلول", "september", "sep"],
    10: ["اكتوبر", "أكتوبر", "تشرين الاول", "تشرين أول", "october", "oct"],
    11: ["نوفمبر", "تشرين الثاني", "تشرين ثاني", "november", "nov"],
    12: ["ديسمبر", "كانون الاول", "كانون أول", "december", "dec"],
}


def resolve_tab(text: str) -> str:
    """يستخرج الشهر والسنة من السؤال ويرجع اسم التبويب mMM-YYYY"""
    t = str(text).translate(ARABIC_DIGITS).lower()
    today = dt.date.today()

    month = None
    for num, names in ARABIC_MONTHS.items():
        if any(n in t for n in names):
            month = num
            break

    if month is None:
        m = re.search(r"\bm?(0?[1-9]|1[0-2])[/\-](20\d{2})\b", t)
        if m:
            return f"m{int(m.group(1)):02d}-{int(m.group(2))}"
        month = today.month

    y = re.search(r"\b(20\d{2})\b", t)
    year = int(y.group(1)) if y else today.year
    return f"m{month:02d}-{year}"


# ──────────────────────────── التحاليل ────────────────────────────

def _group_stats(prod: pd.DataFrame, col, qty: str, top: int) -> str:
    if not col:
        return "غير متوفر في الشيت"
    rows = []
    for name, sub in prod.groupby(prod[col].replace("", "غير محدد")):
        total, n = sub[qty].sum(), len(sub)
        l10 = int((sub[qty] < 10).sum())
        l5 = int((sub[qty] < 5).sum())
        rows.append((name, total, n, l10, l5))
    rows.sort(key=lambda r: -r[1])
    return "\n".join(
        f"{name}: {total:.1f}م3 | {n} حركة | متوسط {total/n:.2f} | "
        f"<10م3: {l10} ({l10/n*100:.0f}%) | <5م3: {l5}"
        for name, total, n, l10, l5 in rows[:top]
    ) or "لا يوجد"


def build_summary(df: pd.DataFrame, tab: str) -> str:
    c = detect_columns(df)
    if not c["qty"]:
        raise ValueError("ما لقيت عمود 'الكمية' — استخدم /columns للتشخيص")

    d = df.copy()
    qty = c["qty"]
    d[qty] = d[qty].map(to_num)

    prod = d[d[qty] > 0]           # الإنتاج الفعلي فقط
    pumps = len(d) - len(prod)      # صفوف المضخة (الكمية = 0)

    total = prod[qty].sum()
    moves = len(prod)
    avg = total / moves if moves else 0
    lt10 = int((prod[qty] < 10).sum())
    lt5 = int((prod[qty] < 5).sum())

    # الهدر — مطالب وغير مطالب
    if c["waste"]:
        d[c["waste"]] = d[c["waste"]].map(to_num)
        w = d[d[c["waste"]] > 0]
        w_total = w[c["waste"]].sum()
        if c["claim"] and len(w):
            flag = w[c["claim"]].astype(str)
            claimed_mask = flag.str.contains("نعم|مطالب|yes", case=False, na=False) & \
                ~flag.str.contains("غير|لا |not|no", case=False, na=False)
            claimed = w.loc[claimed_mask, c["waste"]].sum()
            waste_txt = (
                f"إجمالي الهدر: {w_total:.1f}م3 "
                f"({w_total/max(total,1)*100:.2f}% من الإنتاج) | حالات: {len(w)}\n"
                f"- مطالب به من العميل: {claimed:.1f}م3\n"
                f"- غير مطالب به (خسارة): {w_total-claimed:.1f}م3"
            )
        else:
            waste_txt = (f"إجمالي الهدر: {w_total:.1f}م3 | حالات: {len(w)} "
                         f"(ما في عمود للمطالبة)")
    else:
        waste_txt = "غير متوفر في الشيت"

    # البوندات
    if c["bond"]:
        b = prod.groupby(prod[c["bond"]].replace("", "بدون"))[qty].agg(["sum", "count"])
        b = b.sort_values("sum", ascending=False)
        bond_txt = (
            f"عدد البوندات: {len(b)} | متوسط الكمية للبوند: {total/max(len(b),1):.1f}م3\n"
            + "\n".join(f"{i}: {r['sum']:.1f}م3 ({int(r['count'])} حركة)"
                        for i, r in b.head(10).iterrows())
        )
    else:
        bond_txt = "غير متوفر في الشيت"

    return f"""الشهر: {tab}

═══ ملخص الإنتاج ═══
إجمالي الإنتاج: {total:.1f} م3
عدد الحركات: {moves}
متوسط الحمولة: {avg:.2f} م3
حركات أقل من 10م3: {lt10} ({lt10/max(moves,1)*100:.1f}%)
حركات أقل من 5م3: {lt5} ({lt5/max(moves,1)*100:.1f}%)
حركات مضخة (كمية=0): {pumps}

═══ الهدر والمرتجع ═══
{waste_txt}

═══ البوندات ═══
{bond_txt}

═══ المناطق ═══
{_group_stats(prod, c["area"], qty, 15)}

═══ الكسرات ═══
{_group_stats(prod, c["grade"], qty, 15)}

═══ أكبر العملاء ═══
{_group_stats(prod, c["client"], qty, 10)}

═══ السيارات ═══
{_group_stats(prod, c["truck"], qty, 30)}

═══ السائقين ═══
{_group_stats(prod, c["driver"], qty, 30)}"""


# ──────────────────────────── Claude ────────────────────────────

SYSTEM_PROMPT = """أنت محلل خبير في إنتاج الخرسانة الجاهزة.

قواعد صارمة:
- استخدم الأرقام من الملخص المرفق فقط، لا تخترع ولا تقدّر أي رقم
- إذا المعلومة مكتوب عندها "غير متوفر" قل صراحة إنها مش موجودة بالشيت
- جاوب مختصر ومباشر بنفس لغة السؤال
- استخدم تنسيق Markdown بسيط (نجمة واحدة للتشديد)
- اختم بسطر: البيانات من: [الشهر]"""


def ask_claude(question: str, summary: str) -> str:
    resp = anthropic_client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"سؤال المستخدم: {question}\n\nملخص البيانات:\n{summary}"
        }],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ──────────────────────────── Telegram ────────────────────────────

def split_message(text: str, limit: int = MAX_LEN):
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


async def send_answer(update: Update, text: str):
    for chunk in split_message(text):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)
        await asyncio.sleep(0.25)


async def keep_typing(bot, chat_id, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "*مساعد انتاج الباطون الجاهز*\n\n"
        "امثلة:\n"
        "- كم م3 انتجنا في مايو؟\n"
        "- شو متوسط الحمولة في مارس؟\n"
        "- اكبر 10 عملاء في ابريل\n"
        "- تحليل السيارات / السائقين\n"
        "- الهدر المطالب وغير المطالب\n"
        "- تحليل البوندات / توزيع المناطق\n\n"
        "*الاوامر:*\n"
        "/report - التقرير الكامل\n"
        "/columns - فحص اعمدة الشيت\n"
        "/refresh - تحديث البيانات فورا\n\n"
        "_اذكر الشهر بسؤالك_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def columns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تشخيص: يعرض أسماء الأعمدة الفعلية وشو انلقط منها"""
    if not is_allowed(update.effective_user.id):
        return
    tab = resolve_tab(" ".join(context.args) if context.args else "")
    try:
        df = await asyncio.to_thread(load_sheet, tab)
        c = detect_columns(df)
        found = "\n".join(f"{k}: {v or 'ما انلقط ❌'}" for k, v in c.items())
        await send_answer(
            update,
            f"التبويب: {tab}\nالصفوف: {len(df)}\n\n"
            f"*الاعمدة الفعلية:*\n" + " | ".join(map(str, df.columns)) +
            f"\n\n*التعرف التلقائي:*\n{found}",
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")


async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    _cache.clear()
    await update.message.reply_text("تم مسح الكاش — السؤال الجاي بيجيب بيانات جديدة")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التقرير الكامل — محسوب محلياً بالكامل، بدون Claude: فوري ودقيق"""
    if not is_allowed(update.effective_user.id):
        return
    tab = resolve_tab(" ".join(context.args) if context.args else "")
    try:
        df = await asyncio.to_thread(load_sheet, tab)
        summary = await asyncio.to_thread(build_summary, df, tab)
        await send_answer(update, summary)
    except gspread.WorksheetNotFound:
        await update.message.reply_text(f"ما في تبويب اسمه {tab} في الشيت")
    except Exception as e:
        logger.exception("report failed")
        await update.message.reply_text(f"خطا: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    question = update.message.text
    stop = asyncio.Event()
    typing = asyncio.create_task(
        keep_typing(context.bot, update.effective_chat.id, stop)
    )

    try:
        tab = resolve_tab(question)
        df = await asyncio.to_thread(load_sheet, tab)
        summary = await asyncio.to_thread(build_summary, df, tab)
        answer = await asyncio.to_thread(ask_claude, question, summary)
        await send_answer(update, answer)
    except gspread.WorksheetNotFound:
        await update.message.reply_text(
            f"ما لقيت بيانات لهذا الشهر ({resolve_tab(question)}) في الشيت"
        )
    except Exception as e:
        logger.exception("query failed")
        await update.message.reply_text(f"خطا: {e}")
    finally:
        stop.set()
        typing.cancel()


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("columns", columns_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Bot (standalone) running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
