"""
بناء سياق الأسئلة — يوسّع البيانات حسب موضوع السؤال والكيانات المذكورة فيه.

بدل إرسال ملخّص ثابت، يُقرأ السؤال فيُحدَّد:
  • موضوعه: كميات، عملاء، سيارات، سائقون، مناطق
  • الكيانات المذكورة فيه بالاسم (عميل بعينه، خلاطة، سائق، منطقة)
ثم يُبنى سياق يحتوي التفاصيل اللازمة لتلك الزاوية عبر كل الأشهر.
"""

import re
import pandas as pd
import numpy as np
import analytics as A
import entities as E

MAX_ROWS = 40          # أقصى عدد صفوف لأي قائمة موسّعة
MATCH_MIN = 4          # أقل طول لمطابقة اسم كيان في السؤال

# كلمات شائعة لا تصلح وحدها لتمييز كيان
COMMON = {"شركه", "شركة", "مؤسسه", "مؤسسة", "موسسه", "للمقاولات", "للمقاوت",
          "المقاولات", "المقاوت", "الانشائيه", "الانشائية", "وشريكه",
          "واخوانه", "وشركاه", "للاسكان", "الاسكان", "للتجاره", "للتجارة",
          "العامه", "العامة", "المحدوده", "المحدودة", "ائتلاف", "مشروع",
          "ابو", "عبد", "عبدالله", "محمد", "احمد", "الدوليه", "الدولية"}

TOPICS = {
    "quantities": ["كمية", "كميات", "انتاج", "إنتاج", "م3", "متر", "امتار",
                   "حجم", "مبيعات", "تسوية", "صافي", "اتلاف", "إتلاف",
                   "راجع", "مرتجع", "حمولة", "حمولات"],
    "clients": ["عميل", "عملاء", "زبون", "زبائن", "شركة", "مؤسسة", "ائتلاف"],
    "trucks": ["سيارة", "سيارات", "خلاطة", "خلاطات", "مركبة", "اسطول",
               "أسطول", "مضخة", "مضخات", "ديزل", "وقود", "لتر", "استهلاك"],
    "drivers": ["سائق", "سائقين", "سواق", "سائقون", "نقلات", "حوافز",
                "مخصصات", "سروة", "سهرة"],
    "areas": ["منطقة", "مناطق", "موقع", "مواقع", "بعد", "مسافة"],
}


def detect_topics(question):
    q = str(question)
    hits = [t for t, words in TOPICS.items() if any(w in q for w in words)]
    return hits or ["quantities"]


# ────────────────────── مطابقة الكيانات المذكورة ──────────────────────

def _norm(s):
    return re.sub(r"[^\w\u0600-\u06FF]", "", E.norm(s))


def detect_entities(question, months):
    """يلاقي أسماء العملاء والسائقين والخلاطات والمناطق المذكورة في السؤال"""
    q = _norm(question)
    q_raw = str(question)
    found = {"client": [], "driver": [], "truck": [], "area": []}
    if not months:
        return found

    pools = {k: set() for k in found}
    for d, _, _ in months:
        prod = d[d["_qty"] > 0]
        for k in ("client", "driver", "truck", "area"):
            pools[k].update(prod["_" + k].dropna().unique().tolist())

    # أرقام الخلاطات تُطابق كنص مباشر
    for t in pools["truck"]:
        digits = re.sub(r"\D", "", str(t))
        if len(digits) >= 5 and digits in re.sub(r"\D", "", q_raw):
            found["truck"].append(t)

    for k in ("client", "driver", "area"):
        for name in pools[k]:
            key = _norm(name)
            if len(key) < MATCH_MIN:
                continue
            if key in q:
                found[k].append(name)
            else:
                # مطابقة جزئية بكلمات مميّزة فقط، لا بالكلمات الشائعة
                words = [w for w in str(name).split()
                         if len(w) > 3 and _norm(w) not in COMMON]
                distinct = [w for w in words[:4] if _norm(w) not in COMMON]
                hit = [w for w in distinct if _norm(w) in q]
                if len(hit) >= 2:
                    found[k].append(name)
                elif len(hit) == 1 and len(_norm(hit[0])) >= 5:
                    # كلمة مميّزة واحدة تكفي إن كانت نادرة في القائمة
                    rare = sum(1 for other in pools[k]
                               if _norm(hit[0]) in _norm(other))
                    if rare <= 3:
                        found[k].append(name)

    # إزالة التكرار: الأسماء المتطابقة بعد التوحيد تُعد كياناً واحداً
    for k in found:
        seen, uniq = set(), []
        for name in sorted(set(found[k]), key=lambda s: -len(str(s))):
            key = _norm(name)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(name)
        found[k] = uniq[:4]
    return found


