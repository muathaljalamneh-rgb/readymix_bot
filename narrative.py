"""
طبقة السرد — تحوّل الأرقام والاكتشافات إلى قراءة تنفيذية وتوصيات.
الأرقام تُحسب إحصائياً قبل الوصول إلى النموذج؛ دوره التفسير والترتيب فقط.
"""

import html
import logging
import analytics as A
import insights as I
import salary as S

logger = logging.getLogger(__name__)

_NUM_LI = __import__("re").compile(r"^\d+\.\s*")

# توجيه الكلفة: مهمة معقّدة شهرية تستحق نموذجاً أقوى
MODEL_HEAVY = "claude-sonnet-4-6"
MODEL_LIGHT = "claude-haiku-4-5-20251001"

SYSTEM = """أنت محلل عمليات أول في شركة خرسانة جاهزة، تكتب القراءة الشهرية لمالك الشركة.

قواعد ملزمة:
- كل رقم تذكره يجب أن يكون منقولاً حرفياً من المعطيات. ممنوع الاشتقاق أو التقدير أو الجمع.
- إذا لم تجد رقماً يدعم فكرة، لا تذكرها إطلاقاً.
- لا تعِد سرد الجداول. اربط بين الملاحظات واستخرج ما لا يظهر بالنظر المباشر.
- ميّز بوضوح بين ما تثبته البيانات وما هو تفسير محتمل. استخدم صيغة الاحتمال للثاني.
- تجنّب المجاملات والعبارات العامة. المالك يعرف عمله، يريد ما لم ينتبه له.
- اكتب بالعربية الفصحى المباشرة، بلا مقدمات ولا خاتمة إنشائية.

الصيغة المطلوبة بالضبط:

## الخلاصة
ثلاث إلى أربع جمل تصف حال الشهر وأهم تحوّل فيه.

## ما يستحق الانتباه
ثلاث إلى خمس نقاط. كل نقطة: **عنوان قصير** ثم سطران يشرحان الملاحظة بأرقامها وأثرها المحتمل.

## روابط بين الأرقام
نقطتان إلى ثلاث تربط بين مؤشرين أو أكثر برابط سببي محتمل، مع التصريح بأنه احتمال.

## توصيات
ثلاث إلى خمس توصيات مرتّبة بالأولوية. كل توصية إجراء محدد قابل للتنفيذ هذا الشهر، مع الرقم الذي يبرره."""


