"""
ReadyMix Production Bot — يقرأ Google Sheets مباشرة ويولّد التقارير والرواتب.
"""

import os
import re
import json
import time
import asyncio
import logging
import tempfile
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

import analytics as A
import salary as S
import report_html as R
import salary_report as SR

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",
                    level=logging.INFO)
logger = logging.getLogger(__name__)
for noisy in ("httpx", "gspread"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# ──────────────────────────── الإعدادات ────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDS = os.environ["GOOGLE_CREDENTIALS"]
SHEET_ID = os.environ.get("SHEET_ID", "").strip()
SHEET_NAME = os.environ.get("SHEET_NAME", "ReadyMix_Production_Data")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
ALLOWED_USERS = [int(x) for x in
                 os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]
MAX_LEN = 3900

client = Anthropic(api_key=ANTHROPIC_KEY)
_cache = {}
_tabs = {"t": 0.0, "v": []}


def is_allowed(uid):
    return not ALLOWED_USERS or uid in ALLOWED_USERS


# ──────────────────────── قراءة Google Sheets ────────────────────────

def _spreadsheet():
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS), scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID) if SHEET_ID else gc.open(SHEET_NAME)


def list_tabs():
    now = time.time()
    if _tabs["v"] and now - _tabs["t"] < CACHE_TTL:
        return _tabs["v"]
    names = [ws.title for ws in _spreadsheet().worksheets()]
    _tabs.update({"t": now, "v": names})
    return names


def load_raw(tab):
    """الجدول الخام كما هو، مع الكاش"""
    now = time.time()
    if tab in _cache and now - _cache[tab][0] < CACHE_TTL:
        return _cache[tab][1]
    values = _spreadsheet().worksheet(tab).get_all_values()
    if not values:
        raise ValueError(f"التبويب {tab} فاضي")
    header = [(h or "").strip() or f"عمود_{i}" for i, h in enumerate(values[0])]
    seen, cols = {}, []
    for h in header:
        if h in seen:
            seen[h] += 1
            cols.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            cols.append(h)
    df = pd.DataFrame(values[1:], columns=cols)
    _cache[tab] = (now, df)
    logger.info(f"loaded {tab}: {len(df)} rows")
    return df


TAB_RE = re.compile(r"^m(0?[1-9]|1[0-2])[-_](20\d{2})$", re.I)


def month_tabs():
    """كل التبويبات الشهرية مع (سنة، شهر)"""
    out = []
    for t in list_tabs():
        m = TAB_RE.match(t.strip())
        if m:
            out.append((t, int(m.group(2)), int(m.group(1))))
    return sorted(out, key=lambda x: (x[1], x[2]))


def find_tab(year, month):
    for t, y, mo in month_tabs():
        if y == year and mo == month:
            return t
    raise gspread.WorksheetNotFound(f"m{month:02d}-{year}")


def get_month(tab, year):
    """يرجع (المُجهَّز، الخام، الديزل)"""
    raw = load_raw(tab)
    raw = raw.copy()
    raw.columns = [str(c).strip() for c in raw.columns]
    return A.prepare(raw, year), raw, A.parse_diesel(raw)


def all_kpis():
    """مؤشرات كل الشهور — أساس المقارنة بأفضل شهر"""
    ks = []
    for tab, y, mo in month_tabs():
        try:
            d, _, _ = get_month(tab, y)
            ks.append(A.kpis(d, y, mo))
        except Exception as e:
            logger.warning(f"kpis failed for {tab}: {e}")
    return ks


# ──────────────────────────── Telegram ────────────────────────────

def split_message(text, limit=MAX_LEN):
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


async def send_text(update, text):
    for chunk in split_message(text):
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(chunk)
        await asyncio.sleep(0.25)


async def send_html(update, html, filename, caption=""):
    path = os.path.join(tempfile.gettempdir(), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(path, "rb") as f:
        await update.message.reply_document(document=f, filename=filename,
                                            caption=caption)
    try:
        os.remove(path)
    except OSError:
        pass


async def keep_typing(bot, chat_id, stop):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4.5)
        except asyncio.TimeoutError:
            pass


