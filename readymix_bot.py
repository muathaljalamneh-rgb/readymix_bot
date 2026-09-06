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
import report_html as R
import insights as I
import narrative as N
import qa as Q

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
MODEL_LIGHT = os.environ.get("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")
MODEL_HEAVY = os.environ.get("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-6")
NARRATIVE = os.environ.get("NARRATIVE", "1") != "0"
CLARIFY = os.environ.get("CLARIFY", "1") != "0"


def is_all_months(text):
    return bool(re.search(r"كل\s*(ال)?(شهور|أشهر|اشهر)|جميع\s*(ال)?(شهور|أشهر|اشهر)",
                          str(text)))
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
ALLOWED_USERS = [int(x) for x in
                 os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly",
          "https://www.googleapis.com/auth/drive.readonly"]
MAX_LEN = 3900

client = Anthropic(api_key=ANTHROPIC_KEY)
_cache = {}
_tabs = {"t": 0.0, "v": []}

# ذاكرة المحادثة لكل مستخدم: آخر جولات السؤال والجواب
from collections import deque
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "6"))
_history = {}
_last_months = {}          # آخر شهور استُخدمت في كل محادثة


def history_of(chat_id):
    if chat_id not in _history:
        _history[chat_id] = deque(maxlen=HISTORY_TURNS * 2)
    return _history[chat_id]


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
            tabs = "، ".join(f"{A.MONTH_AR[m]} {y}" for _, y, m in month_tabs())
            await update.message.reply_text(
                f"ما لقيت بيانات للشهر المطلوب.\nالمتوفر: {tabs}")
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
        "*ملفات HTML — اكتب الطلب لوحده:*\n"
        "/report تموز — التقرير الشهري الكامل\n"
        "_او بدون شرطة: «تقرير تموز»_\n\n"
        "*الادوات:*\n"
        "/insights تموز — التحوّلات المرصودة فقط\n"
        "/months — الشهور المتوفرة\n"
        "/columns — فحص اعمدة الشيت\n"
        "/refresh — تحديث البيانات فورا\n"
        "/reset — بدء محادثة جديدة\n\n"
        "*محادثة — الجواب بيجي رسالة، والبوت بيتذكر السياق:*\n"
        "الكميات: كم م3 انتجنا في تموز؟\n"
        "العملاء: شو وضع شركة المهندس عبر الشهور؟\n"
        "الخلاطات: اي خلاطة استهلاكها عالي وليش؟\n"
        "السائقين: مين اكثر سائق نقلات؟\n"
        "المناطق: اي منطقة الصب فيها ابطأ؟\n\n"
        "_وبتقدر تكمل: «وشو عن حزيران؟» بدون ما تعيد السؤال_",
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


def latest_month():
    """آخر شهر متوفر في الشيت — لا شهر اليوم، فقد لا يكون له تبويب بعد"""
    try:
        tabs = month_tabs()
        if tabs:
            _, y, m = tabs[-1]
            return y, m
    except Exception as e:
        logger.warning(f"latest_month: {e}")
    today = dt.date.today()
    return today.year, today.month