def _prompt(d, year, month, all_kpis, findings, diesel):
    k = A.kpis(d, year, month)
    rc = A.reconcile(d, year, month)
    alerts = A.build_alerts(d, k, all_kpis, year, month)
    pp = A.period_profile(d)
    tr = A.truck_report(d)
    st, _ = S.compute(d)
    ss = S.summary(st)

    best = []
    for metric, (lbl, unit, up, dd) in A.COMPARE.items():
        b = A.best_month(all_kpis, metric)
        if b:
            mark = " (هذا الشهر هو الأفضل)" if b["label"] == k["label"] else ""
            best.append(f"{lbl}: الآن {k[metric]:,.{dd}f}{unit} — "
                        f"أفضل قراءة {b[metric]:,.{dd}f}{unit} في {b['label']}{mark}")

    per = "\n".join(
        f"{i}: {r['vol_pct']:.1f}% من الكمية، متوسط حمولة {r['avg']:.2f}"
        for i, r in pp.iterrows()) if not pp.empty else "—"

    low = tr[tr["below_target"]] if not tr.empty else tr
    swap = int(tr["other_moves"].sum()) if not tr.empty else 0

    diesel_txt = "لا تتوفر بيانات ديزل لهذا الشهر."
    if diesel is not None and len(diesel):
        e = A.truck_efficiency(d, diesel)
        e = e[(e["truck"] != "0") & (e["km"] > 0)]
        if not e.empty:
            worst = e.sort_values("l_per_100km", ascending=False).head(3)
            diesel_txt = (
                f"كلفة الديزل {e['cost'].sum():,.0f} دينار، "
                f"{e['cost'].sum()/max(e['total'].sum(),1):.2f} دينار لكل م3، "
                f"{e['liters'].sum():,.0f} لتر على {e['km'].sum():,.0f} كم.\n"
                "الأعلى استهلاكاً لكل 100كم: " + "، ".join(
                    f"{r['truck']} ({r['l_per_100km']:.0f} لتر/100كم)"
                    for _, r in worst.iterrows()))

    salary_txt = "—"
    if ss:
        pt = S.compute_pumps(d, A)
        pw = S.compute_pump_workers(d, A)
        total = ss["confirmed"] + pt["total_operator"].sum() + pw["total"].sum()
        salary_txt = (f"إجمالي رواتب عنبر النقل {total:,.2f} دينار، "
                      f"أي {total/max(k['total'],1):.3f} دينار لكل م3 منتَج.")

    return f"""شهر التقرير: {A.MONTH_AR[month]} {year}

# تسوية الإنتاج
إنتاج نظام الحركة {rc['gross']:,.1f} م3
ناقص إتلاف {rc['loss']:,.1f} م3، ناقص راجع غير مطالب به {rc['double']:,.1f} م3
صافي البيع {rc['net']:,.1f} م3
كميات محوّلة لعملاء آخرين (غير مخصومة) {rc['transferred']:,.1f} م3

# المؤشرات مقابل أفضل شهر مسجّل
{chr(10).join(best)}

# التشغيل
عدد الحركات {k['moves']:,} على {k['days']} يوم عمل، معدل {k['per_day']:,.0f} م3 يومياً
حركات المضخة {k['pumps']:,}
خلاطات نشطة {k['trucks']}، سائقون {k['drivers']}، عملاء {k['clients']}، سندات {k['bonds']}، مناطق {k['areas']}
خلاطات تحت حد {A.MIN_TRUCK_MONTHLY:,.0f} م3: {len(low)}
حركات بخلاطة غير سائقها الأساسي: {swap}

# فترات التحميل
{per}

# الديزل
{diesel_txt}

# الرواتب
{salary_txt}

# تنبيهات محسوبة آلياً
{chr(10).join('- ' + a['title'] + ': ' + a['detail'] for a in alerts) or 'لا يوجد'}

# تحوّلات مرصودة مقارنةً بالأشهر السابقة
{I.to_text(findings)}"""


def _md_to_html(md):
    """تحويل مبسّط للمخرجات المتوقّعة: عناوين ##، نقاط -، تشديد **"""
    import re
    out, in_ul = [], False

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        return t

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue
        if s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<h3 class="narr-h">{inline(s[3:])}</h3>')
        elif s.startswith(("- ", "* ")):
            if not in_ul:
                out.append('<ul class="narr-ul">')
                in_ul = True
            out.append(f"<li>{inline(s[2:])}</li>")
        elif _NUM_LI.match(s):
            if not in_ul:
                out.append('<ul class="narr-ul">')
                in_ul = True
            item = inline(_NUM_LI.sub("", s))
            out.append(f"<li>{item}</li>")
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{inline(s)}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def build(client, d, year, month, all_kpis, findings, diesel=None,
          model=MODEL_HEAVY):
    """يرجع HTML جاهز للإدراج، أو رسالة واضحة عند التعذّر"""
    try:
        prompt = _prompt(d, year, month, all_kpis, findings, diesel)
        r = client.messages.create(
            model=model, max_tokens=3000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}])
        md = "".join(b.text for b in r.content if b.type == "text").strip()
        if not md:
            raise ValueError("رد فارغ")
        return _md_to_html(md)
    except Exception as e:
        logger.exception("narrative failed")
        return ('<div class="pend">تعذّر توليد القراءة التنفيذية: '
                f'{html.escape(str(e))}. باقي أقسام التقرير غير متأثرة.</div>')