def busy(fn):
    """يظهر 'يكتب...' ويمسك الأخطاء"""
    async def wrapper(update, context):
        if not is_allowed(update.effective_user.id):
            return
        stop = asyncio.Event()
        task = asyncio.create_task(
            keep_typing(context.bot, update.effective_chat.id, stop))
        try:
            await fn(update, context)
        except gspread.WorksheetNotFound:
            tabs = ", ".join(t for t, _, _ in month_tabs())
            await update.message.reply_text(
                f"ما لقيت بيانات لهذا الشهر.\nالمتوفر: {tabs}")
        except Exception as e:
            logger.exception(fn.__name__)
            await update.message.reply_text(f"خطا: {e}")
        finally:
            stop.set()
            task.cancel()
    return wrapper


# ──────────────────────────── الأوامر ────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"اهلا {update.effective_user.first_name}!\n\n"
        "*مساعد انتاج الباطون الجاهز*\n\n"
        "*التقارير:*\n"
        "/report تموز — التقرير الشهري الكامل\n"
        "/salary تموز — كشف رواتب عنبر النقل\n"
        "/all تموز — التقريرين معاً\n\n"
        "*الادوات:*\n"
        "/months — الشهور المتوفرة\n"
        "/columns — فحص اعمدة الشيت\n"
        "/refresh — تحديث البيانات فورا\n\n"
        "*او اسأل مباشرة:*\n"
        "كم م3 انتجنا في تموز؟\n"
        "شو كلفة الديزل لكل متر؟\n"
        "مين اكثر سائق نقلات؟\n"
        "اي خلاطة تحت 500 متر؟",
        parse_mode=ParseMode.MARKDOWN)


@busy
async def months_cmd(update, context):
    lines = []
    for tab, y, mo in month_tabs():
        d, _, dz = get_month(tab, y)
        k = A.kpis(d, y, mo)
        lines.append(f"{A.MONTH_AR[mo]} {y} ({tab}): {k['total']:,.0f} م3 · "
                     f"{k['moves']:,} حركة" + (" · ديزل ✓" if len(dz) else ""))
    await send_text(update, "*الشهور المتوفرة:*\n" + "\n".join(lines))


@busy
async def columns_cmd(update, context):
    year, month = resolve(context.args)
    tab = find_tab(year, month)
    raw = load_raw(tab)
    cols = [str(c).strip() for c in raw.columns]
    missing = [v for v in A.COL.values() if v not in cols]
    dz = A.parse_diesel(raw.rename(columns=lambda c: str(c).strip()))
    await send_text(update,
        f"التبويب: {tab}\nالصفوف: {len(raw)}\n\n*الاعمدة:*\n"
        + "\n".join(f"- {c}" for c in cols)
        + f"\n\n*ناقص:* {missing or 'لا شيء ✅'}"
        + f"\n*جدول الديزل:* {len(dz)} مركبة"
        + f"\n*الراجع موثوق:* {'نعم ✅' if A.waste_reliable(year, month) else 'لا ⚠️'}")


@busy
async def refresh_cmd(update, context):
    _cache.clear()
    _tabs.update({"t": 0.0, "v": []})
    await update.message.reply_text("تم مسح الكاش — الطلب الجاي بيجيب بيانات جديدة")


def resolve(args):
    txt = " ".join(args) if args else ""
    return months_in(txt)[0] if txt else _resolve(txt)


