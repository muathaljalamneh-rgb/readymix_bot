"""
محرك الاكتشاف — يقارن كل كيان بتاريخه الخاص ويرصد التحوّلات الشاذة.
الحسابات إحصائية بالكامل، لا يتدخل فيها نموذج لغوي.
"""

import numpy as np
import pandas as pd
import analytics as A
import entities as E

# عتبات الرصد
MIN_VOL = 100.0        # أقل كمية شهرية تجعل الكيان جديراً بالمتابعة (م3)
MIN_MOVES = 10         # أقل عدد حركات
PCT_BIG = 35.0         # تغيّر نسبي يُعد كبيراً
Z_STRONG = 2.0         # انحراف معياري يُعد شاذاً
RATE_WORSE = 1.5       # تدهور معدل الصب (دقيقة/م3) يُعد ملحوظاً


def _agg(d, key):
    prod = d[d["_qty"] > 0]
    if prod.empty:
        return pd.DataFrame()
    g = prod.groupby("_" + key).agg(
        total=("_qty", "sum"), moves=("_qty", "size"), avg=("_qty", "mean"))
    g["lt10_pct"] = prod.groupby("_" + key)["_qty"].apply(
        lambda s: (s < 10).mean() * 100)
    return g


def _hist_stats(series):
    """متوسط وانحراف تاريخي مع تفادي الانحراف الصفري"""
    vals = np.array([v for v in series if v is not None], dtype=float)
    if len(vals) == 0:
        return None, None
    mu = float(vals.mean())
    sd = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    return mu, sd