# ────────────────────── ملفات الكيانات عبر الأشهر ──────────────────────

def entity_profile(name, kind, months):
    """تفصيل كيان واحد عبر كل الأشهر"""
    col = "_" + kind
    lines = [f"تفصيل {_kind_ar(kind)}: {name}"]
    total_all = 0.0
    for d, dz, (y, m) in months:
        prod = d[d["_qty"] > 0]
        sub = prod[prod[col] == name]
        if sub.empty:
            lines.append(f"  {A.MONTH_AR[m]} {y}: لا نشاط")
            continue
        vol = sub["_qty"].sum()
        total_all += vol
        parts = [f"{vol:,.1f} م3", f"{len(sub)} حركة",
                 f"متوسط {sub['_qty'].mean():.2f}",
                 f"<10م3: {(sub['_qty'] < 10).mean()*100:.0f}%",
                 f"{sub['_date'].nunique()} يوم"]
        if kind == "client":
            top_area = sub["_area"].value_counts().head(2).index.tolist()
            parts.append(f"سنداته {sub['_bond'].nunique()}")
            parts.append(f"مناطقه: {'، '.join(top_area)}")
        elif kind == "driver":
            parts.append(f"خلاطاته: {'، '.join(sub['_truck'].unique()[:3])}")
        elif kind == "truck":
            parts.append(f"سائقوه: {'، '.join(sub['_driver'].unique()[:3])}")
            if dz is not None and len(dz):
                key = re.sub(r"\D", "", str(name))
                if key in dz.index:
                    r = dz.loc[key]
                    parts.append(f"ديزل {r['liters']:,.0f} لتر / {r['km']:,.0f} كم"
                                 f" / {r['liters']/max(vol,1):.2f} لتر لكل م3")
        elif kind == "area":
            parts.append(f"عملاؤها {sub['_client'].nunique()}")
        per = sub["_period"].dropna()
        if len(per):
            top_per = per.value_counts().head(1)
            parts.append(f"أغلب صبّه في {top_per.index[0]} "
                         f"({top_per.iloc[0]/len(per)*100:.0f}%)")
        lines.append(f"  {A.MONTH_AR[m]} {y}: " + " | ".join(parts))
    lines.append(f"  الإجمالي عبر الأشهر: {total_all:,.1f} م3")
    return "\n".join(lines)


def _kind_ar(kind):
    return {"client": "العميل", "driver": "السائق",
            "truck": "الخلاطة", "area": "المنطقة"}[kind]


# ────────────────────── جداول موسّعة حسب الموضوع ──────────────────────

def _trend(months, key, top=MAX_ROWS):
    """اتجاه الكميات لكل كيان عبر الأشهر"""
    frames = []
    for d, _, (y, m) in months:
        prod = d[d["_qty"] > 0]
        g = prod.groupby("_" + key)["_qty"].agg(["sum", "size", "mean"])
        g.columns = ["vol", "moves", "avg"]
        g["label"] = f"{A.MONTH_AR[m]}"
        frames.append(g.reset_index().rename(columns={"_" + key: "name"}))
    if not frames:
        return ""
    allf = pd.concat(frames)
    order = allf.groupby("name")["vol"].sum().sort_values(ascending=False)
    keep = order.head(top).index
    piv = allf[allf["name"].isin(keep)].pivot_table(
        index="name", columns="label", values="vol", aggfunc="sum")
    piv = piv.reindex(keep)
    cols = [f"{A.MONTH_AR[m]}" for _, _, (_, m) in months]
    cols = [c for c in cols if c in piv.columns]
    lines = ["الكميات بالم3 عبر الأشهر (" + " | ".join(cols) + " | الإجمالي):"]
    for name, r in piv.iterrows():
        vals = " | ".join(f"{r[c]:,.0f}" if pd.notna(r.get(c)) else "—"
                          for c in cols)
        lines.append(f"  {name}: {vals} | {order[name]:,.0f}")
    if len(order) > top:
        lines.append(f"  ... و{len(order)-top} آخرون غير معروضين")
    return "\n".join(lines)


def _detail_block(months, key, top=MAX_ROWS):
    """تفصيل شامل لآخر شهر لكل كيان"""
    d, dz, (y, m) = months[-1]
    prod = d[d["_qty"] > 0]
    g = prod.groupby("_" + key).agg(
        vol=("_qty", "sum"), moves=("_qty", "size"), avg=("_qty", "mean"),
        days=("_date", "nunique"))
    g["lt10"] = prod.groupby("_" + key)["_qty"].apply(
        lambda s: (s < 10).mean() * 100)
    g = g.sort_values("vol", ascending=False).head(top)
    lines = [f"تفصيل {A.MONTH_AR[m]} {y}:"]
    for name, r in g.iterrows():
        lines.append(f"  {name}: {r['vol']:,.1f} م3 | {int(r['moves'])} حركة | "
                     f"متوسط {r['avg']:.2f} | <10م3 {r['lt10']:.0f}% | "
                     f"{int(r['days'])} يوم")
    return "\n".join(lines)