def _resolve(text):
    t = str(text).translate(A.ARABIC_DIGITS).lower()
    month = next((n for n, names in A.ARABIC_MONTHS.items()
                  if any(x in t for x in names)), None)
    if month is None:
        m = re.search(r"\bm?(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t)
        if m:
            return int(m.group(2)), int(m.group(1))
        return latest_month()          # الافتراضي: آخر شهر فيه بيانات
    y = re.search(r"\b(20\d{2})\b", t)
    ly, _ = latest_month()
    return (int(y.group(1)) if y else ly), month



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
    t_noplate = re.sub(r"\b\d{2}-\d{4,}\b", " ", t)      # 60-81527 ليست شهراً
    for m in re.finditer(r"(?:شهر|شهور|m)\s*(0?[1-9]|1[0-2])(?![\d])", t_noplate):
        found.append((default_year, int(m.group(1))))
        # أرقام إضافية بعدها مفصولة بأداة ربط أو مقارنة
        tail = t[m.end():m.end() + 40]
        for m2 in re.finditer(
                r"(?:مع|و|أو|او|مقابل|ضد|vs|,|-)\s*(0?[1-9]|1[0-2])(?![\d])", tail):
            found.append((default_year, int(m2.group(1))))
    for m in re.finditer(r"\bm?(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t_noplate):
        found.append((int(m.group(2)), int(m.group(1))))

    out = []
    for item in found:
        if item not in out:
            out.append(item)
    return out or [_resolve(text)]



# ── توجيه الرسائل النصية إلى الأوامر ──
_KW_REPORT = re.compile(r"تقرير|التقرير|ريبورت|report", re.I)
_KW_SALARY = re.compile(r"(?!x)x")   # معطّل: الحوافز تُبنى لاحقاً بنظام مستقل
_KW_INSIGHT = re.compile(r"تحو[لّ]ات|التحو[لّ]ات|اكتشاف|اكتشافات|تغي[يّ]رات", re.I)

# كلمات تُحذف قبل الفحص: أدوات طلب وأسماء شهور وأرقام
_FILLER = re.compile(
    r"\b(?:بدي|أبغى|ابغى|اعطني|أعطني|اعطيني|ابعتلي|ابعث|ارسل|أرسل|"
    r"طلع|طلعلي|جهز|جهزلي|اطبع|هات|please|send|give|me|the|for|of)\b",
    re.I)
_MONTH_WORDS = None


def _strip_known(text):
    """يحذف كلمات الطلب وأسماء الشهور والأرقام والرموز"""
    global _MONTH_WORDS
    if _MONTH_WORDS is None:
        names = [n for names in A.ARABIC_MONTHS.values() for n in names]
        _MONTH_WORDS = re.compile("|".join(sorted(map(re.escape, names),
                                                  key=len, reverse=True)), re.I)
    t = str(text)
    t = _KW_REPORT.sub(" ", t)
    t = _KW_SALARY.sub(" ", t)
    t = _KW_INSIGHT.sub(" ", t)
    t = _MONTH_WORDS.sub(" ", t)
    t = re.sub(r"\b(?:شهر|الشهر|شهور|لشهر|عن|في|ال|كامل|الكامل|كاملا|"
               r"من|هذا|هاد|m)\b", " ", t, flags=re.I)
    t = _FILLER.sub(" ", t)
    t = re.sub(r"[0-9\u0660-\u0669\-_/\\.،,؟?!:]+", " ", t)
    return re.sub(r"\s+", "", t)


def route_intent(text):
    """
    يرجع 'report' أو 'salary' أو 'insights' لطلب ملف، أو None لسؤال محادثة.

    الملف يُرسَل فقط إذا كانت الرسالة طلباً صرفاً: كلمة التقرير مع الشهر
    ولا شيء آخر. أي كلمة إضافية — اسم عميل أو خلاطة أو أداة استفهام —
    تجعلها سؤالاً يُجاب عليه برسالة نصية.
    """
    t = str(text).strip()
    if len(t) > 70:
        return None
    has = (_KW_SALARY.search(t) and "salary") or \
          (_KW_INSIGHT.search(t) and "insights") or \
          (_KW_REPORT.search(t) and "report")
    if not has:
        return None
    return has if len(_strip_known(t)) <= 2 else None


@busy
async def report_cmd(update, context):
    year, month = resolve(context.args)
    tab = find_tab(year, month)
    await update.message.reply_text(
        f"جاري تجهيز تقرير {A.MONTH_AR[month]} {year}...\n"
        "يقرأ كل الشهور للمقارنة ورصد التحوّلات، فقد يستغرق دقيقة.")
    d, raw, dz = get_month(tab, year)
    ks = await asyncio.to_thread(all_kpis)

    # الأشهر السابقة لرصد التحوّلات
    history = []
    for t2, y2, m2 in month_tabs():
        if (y2, m2) >= (year, month):
            continue
        try:
            hd, _, _ = get_month(t2, y2)
            history.append((hd, (y2, m2)))
        except Exception as e:
            logger.warning(f"history {t2}: {e}")

    findings = await asyncio.to_thread(I.detect, d, (year, month), history)

    # كل الشهور لتحليل الديزل متعدد الفترات
    months_data = []
    for t2, y2, m2 in month_tabs():
        if (y2, m2) > (year, month):
            continue
        try:
            hd, _, hdz = get_month(t2, y2)
            months_data.append((hd, hdz, (y2, m2)))
        except Exception as e:
            logger.warning(f"months_data {t2}: {e}")

    # تحوّلات الديزل عند توفّر بيانات لأشهر سابقة
    try:
        cur_e = A.truck_efficiency(d, dz) if dz is not None and len(dz) else None
        if cur_e is not None and not cur_e.empty:
            cur_e = cur_e[(cur_e["truck"] != "0") & (cur_e["km"] > 0)]
        hist_e = []
        for t2, y2, m2 in month_tabs():
            if (y2, m2) >= (year, month):
                continue
            hd, hraw, hdz = get_month(t2, y2)
            if hdz is None or not len(hdz):
                continue
            he = A.truck_efficiency(hd, hdz)
            he = he[(he["truck"] != "0") & (he["km"] > 0)]
            if not he.empty:
                hist_e.append((he, (y2, m2)))
        if cur_e is not None and hist_e:
            findings = findings + I.detect_diesel(cur_e, (year, month), hist_e)
    except Exception as e:
        logger.warning(f"diesel insights: {e}")

    narr = None
    if NARRATIVE:
        narr = await asyncio.to_thread(
            N.build, client, d, year, month, ks, findings, dz, MODEL_HEAVY)

    html = await asyncio.to_thread(
        R.build, d, year, month, tab, ks, dz, findings, narr, months_data)
    k = A.kpis(d, year, month)
    await send_html(update, html, f"report_{tab}.html",
        f"تقرير {A.MONTH_AR[month]} {year}\n"
        f"{k['total']:,.1f} م3 · {k['moves']:,} حركة · متوسط {k['avg']:.2f}\n"
        f"{len(findings)} تحوّل مرصود مقارنةً بالأشهر السابقة\n\n"
        "افتح الملف في المتصفح. لحفظه PDF: قائمة المتصفح ← طباعة ← حفظ بصيغة PDF.")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ محادثة جديدة بلا سياق سابق"""
    if not is_allowed(update.effective_user.id):
        return
    _history.pop(update.effective_chat.id, None)
    _last_months.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        "بدأنا محادثة جديدة. الأسئلة الجاية بتنعامل كأنها أول سؤال.")


@busy
async def insights_cmd(update, context):
    """التحوّلات المرصودة بلا تقرير كامل — سريع ورخيص"""
    year, month = resolve(context.args)
    tab = find_tab(year, month)
    d, raw, dz = get_month(tab, year)
    history = []
    for t2, y2, m2 in month_tabs():
        if (y2, m2) < (year, month):
            hd, _, _ = get_month(t2, y2)
            history.append((hd, (y2, m2)))
    findings = await asyncio.to_thread(I.detect, d, (year, month), history)
    if not findings:
        await update.message.reply_text("لا تحوّلات شاذة مرصودة هذا الشهر")
        return
    icon = {"high": "🔴", "mid": "🟠", "good": "🟢"}
    txt = f"*تحوّلات {A.MONTH_AR[month]} {year}*\n\n" + "\n\n".join(
        f"{icon[f['severity']]} *{f['title']}*\n{f['detail']}" for f in findings)
    await send_text(update, txt)


SYSTEM_PROMPT = """أنت محلل إنتاج خرسانة جاهزة تتحاور مع مالك الشركة عبر تليجرام.

## قاعدة الإيجاز — الأهم
جاوب على ما سُئلت عنه فقط. لا تضف أقساماً ولا تحليلات ولا توصيات لم تُطلب منك.
المعطيات التي تصلك أوسع من السؤال بكثير؛ استخدم منها ما يخص السؤال واترك الباقي.

الطول المستهدف:
- سؤال عن رقم واحد: جملة أو جملتان
- سؤال عن ترتيب أو مقارنة: أعلى 3 إلى 5 بنود فقط، لا القائمة كاملة
- سؤال تحليلي «ليش» أو «كيف»: فقرة قصيرة أو ثلاث نقاط

ممنوع: المقدمات، إعادة صياغة السؤال، الخواتيم الإنشائية، عرض جداول كاملة،
إضافة سياق «للفائدة»، اقتراح تحليلات إضافية، أو تكرار ما قلته في رد سابق.

## الدقة
- كل رقم من المعطيات حرفياً. لا تحسب ولا تقدّر ولا تجمع من عندك.
- صفوف المضخة كميتها صفر ولا تدخل في الإنتاج.
- الراجع المطالب به خطأ حركة وكلفة، وغير المطالب به مكسب.
- إن لم تجد الرقم فقل «غير متوفر» ولا تخمّن.
- إن احتمل السؤال أكثر من قصد فاسأل سؤالاً واحداً قصيراً بخيارات مرقّمة، ولا
  تجب مع السؤال في الوقت نفسه.

## الشكل
عربية مباشرة، Markdown بسيط، أرقام بفواصل الآلاف. لا عناوين أقسام إلا إذا تجاوز
الرد ثلاث فقرات. لا تكتب سطر «البيانات من» — يضاف آلياً.
"""


HEAVY_HINTS = ("قارن", "مقارنة", "لماذا", "ليش", "حلل", "تحليل", "علاقة",
               "سبب", "اقترح", "توصية", "توصيات", "فسّر", "فسر", "استنتج",
               "خطة", "كيف اطور", "كيف أطور", "افضل طريقة", "أفضل طريقة")


def pick_model(question, n_months=1):
    """الأسئلة التحليلية أو متعددة الشهور تستحق النموذج الأقوى"""
    q = str(question)
    if n_months > 1 or len(q) > 160 or any(h in q for h in HEAVY_HINTS):
        return MODEL_HEAVY
    return MODEL_LIGHT


def ask_claude(question, summary, model=None, hist=None):
    msgs = list(hist or [])
    msgs.append({"role": "user",
                 "content": f"سؤال المستخدم: {question}\n\nالبيانات:\n{summary}"})
    r = client.messages.create(
        model=model or MODEL_LIGHT, max_tokens=1400, system=SYSTEM_PROMPT,
        messages=msgs)
    return "".join(b.text for b in r.content if b.type == "text").strip()


@busy
async def handle_message(update, context):
    q = update.message.text

    intent = route_intent(q)
    if intent and CLARIFY and not Q.has_month(q, A.ARABIC_MONTHS):
        avail = [(y, m) for _, y, m in month_tabs()]
        names = "، ".join(f"{A.MONTH_AR[m]} {y}" for y, m in avail)
        kind = {"report": "التقرير", "insights": "التحوّلات"}[intent]
        await update.message.reply_text(
            f"{kind} لأي شهر؟ المتوفر: {names}\n\n"
            f"_مثال: «{'تقرير' if intent=='report' else 'رواتب'} "
            f"{A.MONTH_AR[avail[-1][1]]}»_", parse_mode=ParseMode.MARKDOWN)
        return
    if intent:
        context.args = q.split()
        if intent == "report":
            await report_cmd(update, context)
        else:
            await insights_cmd(update, context)
        return

    chat_id = update.effective_chat.id
    hist = history_of(chat_id)

    # استيضاح الطلبات الغامضة قبل أي قراءة للبيانات
    if CLARIFY:
        avail = [(y, m) for _, y, m in month_tabs()]
        msg = Q.clarify(q, A.MONTH_AR, avail, bool(hist),
                        arabic_months=A.ARABIC_MONTHS)
        if msg is None and not is_all_months(q):
            # التباس في اسم كيان يحتاج تحديداً
            try:
                probe = []
                for t2, y2, m2 in month_tabs()[-2:]:
                    pd_, _, pdz = get_month(t2, y2)
                    probe.append((pd_, pdz, (y2, m2)))
                ents = Q.detect_entities(q, Q.unify(probe))
                msg = Q.clarify(q, A.MONTH_AR, avail, bool(hist), ents,
                                arabic_months=A.ARABIC_MONTHS)
            except Exception as e:
                logger.warning(f"clarify probe: {e}")
        if msg:
            await send_text(update, msg)
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": msg[:400]})
            return

    explicit = Q.has_month(q, A.ARABIC_MONTHS)
    if is_all_months(q):
        wanted = [(y, m) for _, y, m in month_tabs()]
    elif explicit:
        wanted = months_in(q)[:3]
    else:
        # سؤال متابعة: يرث شهور السؤال السابق إن وُجدت
        wanted = _last_months.get(chat_id) or months_in(q)[:3]
    _last_months[chat_id] = wanted
    blocks, months_ctx = [], []
    for year, month in wanted:
        try:
            tab = find_tab(year, month)
        except gspread.WorksheetNotFound:
            continue
        d, raw, dz = get_month(tab, year)
        blocks.append(await asyncio.to_thread(
            A.text_summary, d, year, month, tab, dz))
        months_ctx.append((d, dz, (year, month)))
    if not blocks:
        raise gspread.WorksheetNotFound("no month")

    # كل الأشهر متاحة للاتجاهات وتفاصيل الكيانات
    all_months = []
    for t2, y2, m2 in month_tabs():
        try:
            hd, _, hdz = get_month(t2, y2)
            all_months.append((hd, hdz, (y2, m2)))
        except Exception as e:
            logger.warning(f"ctx {t2}: {e}")

    sep = "\n\n" + "=" * 50 + "\n\n"
    base = sep.join(blocks)
    if len(blocks) > 1:
        base = "بيانات أكثر من شهر للمقارنة:\n\n" + base

    ctx_months = await asyncio.to_thread(Q.unify, all_months or months_ctx)
    summary = await asyncio.to_thread(Q.build_context, q, ctx_months, base)

    model = pick_model(q, len(blocks))
    answer = await asyncio.to_thread(ask_claude, q, summary, model, list(hist))
    if not explicit and not is_all_months(q):
        used = "، ".join(f"{A.MONTH_AR[m0]} {y0}" for y0, m0 in wanted)
        answer += f"\n\n_البيانات المستخدمة: {used}_"
    await send_text(update, answer)

    hist.append({"role": "user", "content": q})
    hist.append({"role": "assistant", "content": answer[:1500]})


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    for name, fn in [("start", start), ("help", start), ("report", report_cmd),
                     ("months", months_cmd), ("columns", columns_cmd),
                     ("insights", insights_cmd), ("reset", reset_cmd),
                     ("refresh", refresh_cmd)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("ReadyMix Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