def _resolve(text):
    t = str(text).translate(A.ARABIC_DIGITS).lower()
    today = dt.date.today()
    month = next((n for n, names in A.ARABIC_MONTHS.items()
                  if any(x in t for x in names)), None)
    if month is None:
        m = re.search(r"\bm?(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t)
        if m:
            return int(m.group(2)), int(m.group(1))
        month = today.month
    y = re.search(r"\b(20\d{2})\b", t)
    return (int(y.group(1)) if y else today.year), month



def months_in(text):
    """كل الشهور المذكورة في السؤال — تدعم أسئلة المقارنة"""
    t = str(text).translate(A.ARABIC_DIGITS).lower()
    year_m = re.search(r"\b(20\d{2})\b", t)
    default_year = int(year_m.group(1)) if year_m else dt.date.today().year

    found = []
    for n, names in A.ARABIC_MONTHS.items():
        for nm in names:
            if nm in t:
                found.append((default_year, n))
                break
    # صيغ رقمية: "شهر 7" و "شهر 6 مع 7" و "6 و 7"
    for m in re.finditer(r"(?:شهر|شهور|m)\s*(0?[1-9]|1[0-2])(?![\d])", t):
        found.append((default_year, int(m.group(1))))
        # أرقام إضافية بعدها مفصولة بأداة ربط أو مقارنة
        tail = t[m.end():m.end() + 40]
        for m2 in re.finditer(
                r"(?:مع|و|أو|او|مقابل|ضد|vs|,|-)\s*(0?[1-9]|1[0-2])(?![\d])", tail):
            found.append((default_year, int(m2.group(1))))
    for m in re.finditer(r"\bm?(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t):
        found.append((int(m.group(2)), int(m.group(1))))

    out = []
    for item in found:
        if item not in out:
            out.append(item)
    return out or [_resolve(text)]


@busy
async def report_cmd(update, context):
    year, month = resolve(context.args)
    tab = find_tab(year, month)
    await update.message.reply_text(
        f"جاري تجهيز تقرير {A.MONTH_AR[month]} {year}...")
    d, raw, dz = get_month(tab, year)
    ks = await asyncio.to_thread(all_kpis)
    html = await asyncio.to_thread(R.build, d, year, month, tab, ks, dz)
    k = A.kpis(d, year, month)
    await send_html(update, html, f"report_{tab}.html",
        f"تقرير {A.MONTH_AR[month]} {year}\n"
        f"{k['total']:,.1f} م3 · {k['moves']:,} حركة · متوسط {k['avg']:.2f}")


@busy
async def salary_cmd(update, context):
    year, month = resolve(context.args)
    tab = find_tab(year, month)
    await update.message.reply_text(
        f"جاري احتساب رواتب {A.MONTH_AR[month]} {year}...")
    d, raw, dz = get_month(tab, year)
    html = await asyncio.to_thread(SR.build, d, year, month, tab)
    st, _ = S.compute(d)
    ss = S.summary(st)
    pumps = S.compute_pumps(d, A)
    workers = S.compute_pump_workers(d, A)
    tot = ss["confirmed"] + pumps["total_operator"].sum() + workers["total"].sum()
    await send_html(update, html, f"salary_{tab}.html",
        f"رواتب {A.MONTH_AR[month]} {year}\n"
        f"سائقو الخلاطات {ss['confirmed']:,.0f} · "
        f"مشغّلو المضخات {pumps['total_operator'].sum():,.0f} · "
        f"عمّال المضخات {workers['total'].sum():,.0f}\n"
        f"الاجمالي {tot:,.2f} دينار")


@busy
async def all_cmd(update, context):
    await report_cmd(update, context)
    await salary_cmd(update, context)


SYSTEM_PROMPT = """أنت محلل خبير في إنتاج الخرسانة الجاهزة لدى شركة ألفا.

قواعد صارمة:
- استخدم الأرقام من الملخص المرفق فقط، لا تخترع ولا تقدّر أي رقم
- صفوف المضخة كميتها صفر ولا تُحتسب ضمن الإنتاج
- الراجع المطالب به هو خطأ حركة أي كلفة على الشركة، والراجع غير المطالب به مكسب
- إذا كان مكتوب أن بيانات الراجع غير موثوقة فلا تستخدمها إطلاقاً
- إذا المعلومة غير موجودة قل ذلك صراحة بدل التخمين
- جاوب مختصر ومباشر بنفس لغة السؤال، وبتنسيق Markdown بسيط
- اختم بسطر: البيانات من: [الشهر والسنة]"""


def ask_claude(question, summary):
    r = client.messages.create(
        model=MODEL, max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": f"سؤال المستخدم: {question}\n\nالبيانات:\n{summary}"}])
    return "".join(b.text for b in r.content if b.type == "text").strip()


@busy
async def handle_message(update, context):
    q = update.message.text
    wanted = months_in(q)[:3]          # حتى ثلاثة شهور في سؤال واحد
    blocks = []
    for year, month in wanted:
        try:
            tab = find_tab(year, month)
        except gspread.WorksheetNotFound:
            continue
        d, raw, dz = get_month(tab, year)
        blocks.append(await asyncio.to_thread(
            A.text_summary, d, year, month, tab, dz))
    if not blocks:
        raise gspread.WorksheetNotFound("no month")
    sep = "\n\n" + "=" * 50 + "\n\n"
    summary = sep.join(blocks)
    if len(blocks) > 1:
        summary = ("بيانات أكثر من شهر للمقارنة:\n\n" + summary)
    answer = await asyncio.to_thread(ask_claude, q, summary)
    await send_text(update, answer)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    for name, fn in [("start", start), ("help", start), ("report", report_cmd),
                     ("salary", salary_cmd), ("all", all_cmd),
                     ("months", months_cmd), ("columns", columns_cmd),
                     ("refresh", refresh_cmd)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