def build_context(question, months, base_summary):
    """السياق النهائي: الملخّص الأساسي + توسعة حسب الموضوع + الكيانات المذكورة"""
    parts = [base_summary]
    ents = detect_entities(question, months)
    topics = list(detect_topics(question))
    kind_topic = {"client": "clients", "truck": "trucks",
                  "driver": "drivers", "area": "areas"}
    for kind, names in ents.items():
        if names and kind_topic[kind] not in topics:
            topics.append(kind_topic[kind])
    if len(topics) > 1 and "quantities" in topics and len(topics) > 2:
        topics.remove("quantities")

    # الكيانات المذكورة بالاسم لها الأولوية
    named = []
    for kind in ("client", "truck", "driver", "area"):
        for name in ents[kind]:
            named.append(entity_profile(name, kind, months))
    if named:
        parts.append("=" * 46 + "\nتفاصيل الكيانات المذكورة في السؤال\n"
                     + "=" * 46 + "\n" + "\n\n".join(named))

    key_of = {"clients": "client", "trucks": "truck",
              "drivers": "driver", "areas": "area"}
    for t in topics:
        if t == "quantities":
            block = _trend(months, "grade", 15)
            head = "الاتجاه الشهري حسب الرتبة"
        else:
            k = key_of[t]
            block = (_trend(months, k) + "\n\n" + _detail_block(months, k))
            head = {"clients": "العملاء", "trucks": "الخلاطات",
                    "drivers": "السائقون", "areas": "المناطق"}[t]
        if block.strip():
            parts.append("=" * 46 + f"\n{head} — تفصيل موسّع\n"
                         + "=" * 46 + "\n" + block)

    # الديزل يُضاف عند سؤال عن الخلاطات
    if "trucks" in topics:
        dl = _diesel_block(months)
        if dl:
            parts.append("=" * 46 + "\nالديزل عبر الأشهر\n" + "=" * 46 + "\n" + dl)

    return "\n\n".join(parts)


def _diesel_block(months):
    rows = []
    for d, dz, (y, m) in months:
        if dz is None or len(dz) == 0:
            continue
        e = A.truck_efficiency(d, dz)
        e = e[(e["truck"] != "0") & (e["km"] > 0) & (e["total"] > 0)]
        if e.empty:
            continue
        e = e.assign(l_per_m3=e["liters"] / e["total"],
                     km_per_m3=e["km"] / e["total"], label=A.MONTH_AR[m])
        rows.append(e[["truck", "label", "total", "liters", "km",
                       "cost", "l_per_m3", "km_per_m3"]])
    if not rows:
        return ""
    allr = pd.concat(rows)
    lines = []
    for m_label, sub in allr.groupby("label", sort=False):
        lines.append(f"{m_label}: {sub['liters'].sum():,.0f} لتر | "
                     f"{sub['cost'].sum():,.0f} دينار | "
                     f"{sub['liters'].sum()/sub['total'].sum():.2f} لتر لكل م3")
    lines.append("")
    lines.append("لتر لكل م3 لكل خلاطة (المعدل عبر الأشهر المتاحة):")
    g = allr.groupby("truck").agg(
        l=("liters", "sum"), v=("total", "sum"), km=("km", "sum"),
        c=("cost", "sum"), n=("label", "nunique"))
    g["l_per_m3"] = g["l"] / g["v"]
    g["km_per_m3"] = g["km"] / g["v"]
    for t, r in g.sort_values("l_per_m3", ascending=False).iterrows():
        lines.append(f"  {t}: {r['l_per_m3']:.2f} لتر/م3 | "
                     f"{r['km_per_m3']:.2f} كم/م3 | {r['v']:,.0f} م3 | "
                     f"{r['c']/r['v']:.2f} دينار/م3 | {int(r['n'])} أشهر")
    return "\n".join(lines)


# ────────────────────── استيضاح الطلبات الغامضة ──────────────────────

VAGUE = [
    r"^\s*(شو|ما|ايش|إيش)\s*(هو\s*)?(ال)?(وضع|أوضاع|اوضاع|حال|الحال|أخبار|اخبار|"
    r"الأمور|الامور|صار|الجديد|رأيك|رايك)\s*[؟?]?\s*$",
    r"^\s*(حلل|حلّل|احسب|اعطني|أعطني|معلومات|تفاصيل|تحليل|ملخص|ملخّص|"
    r"شرح|اشرح|قارن|قارنلي)\s*[؟?]?\s*$",
    r"^\s*(كيف|شلون)\s*(ال)?(وضع|حال|الحال|الأمور|الامور|الشغل)\s*[؟?]?\s*$",
]
_VAGUE_RE = [re.compile(p, re.I) for p in VAGUE]

