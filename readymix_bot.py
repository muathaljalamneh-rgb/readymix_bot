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
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]
SHEET_ID = os.environ.get("SHEET_ID", "").strip()
SHEET_NAME = os.environ.get("SHEET_NAME", "ReadyMix_Production_Data")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
ALLOWED_USERS = [
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
MAX_LEN = 3900

# الكميات الراجعة قبل هذا التاريخ مسجّلة بطريقة مختلفة وغير موثوقة
WASTE_RELIABLE_FROM = (2026, 6)

anthropic_client = Anthropic(api_key=ANTHROPIC_KEY)
_cache = {}
_tabs_cache = {"t": 0.0, "v": []}


def is_allowed(uid: int) -> bool:
    return not ALLOWED_USERS or uid in ALLOWED_USERS


# ──────────────────── أسماء الأعمدة الفعلية في الشيت ────────────────────

COL = {
    "ret_unclaimed": "الكمية الراجعة التي لم يطالب بها العميل",
    "ret_claimed":   "الكمية الراجعة طالب بها العميل",
    "time":          "وقت الصب",
    "phone":         "رقم هاتف العميل",
    "pour_type":     "طبيعة الصب",
    "plant":         "مكان اخراج البضاعة",
    "pours":         "عدد الصبات",
    "vehicle_type":  "نوع السيارة",
    "qty":           "الكمية",
    "grade":         "نوع الكسر",
    "driver":        "اسم السائق",
    "truck":         "رقم السيارة",
    "area":          "المنطقة",
    "client":        "اسم العميل",
    "bond":          "رقم السند",
}


# ──────────────────────── قراءة Google Sheets ────────────────────────

def _spreadsheet():
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID) if SHEET_ID else gc.open(SHEET_NAME)


ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def to_num(series: pd.Series) -> pd.Series:
    """تحويل عمود لأرقام — يتعامل مع الفواصل والأرقام العربية والفراغات"""
    s = series.astype(str).str.translate(ARABIC_DIGITS).str.replace(",", "", regex=False)
    return pd.to_numeric(s.str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce").fillna(0)


def list_tabs() -> list:
    """أسماء كل التبويبات، مع كاش"""
    now = time.time()
    if now - _tabs_cache["t"] < CACHE_TTL and _tabs_cache["v"]:
        return _tabs_cache["v"]
    tabs = [ws.title for ws in _spreadsheet().worksheets()]
    _tabs_cache.update({"t": now, "v": tabs})
    return tabs


def load_sheet(tab: str) -> pd.DataFrame:
    now = time.time()
    if tab in _cache and now - _cache[tab][0] < CACHE_TTL:
        return _cache[tab][1]

    values = _spreadsheet().worksheet(tab).get_all_values()
    if not values:
        raise ValueError(f"التبويب {tab} فاضي")

    header = [(h or "").strip() or f"عمود_{i}" for i, h in enumerate(values[0])]
    df = pd.DataFrame(values[1:], columns=header)
    _cache[tab] = (now, df)
    logger.info(f"loaded {tab}: {len(df)} rows")
    return df


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
MONTH_AR = dict(zip(
    range(1, 13),
    ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
     "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]
))


def resolve_period(text: str):
    """يرجع (سنة، شهر) من نص السؤال"""
    t = str(text).translate(ARABIC_DIGITS).lower()
    today = dt.date.today()

    month = next(
        (n for n, names in ARABIC_MONTHS.items() if any(x in t for x in names)), None
    )
    if month is None:
        m = re.search(r"\bm?(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t)
        if m:
            return int(m.group(2)), int(m.group(1))
        month = today.month

    y = re.search(r"\b(20\d{2})\b", t)
    return (int(y.group(1)) if y else today.year), month


def find_tab(year: int, month: int) -> str:
    """يلاقي التبويب مهما كان الفاصل (- أو _)"""
    tabs = list_tabs()
    for sep in ("-", "_"):
        name = f"m{month:02d}{sep}{year}"
        if name in tabs:
            return name
    # مطابقة مرنة كملاذ أخير
    target = f"m{month:02d}{year}"
    for t in tabs:
        if re.sub(r"[-_\s]", "", t.lower()) == target:
            return t
    raise gspread.WorksheetNotFound(f"m{month:02d}-{year}")


def waste_reliable(year: int, month: int) -> bool:
    return (year, month) >= WASTE_RELIABLE_FROM


# ──────────────────────────── التحاليل ────────────────────────────

def _stats(prod: pd.DataFrame, col: str, top: int) -> str:
    if col not in prod.columns:
        return "غير متوفر في الشيت"
    q = "_q"
    rows = []
    for name, sub in prod.groupby(prod[col].astype(str).str.strip().replace("", "غير محدد")):
        tot, n = sub[q].sum(), len(sub)
        l10 = int((sub[q] < 10).sum())
        l5 = int((sub[q] < 5).sum())
        rows.append((name, tot, n, l10, l5))
    rows.sort(key=lambda r: -r[1])
    out = "\n".join(
        f"{nm}: {tot:.1f}م3 | {n} حركة | متوسط {tot/n:.2f} | "
        f"<10م3: {l10} ({l10/n*100:.0f}%) | <5م3: {l5}"
        for nm, tot, n, l10, l5 in rows[:top]
    )
    if len(rows) > top:
        out += f"\n... (وباقي {len(rows)-top} غير معروضين)"
    return out or "لا يوجد"


def build_summary(df: pd.DataFrame, year: int, month: int, tab: str) -> str:
    d = df.copy()
    d.columns = [str(c).strip() for c in d.columns]

    if COL["qty"] not in d.columns:
        raise ValueError("ما لقيت عمود 'الكمية' — استخدم /columns للتشخيص")

    d["_q"] = to_num(d[COL["qty"]])
    prod = d[d["_q"] > 0].copy()          # الإنتاج الفعلي (الخلاطات)
    pumps = int((d["_q"] <= 0).sum())      # صفوف المضخة

    total = prod["_q"].sum()
    moves = len(prod)
    avg = total / moves if moves else 0
    lt10 = int((prod["_q"] < 10).sum())
    lt5 = int((prod["_q"] < 5).sum())

    # ── الكميات الراجعة ──
    if not waste_reliable(year, month):
        waste_txt = (
            "⚠️ بيانات الكميات الراجعة لهذا الشهر غير موثوقة "
            "(طريقة التسجيل قبل يونيو 2026 كانت مختلفة) — لا تُستخدم في التحليل"
        )
    elif COL["ret_claimed"] in d.columns:
        rc = to_num(d[COL["ret_claimed"]])
        ru = to_num(d[COL["ret_unclaimed"]]) if COL["ret_unclaimed"] in d.columns else pd.Series(0, index=d.index)
        tot_ret = rc.sum() + ru.sum()
        waste_txt = (
            f"إجمالي الراجع: {tot_ret:.1f}م3 ({tot_ret/max(total,1)*100:.2f}% من الإنتاج)\n"
            f"- طالب بها العميل: {rc.sum():.1f}م3 في {int((rc>0).sum())} حالة\n"
            f"- لم يطالب بها العميل (خسارة صافية): {ru.sum():.1f}م3 في {int((ru>0).sum())} حالة"
        )
    else:
        waste_txt = "غير متوفر في الشيت"

    # ── السندات (البوندات) ──
    if COL["bond"] in prod.columns:
        b = prod.groupby(prod[COL["bond"]].astype(str).str.strip())["_q"].agg(["sum", "count"])
        b = b[b.index != ""].sort_values("sum", ascending=False)
        bond_txt = (
            f"عدد السندات: {len(b)} | متوسط الكمية للسند: {total/max(len(b),1):.1f}م3 | "
            f"متوسط الحركات للسند: {moves/max(len(b),1):.1f}\n"
            + "\n".join(f"{i}: {r['sum']:.1f}م3 ({int(r['count'])} حركة)"
                        for i, r in b.head(10).iterrows())
        )
    else:
        bond_txt = "غير متوفر"

    # ── المضخات ──
    pump_txt = f"عدد حركات المضخة: {pumps}"
    if COL["vehicle_type"] in d.columns:
        vt = d[COL["vehicle_type"]].astype(str).str.strip().value_counts().to_dict()
        pump_txt += f" | توزيع نوع السيارة: {vt}"

    return f"""الشهر: {MONTH_AR.get(month, month)} {year}  (التبويب: {tab})

═══ ملخص الإنتاج ═══
إجمالي الإنتاج: {total:.1f} م3
عدد الحركات: {moves}
متوسط الحمولة: {avg:.2f} م3
حركات أقل من 10م3: {lt10} ({lt10/max(moves,1)*100:.1f}%)
حركات أقل من 5م3: {lt5} ({lt5/max(moves,1)*100:.1f}%)
{pump_txt}

═══ الكميات الراجعة ═══
{waste_txt}

═══ السندات ═══
{bond_txt}

═══ المناطق ═══
{_stats(prod, COL["area"], 20)}

═══ الكسرات (الرتب) ═══
{_stats(prod, COL["grade"], 15)}

═══ طبيعة الصب ═══
{_stats(prod, COL["pour_type"], 12)}

═══ مكان اخراج البضاعة ═══
{_stats(prod, COL["plant"], 10)}

═══ أكبر العملاء ═══
{_stats(prod, COL["client"], 15)}

═══ السيارات ═══
{_stats(prod, COL["truck"], 30)}

═══ السائقين ═══
{_stats(prod, COL["driver"], 30)}"""


# ──────────────────────────── Claude ────────────────────────────

SYSTEM_PROMPT = """أنت محلل خبير في إنتاج الخرسانة الجاهزة.

قواعد صارمة:
- استخدم الأرقام من الملخص المرفق فقط، لا تخترع ولا تقدّر أي رقم
- إذا كان مكتوب أن بيانات الكميات الراجعة غير موثوقة، لا تستخدمها إطلاقاً واذكر ذلك للمستخدم
- إذا المعلومة "غير متوفر" قل صراحة إنها مش موجودة بالشيت
- صفوف المضخة كميتها صفر ولا تُحتسب ضمن الإنتاج
- جاوب مختصر ومباشر بنفس لغة السؤال
- استخدم Markdown بسيط (نجمة واحدة للتشديد)
- اختم بسطر: البيانات من: [الشهر والسنة]"""


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
        "- كم م3 انتجنا في تموز؟\n"
        "- شو متوسط الحمولة في حزيران؟\n"
        "- اكبر 10 عملاء في تموز\n"
        "- تحليل السيارات / السائقين\n"
        "- الكميات الراجعة المطالب فيها\n"
        "- تحليل السندات / توزيع المناطق\n\n"
        "*الاوامر:*\n"
        "/report - التقرير الكامل\n"
        "/months - الشهور المتوفرة\n"
        "/columns - فحص اعمدة الشيت\n"
        "/refresh - تحديث البيانات فورا\n\n"
        "_اذكر الشهر بسؤالك_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def months_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    try:
        tabs = await asyncio.to_thread(list_tabs)
        await update.message.reply_text("الشهور المتوفرة:\n" + "\n".join(tabs))
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")


async def columns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    year, month = resolve_period(" ".join(context.args) if context.args else "")
    try:
        tab = await asyncio.to_thread(find_tab, year, month)
        df = await asyncio.to_thread(load_sheet, tab)
        cols = [str(c).strip() for c in df.columns]
        missing = [k for k, v in COL.items() if v not in cols]
        await send_answer(
            update,
            f"التبويب: {tab}\nالصفوف: {len(df)}\n\n"
            f"*الاعمدة:*\n" + "\n".join(f"- {c}" for c in cols) +
            f"\n\n*ناقص:* {missing or 'لا شيء ✅'}"
            f"\n*الكميات الراجعة موثوقة:* {'نعم ✅' if waste_reliable(year, month) else 'لا ⚠️'}",
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")


async def refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    _cache.clear()
    _tabs_cache.update({"t": 0.0, "v": []})
    await update.message.reply_text("تم مسح الكاش — السؤال الجاي بيجيب بيانات جديدة")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التقرير الكامل — محسوب محلياً بالكامل: فوري ودقيق بدون Claude"""
    if not is_allowed(update.effective_user.id):
        return
    year, month = resolve_period(" ".join(context.args) if context.args else "")
    try:
        tab = await asyncio.to_thread(find_tab, year, month)
        df = await asyncio.to_thread(load_sheet, tab)
        summary = await asyncio.to_thread(build_summary, df, year, month, tab)
        await send_answer(update, summary)
    except gspread.WorksheetNotFound:
        tabs = await asyncio.to_thread(list_tabs)
        await update.message.reply_text(
            f"ما في بيانات لشهر {MONTH_AR.get(month, month)} {year}\n"
            f"المتوفر: {', '.join(tabs)}"
        )
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
        year, month = resolve_period(question)
        tab = await asyncio.to_thread(find_tab, year, month)
        df = await asyncio.to_thread(load_sheet, tab)
        summary = await asyncio.to_thread(build_summary, df, year, month, tab)
        answer = await asyncio.to_thread(ask_claude, question, summary)
        await send_answer(update, answer)
    except gspread.WorksheetNotFound:
        tabs = await asyncio.to_thread(list_tabs)
        await update.message.reply_text(
            f"ما لقيت بيانات لهذا الشهر\nالمتوفر: {', '.join(tabs)}"
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
    app.add_handler(CommandHandler("months", months_cmd))
    app.add_handler(CommandHandler("columns", columns_cmd))
    app.add_handler(CommandHandler("refresh", refresh_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Bot (standalone) running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
