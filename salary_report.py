"""
تقرير رواتب السائقين — ملف منفصل.
"""

import datetime as dt
import analytics as A
import salary as S
from report_html import CSS, esc, table


def build(d, year, month, tab):
    t, dl = S.compute(d)
    s = S.summary(t)
    pt = S.compute_pumps(d, A)          # مشغّلو المضخات
    pw = S.compute_pump_workers(d, A)   # عمّال المضخات
    mname = f"{A.MONTH_AR[month]} {year}"
    if s is None:
        return f"<!DOCTYPE html><html dir='rtl'><body>لا توجد بيانات لـ {mname}</body></html>"

    def kpi(lbl, val, unit=""):
        return (f'<div class="kpi"><div class="lbl">{esc(lbl)}</div>'
                f'<div class="val num">{val}<span class="unit"> {esc(unit)}</span>'
                f'</div></div>')

    op_total = float(pt["total_operator"].sum()) if len(pt) else 0.0
    wk_total = float(pw["total"].sum()) if len(pw) else 0.0
    grand = s["confirmed"] + op_total + wk_total
    prod_m3 = float(d[d["_qty"] > 0]["_qty"].sum())

    head = "".join([
        kpi("إجمالي عنبر النقل", f"{grand:,.2f}", "دينار"),
        kpi("سائقو الخلاطات", f"{s['confirmed']:,.2f}", "دينار"),
        kpi("مشغّلو المضخات", f"{op_total:,.2f}", "دينار"),
        kpi("عمّال المضخات", f"{wk_total:,.2f}", "دينار"),
        kpi("كلفة الرواتب لكل م3", f"{grand/max(prod_m3,1):.3f}", "دينار"),
        kpi("عدد المستحقين",
            f"{s['drivers'] + (len(pt) if len(pt) else 0) + (len(pw) if len(pw) else 0)}",
            "شخص"),
    ])

    # تفكيك المستحق
    parts = [
        ("قيمة النقلات (شرائح 2 و3 دينار)", s["base"]),
        ("حوافز مشغّلي المضخات (20 قرش/م3)", op_total - (
            float(pt["allowances"].sum()) if len(pt) else 0)),
        ("أجور عمّال المضخات (9 قروش/م3)", wk_total - (
            float(pw["allowances"].sum()) if len(pw) else 0)),
        ("سروة — دوام قبل 7 صباحاً", s["sarwa_pay"] + (
            float(pt["sarwa_pay"].sum()) if len(pt) else 0) + (
            float(pw["sarwa_pay"].sum()) if len(pw) else 0)),
        ("سهرة — دوام بعد 6 مساءً", s["sahra_pay"] + (
            float(pt["sahra_pay"].sum()) if len(pt) else 0) + (
            float(pw["sahra_pay"].sum()) if len(pw) else 0)),
        ("بدل دوام يوم الجمعة", (
            float(pt["holiday_pay"].sum()) if len(pt) else 0) + (
            float(pw["holiday_pay"].sum()) if len(pw) else 0)),
        ("بدل تحويل بين الفروع", s["transfer_pay"]),
        ("زيادة نقلات المزاريب", s["maz_pay"]),
        ("بدل عشاء — بعد 10 مساءً", s["dinner_pay"] + (
            float(pt["dinner_pay"].sum()) if len(pt) else 0) + (
            float(pw["dinner_pay"].sum()) if len(pw) else 0)),
    ]
    parts = [(n, v) for n, v in parts if v > 0]
    mx = max(v for _, v in parts)
    brk = "".join(
        f'<div class="slot"><div class="p">{esc(n)}</div>'
        f'<div class="v num">{v:,.2f}</div>'
        f'<div class="m">{v/grand*100:.1f}% من الإجمالي</div>'
        f'<div class="bar" style="width:{v/mx*100:.0f}%"></div></div>'
        for n, v in parts)

    F = {"trips": lambda v: f"{int(v)}", "tier1": lambda v: f"{int(v)}",
         "tier2": lambda v: f"{int(v)}", "mazareeb": lambda v: f"{int(v)}",
         "sarwa_days": lambda v: f"{int(v)}", "sahra_days": lambda v: f"{int(v)}",
         "dinner_days": lambda v: f"{int(v)}", "transfer_days": lambda v: f"{int(v)}",
         "days": lambda v: f"{int(v)}", "delay_strong": lambda v: f"{int(v)}",
         "base": lambda v: f"{v:,.2f}", "maz_pay": lambda v: f"{v:,.2f}",
         "sarwa_pay": lambda v: f"{v:,.2f}", "sahra_pay": lambda v: f"{v:,.2f}",
         "dinner_pay": lambda v: f"{v:,.2f}", "transfer_pay": lambda v: f"{v:,.2f}",
         "confirmed": lambda v: f"{v:,.2f}", "per_trip": lambda v: f"{v:.2f}",
         "volume": lambda v: f"{v:,.0f}", "delay_est": lambda v: f"{v:,.2f}"}

    main_tbl = table("كشف الرواتب — تفصيل كل سائق", t,
        [("driver", "السائق"), ("trips", "نقلات"), ("base", "قيمة النقلات"),
         ("mazareeb", "مزاريب"), ("maz_pay", "زيادة المزاريب"),
         ("sarwa_days", "أيام سروة"), ("sarwa_pay", "قيمتها"),
         ("sahra_days", "أيام سهرة"), ("sahra_pay", "قيمتها"),
         ("dinner_days", "بدل عشاء"), ("dinner_pay", "قيمته"),
         ("transfer_days", "تحويل فرع"), ("transfer_pay", "قيمته"),
         ("confirmed", "المستحق")], F)

    simple_tbl = table("الخلاصة — للاعتماد", t,
        [("driver", "السائق"), ("trips", "نقلات"), ("days", "أيام"),
         ("volume", "م3"), ("per_trip", "دينار/نقلة"), ("confirmed", "المستحق")], F)

    tier_tbl = table("توزيع النقلات على الشرائح", t,
        [("driver", "السائق"), ("tier1", "أول 50 (×2)"),
         ("tier2", "ما بعدها (×3)"), ("trips", "المجموع"),
         ("base", "قيمة النقلات")], F)

    if not dl.empty:
        strong = dl[dl["strong"]].sort_values("gap_h", ascending=False)
        rev = table(f"مرشّحات بدل التأخير — {len(strong)} حالة للمراجعة", strong.head(60),
            [("driver", "السائق"), ("date", "التاريخ"), ("from", "من"),
             ("to", "إلى"), ("gap_h", "ساعات"), ("client", "العميل")],
            {"gap_h": lambda v: f"{v:.1f}"})
        per_drv = table("تكلفة المرشّحات لو اعتُمدت", t[t["delay_strong"] > 0][
            ["driver", "delay_strong", "delay_est"]],
            [("driver", "السائق"), ("delay_strong", "حالات"),
             ("delay_est", "التكلفة")], F)
    else:
        rev = per_drv = ""

    FP = {"jobs": lambda v: f"{int(v)}", "pumped": lambda v: f"{v:,.1f}",
          "operator_pay": lambda v: f"{v:,.2f}", "base_pay": lambda v: f"{v:,.2f}",
          "days": lambda v: f"{int(v)}", "pumps": lambda v: f"{int(v)}",
          "sarwa_days": lambda v: f"{int(v)}", "sahra_days": lambda v: f"{int(v)}",
          "dinner_days": lambda v: f"{int(v)}", "holiday_days": lambda v: f"{int(v)}",
          "allowances": lambda v: f"{v:,.2f}", "total_operator": lambda v: f"{v:,.2f}",
          "total": lambda v: f"{v:,.2f}"}

    pump_op_tbl = table("مشغّلو المضخات — التفصيل", pt,
        [("driver", "المشغّل"), ("jobs", "مهمات"), ("pumped", "م3 مضخوخ"),
         ("operator_pay", "الحوافز"), ("days", "أيام"), ("pumps", "مضخات"),
         ("sarwa_days", "سروة"), ("sahra_days", "سهرة"),
         ("dinner_days", "عشاء"), ("holiday_days", "عطلة"),
         ("allowances", "البدلات"), ("total_operator", "المستحق")], FP,
        bad=lambda r: r["pumps"] > 1)

    pump_wk_tbl = table("عمّال المضخات — التفصيل", pw,
        [("worker", "العامل"), ("jobs", "مهمات"), ("pumped", "م3 مضخوخ"),
         ("base_pay", "الأجر"), ("days", "أيام"),
         ("sarwa_days", "سروة"), ("sahra_days", "سهرة"),
         ("dinner_days", "عشاء"), ("holiday_days", "عطلة"),
         ("allowances", "البدلات"), ("total", "المستحق")], FP)

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>رواتب السائقين — {esc(mname)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600&family=Noto+Kufi+Arabic:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">

<div class="masthead"><div class="stamp">كشف رواتب</div>
<h1>رواتب عنبر النقل — {esc(mname)}</h1>
<div class="sub">{s["drivers"]} سائق خلاطة · {len(pt)} مشغّل مضخة · {len(pw)} عامل مضخة · التبويب {esc(tab)}<br>
صدر في {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}</div></div>

<div class="sec"><h2>الإجمالي</h2><div class="kpis">{head}</div></div>

<div class="sec"><h2>تفكيك المستحق</h2>
<div class="band">{brk}</div></div>

<div class="sec"><h2>سائقو الخلاطات — الخلاصة للاعتماد</h2>{simple_tbl}</div>

<div class="sec"><h2>الشرائح</h2>{tier_tbl}
<div class="note">أول {S.TIER1_TRIPS} نقلة بـ {S.TIER1_RATE:g} دينار، وما بعدها
بـ {S.TIER2_RATE:g} دينار. {s['over50']} سائقاً تجاوزوا الشريحة الأولى.</div></div>

<div class="sec"><h2>التفصيل الكامل</h2>{main_tbl}</div>

<div class="sec"><h2>مشغّلو المضخات</h2>{pump_op_tbl}
<div class="note">الأجر {S.OPERATOR_RATE:g} دينار للمتر المضخوخ. الكمية المضخوخة =
مجموع كميات الخلاطات في السند الذي خدمته المضخة. الاسم المسجّل في الشيت يُعامل
كمشغّل للمضخة.</div></div>

<div class="sec"><h2>عمّال المضخات</h2>{pump_wk_tbl}
<div class="note">الأجر {S.WORKER_RATE:g} دينار للمتر. أسماء العمّال غير مسجّلة في
الشيت، فيُنسب العامل إلى رقم مضخته، وتُحتسب بدلاته من أوقات عمل تلك المضخة.</div></div>

<div class="sec"><h2>بدل التأخير — يحتاج قراراً منك</h2>
<div class="alert mid"><div class="t">لم يُضف إلى الرواتب</div>
<div class="d">القاعدة تشترط أن يكون التأخير بسبب أزمة أو العميل نفسه، وهذا السبب
غير مسجّل في الشيت. رُصدت {s['delay_all']} فجوة تتجاوز {S.DELAY_HOURS:g} ساعات بين
حركتين، لكن معظمها على الأرجح فراغ في الطلب لا انتظار.
المرشّحات القوية هي {s['delay_strong']} حالة كان العميل نفسه قبل الفجوة وبعدها —
أي أن السائق كان ينتظر ذلك العميل. تكلفتها {s['delay_est']:,.2f} دينار لو اعتُمدت
بنقلة واحدة لكل حالة.</div></div>
{per_drv}{rev}</div>

<footer>القيم المعتمدة: النقلة {S.TIER1_RATE:g}/{S.TIER2_RATE:g} دينار ·
المزاريب +{S.MAZAREEB_BONUS:g} · السروة {S.SARWA:g} · السهرة {S.SAHRA:g} ·
العشاء {S.DINNER:g} · التحويل {S.TRANSFER:g}.<br>
النقلات محسوبة من حركات الخلاطات فقط (الكمية أكبر من صفر). لم يُرصد هذا الشهر أي
تحويل لسائق خلاطة إلى مضخة، فبندا التحويل إلى سائق أو مشغّل مضخة غير مطبّقين.
المصدر: {esc(tab)}.</footer>
</div></body></html>"""