# أسئلة تفترض فترة زمنية بعينها
PERIOD_WORDS = ["كم", "اجمالي", "إجمالي", "مجموع", "متوسط", "نسبة", "عدد",
                "اكثر", "أكثر", "اقل", "أقل", "افضل", "أفضل", "اسوأ", "اسوا",
                "اعلى", "أعلى", "ادنى", "أدنى", "قارن", "مقارنة"]


def is_vague(question):
    q = str(question).strip()
    return any(r.match(q) for r in _VAGUE_RE) or len(q) <= 4


ALL_MONTHS_RE = re.compile(
    r"كل\s*(ال)?(شهور|أشهر|اشهر)|جميع\s*(ال)?(شهور|أشهر|اشهر)|"
    r"عبر\s*(ال)?(شهور|أشهر|اشهر)|كل\s*(ال)?فترات")


def has_month(question, arabic_months):
    t = str(question)
    if ALL_MONTHS_RE.search(t):
        return True
    if re.search(r"\b(0?[1-9]|1[0-2])[/\-_](20\d{2})\b", t):
        return True
    if re.search(r"(شهر|شهور|m)\s*(0?[1-9]|1[0-2])\b", t, re.I):
        return True
    low = t.lower()
    return any(n in low for names in arabic_months.values() for n in names)


def clarify(question, month_names, months_available, has_history,
            entities=None, arabic_months=None):
    """
    يرجع نص سؤال استيضاحي، أو None إذا كان الطلب واضحاً بما يكفي.
    الاستيضاح يقتصر على الغموض الحقيقي حتى لا يصير مزعجاً.
    """
    q = str(question).strip()
    arabic_months = arabic_months or {}
    labels = [f"{month_names[m]} {y}" for y, m in months_available]

    # 1) طلب فضفاض بلا محتوى
    if is_vague(q):
        return ("سؤالك عام شوي — عن شو بالضبط بتحكي؟\n\n"
                "• *الكميات* — الإنتاج والتسوية والحمولات\n"
                "• *العملاء* — الأكبر، الأبطأ صبّاً، المتغيّرون\n"
                "• *الخلاطات* — الإنتاج والديزل والتعطّل\n"
                "• *السائقون* — النقلات والحوافز والحمولات\n"
                "• *المناطق* — الكميات وسرعة الصب والأوقات\n\n"
                f"وأي شهر؟ المتوفر: {'، '.join(labels)}")

    # 2) بلا شهر وبلا سياق سابق، والسؤال يفترض فترة
    if (not has_history and not has_month(q, arabic_months)
            and any(w in q for w in PERIOD_WORDS) and len(months_available) > 1):
        return (f"أي شهر تقصد؟ المتوفر: {'، '.join(labels)}\n\n"
                f"_أو قول «كل الشهور» للمقارنة._")

    # 3) اسم التبس على أكثر من كيان متشابه
    if entities:
        for kind, names in entities.items():
            if len(names) >= 2 and kind in ("driver", "client"):
                kind_ar = {"driver": "سائق", "client": "عميل"}[kind]
                opts = "\n".join(f"• {n}" for n in names[:4])
                return (f"في أكثر من {kind_ar} بهذا الاسم — أي واحد تقصد؟\n\n"
                        f"{opts}")
    return None


_UNIFY_CACHE = {}


def unify(months):
    """
    يوحّد أسماء العملاء والسائقين عبر الأشهر قبل أي مطابقة أو تجميع.
    الشيتات القديمة تحوي أسماء مقصوصة أو ناقصة الحروف، فتظهر الجهة الواحدة
    بأكثر من صورة. المناطق موحّدة أصلاً عند تجهيز البيانات.
    """
    if not months:
        return months
    key = tuple(sorted(ym for _, _, ym in months))
    if key in _UNIFY_CACHE:
        cached = _UNIFY_CACHE[key]
        if len(cached) == len(months):
            return cached

    out = [(d, dz, ym) for d, dz, ym in months]
    for field in ("client", "driver"):
        pools = [d["_" + field].dropna().unique().tolist() for d, _, _ in out]
        mapping = E.build_canonical(pools)
        out = [(E.apply_canonical(d, mapping, keys=(field,)), dz, ym)
               for d, dz, ym in out]
    _UNIFY_CACHE.clear()
    _UNIFY_CACHE[key] = out
    return out