def detect(cur_d, cur_ym, history):
    """
    history: قائمة (dataframe مُجهَّز، (سنة، شهر)) للأشهر السابقة مرتّبة زمنياً.
    يرجع قائمة اكتشافات، كل واحد فيه العنوان والأرقام ودرجة الأهمية.
    """
    year, month = cur_ym
    out = []

    # توحيد الأسماء قبل أي مقارنة — الشيتات القديمة فيها قصّ وحذف حروف
    frames = [h for h, _ in history] + [cur_d]
    for key in ("client", "area", "driver"):
        mapping = E.build_canonical(
            [f["_" + key].dropna().unique().tolist() for f in frames])
        cur_d = E.apply_canonical(cur_d, mapping, keys=(key,))
        history = [(E.apply_canonical(h, mapping, keys=(key,)), ym)
                   for h, ym in history]

    def add(sev, cat, title, detail, numbers=None):
        out.append({"severity": sev, "category": cat, "title": title,
                    "detail": detail, "numbers": numbers or {}})

    if not history:
        return out

    prev_d, prev_ym = history[-1]
    prev_label = f"{A.MONTH_AR[prev_ym[1]]} {prev_ym[0]}"
    cur_label = f"{A.MONTH_AR[month]} {year}"

    # ───────── 1) العملاء: دخول، خروج، تحوّل ─────────
    cur_c = _agg(cur_d, "client")
    prev_c = _agg(prev_d, "client")
    hist_c = [_agg(h, "client") for h, _ in history]

    if not cur_c.empty and not prev_c.empty:
        gone = prev_c[(prev_c["total"] >= MIN_VOL) &
                      (~prev_c.index.isin(cur_c.index))]
        if len(gone):
            names = "، ".join(f"{i} ({r['total']:,.0f}م3)"
                              for i, r in gone.head(5).iterrows())
            add("high", "العملاء", f"{len(gone)} عميل توقّف تماماً",
                f"كانوا نشطين في {prev_label} واختفوا في {cur_label}. "
                f"مجموع ما كانوا يأخذونه {gone['total'].sum():,.0f}م3. {names}.",
                {"lost_volume": float(gone["total"].sum())})

        newc = cur_c[(cur_c["total"] >= MIN_VOL) &
                     (~cur_c.index.isin(prev_c.index))]
        if len(newc):
            names = "، ".join(f"{i} ({r['total']:,.0f}م3)"
                              for i, r in newc.head(5).iterrows())
            add("good", "العملاء", f"{len(newc)} عميل جديد",
                f"لم يظهروا في {prev_label}. مجموع كمياتهم "
                f"{newc['total'].sum():,.0f}م3. {names}.",
                {"new_volume": float(newc["total"].sum())})

        # تحوّل حاد لدى عميل مستمر مقارنةً بمتوسطه التاريخي
        shifts = []
        for name in cur_c.index:
            if cur_c.loc[name, "total"] < MIN_VOL:
                continue
            past = [h.loc[name, "total"] for h in hist_c if name in h.index]
            if len(past) < 2:
                continue
            mu, sd = _hist_stats(past)
            now = float(cur_c.loc[name, "total"])
            pct = (now - mu) / mu * 100 if mu else 0
            z = (now - mu) / sd if sd and sd > 0 else 0
            if abs(pct) >= PCT_BIG and abs(z) >= Z_STRONG:
                shifts.append((name, now, mu, pct, z))
        shifts.sort(key=lambda x: -abs(x[3]))
        for name, now, mu, pct, z in shifts[:6]:
            direction = "ارتفاع" if pct > 0 else "انخفاض"
            add("mid" if pct > 0 else "high", "العملاء",
                f"{direction} حاد لدى {name}",
                f"{now:,.0f}م3 في {cur_label} مقابل متوسط تاريخي "
                f"{mu:,.0f}م3 — {direction} {abs(pct):.0f}% "
                f"(انحراف {abs(z):.1f} ضعف الاعتيادي).",
                {"now": now, "mean": mu, "pct": pct})

    # ───────── 2) المناطق ─────────
    cur_a, prev_a = _agg(cur_d, "area"), _agg(prev_d, "area")
    if not cur_a.empty and not prev_a.empty:
        common = cur_a.index.intersection(prev_a.index)
        rows = []
        for name in common:
            now, before = cur_a.loc[name, "total"], prev_a.loc[name, "total"]
            if max(now, before) < MIN_VOL:
                continue
            pct = (now - before) / before * 100 if before else 0
            if abs(pct) >= PCT_BIG:
                rows.append((name, now, before, pct))
        rows.sort(key=lambda x: -abs(x[3]))
        if rows:
            up = [r for r in rows if r[3] > 0][:3]
            down = [r for r in rows if r[3] < 0][:3]
            txt = []
            if up:
                txt.append("ارتفعت: " + "، ".join(
                    f"{n} ({b:,.0f}←{a:,.0f}م3، +{p:.0f}%)" for n, a, b, p in up))
            if down:
                txt.append("انخفضت: " + "، ".join(
                    f"{n} ({b:,.0f}←{a:,.0f}م3، {p:.0f}%)" for n, a, b, p in down))
            add("mid", "المناطق", f"{len(rows)} منطقة تغيّرت بأكثر من {PCT_BIG:.0f}%",
                " | ".join(txt) + f" (مقارنة {prev_label} بـ {cur_label}).")

    # ───────── 3) الخلاطات ─────────
    cur_t = A.truck_report(cur_d)
    prev_t = A.truck_report(prev_d)
    if not cur_t.empty and not prev_t.empty:
        ct = cur_t.set_index("truck")
        pt = prev_t.set_index("truck")
        common = ct.index.intersection(pt.index)
        drops = []
        for name in common:
            now, before = ct.loc[name, "total"], pt.loc[name, "total"]
            if before < MIN_VOL:
                continue
            pct = (now - before) / before * 100
            if pct <= -PCT_BIG:
                drops.append((name, now, before, pct,
                              int(ct.loc[name, "idle_days"])))
        drops.sort(key=lambda x: x[3])
        if drops:
            names = "، ".join(
                f"{n} ({b:,.0f}←{a:,.0f}م3، تعطّل {idle} يوم)"
                for n, a, b, p, idle in drops[:5])
            add("high", "الخلاطات", f"{len(drops)} خلاطة تراجع إنتاجها بشدة",
                f"انخفاض يتجاوز {PCT_BIG:.0f}% عن {prev_label}. {names}.")

        stopped = pt[(pt["total"] >= MIN_VOL) & (~pt.index.isin(ct.index))]
        if len(stopped):
            add("high", "الخلاطات", f"{len(stopped)} خلاطة توقّفت كلياً",
                f"كانت تعمل في {prev_label} ولم تسجّل أي حركة في {cur_label}: "
                + "، ".join(f"{i} ({r['total']:,.0f}م3)"
                            for i, r in stopped.iterrows()) + ".")

    # ───────── 4) السائقون ─────────
    cur_dr = A.driver_trips(cur_d)
    prev_dr = A.driver_trips(prev_d)
    if not cur_dr.empty and not prev_dr.empty:
        gone = prev_dr[(prev_dr["trips"] >= 20) &
                       (~prev_dr.index.isin(cur_dr.index))]
        if len(gone):
            add("mid", "السائقون", f"{len(gone)} سائق لم يظهر هذا الشهر",
                "كانوا يعملون في " + prev_label + ": " +
                "، ".join(f"{i} ({int(r['trips'])} نقلة)"
                          for i, r in gone.head(6).iterrows()) + ".")
        newd = cur_dr[(cur_dr["trips"] >= 20) &
                      (~cur_dr.index.isin(prev_dr.index))]
        if len(newd):
            add("mid", "السائقون", f"{len(newd)} سائق جديد",
                "، ".join(f"{i} ({int(r['trips'])} نقلة)"
                          for i, r in newd.head(6).iterrows()) + ".")

    # ───────── 5) معدل الصب ─────────
    cur_r = A.pour_rate_by(cur_d, "client")
    prev_r = A.pour_rate_by(prev_d, "client")
    if not cur_r.empty and not prev_r.empty:
        c = cur_r.set_index("name")
        p = prev_r.set_index("name")
        worse = []
        for name in c.index.intersection(p.index):
            if c.loc[name, "total"] < MIN_VOL:
                continue
            diff = c.loc[name, "rate"] - p.loc[name, "rate"]
            if diff >= RATE_WORSE:
                worse.append((name, c.loc[name, "rate"], p.loc[name, "rate"], diff))
        worse.sort(key=lambda x: -x[3])
        if worse:
            add("mid", "الأوقات", f"{len(worse)} عميل تباطأ معدل الصب لديه",
                "، ".join(f"{n} ({b:.1f}←{a:.1f} د/م3)"
                          for n, a, b, d_ in worse[:5]) +
                f" مقارنةً بـ {prev_label}.")

    # ───────── 6) توزيع الفترات ─────────
    cp, pp = A.period_profile(cur_d), A.period_profile(prev_d)
    if not cp.empty and not pp.empty:
        for per in cp.index:
            if per not in pp.index:
                continue
            diff = cp.loc[per, "vol_pct"] - pp.loc[per, "vol_pct"]
            if abs(diff) >= 6:
                d_word = "ارتفعت" if diff > 0 else "انخفضت"
                add("mid", "الأوقات", f"حصة {per} {d_word}",
                    f"من {pp.loc[per,'vol_pct']:.1f}% في {prev_label} إلى "
                    f"{cp.loc[per,'vol_pct']:.1f}% في {cur_label} "
                    f"({diff:+.1f} نقطة).")

    # ───────── 7) الرتب ─────────
    cur_g, prev_g = _agg(cur_d, "grade"), _agg(prev_d, "grade")
    if not cur_g.empty and not prev_g.empty:
        cs = cur_g["total"] / cur_g["total"].sum() * 100
        ps = prev_g["total"] / prev_g["total"].sum() * 100
        shifts = []
        for g in cs.index.intersection(ps.index):
            diff = cs[g] - ps[g]
            if abs(diff) >= 5:
                shifts.append((g, ps[g], cs[g], diff))
        if shifts:
            shifts.sort(key=lambda x: -abs(x[3]))
            add("mid", "الرتب", "تحوّل في مزيج الرتب",
                "، ".join(f"{g}: {b:.1f}%←{a:.1f}%" for g, b, a, d_ in shifts[:4])
                + f" (حصة من إجمالي الكمية، مقارنةً بـ {prev_label}).")

    # ───────── 8) حجم الحمولة ─────────
    ck = A.kpis(cur_d, year, month)
    pk = A.kpis(prev_d, *prev_ym)
    if pk["moves"]:
        d_lt10 = ck["lt10_pct"] - pk["lt10_pct"]
        if abs(d_lt10) >= 3:
            w = "ارتفعت" if d_lt10 > 0 else "تحسّنت"
            add("high" if d_lt10 > 0 else "good", "الحمولات",
                f"نسبة الحمولات الناقصة {w}",
                f"من {pk['lt10_pct']:.1f}% إلى {ck['lt10_pct']:.1f}% "
                f"({d_lt10:+.1f} نقطة) — أي {ck['lt10']} حركة تحت 10م3.")

    sev = {"high": 0, "mid": 1, "good": 2}
    return sorted(out, key=lambda x: sev[x["severity"]])


def to_text(findings):
    """صيغة نصية مضغوطة تُمرَّر للنموذج"""
    if not findings:
        return "لا توجد تحوّلات شاذة مرصودة."
    return "\n".join(
        f"[{f['severity']}] ({f['category']}) {f['title']}: {f['detail']}"
        for f in findings)
