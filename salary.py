"""
احتساب رواتب سائقي عنبر النقل من بيانات الحركة.
"""

import pandas as pd
import numpy as np

# ── القيم القابلة للتعديل ──
TIER1_TRIPS = 50        # أول 50 نقلة
TIER1_RATE = 2.0        # دينار للنقلة
TIER2_RATE = 3.0        # دينار لما بعد الـ50
MAZAREEB_BONUS = 1.5    # زيادة على قيمة نقلة المزاريب
SARWA = 5.0             # دوام قبل الساعة 7 صباحاً
SAHRA = 5.0             # استمرار الدوام بعد الساعة 6 مساءً
DINNER = 2.75           # بدل عشاء بعد الساعة 10 مساءً
TRANSFER = 5.0          # بدل تحويل بين فرعين في نفس اليوم
DELAY_TRIPS = 1         # بدل التأخير أكثر من 3 ساعات (نقلة أو نقلتين)
DELAY_HOURS = 3.0

EARLY_HOUR = 7          # قبلها = سروة
LATE_HOUR = 18          # بعدها = سهرة
DINNER_HOUR = 22        # بعدها = بدل عشاء

MAZAREEB_LABEL = "مزاريب"


def trip_value(n):
    """قيمة n نقلة بالشرائح"""
    if n <= TIER1_TRIPS:
        return n * TIER1_RATE
    return TIER1_TRIPS * TIER1_RATE + (n - TIER1_TRIPS) * TIER2_RATE


def marginal_rate(n):
    return TIER1_RATE if n <= TIER1_TRIPS else TIER2_RATE


def compute(d):
    """
    يرجع (جدول الرواتب، جدول مرشّحات التأخير).
    النقلات = الحركات ذات الكمية > 0 فقط (صفوف المضخة مستبعدة).
    """
    prod = d[(d["_qty"] > 0)].copy()
    if prod.empty:
        return pd.DataFrame(), pd.DataFrame()

    rows, delay_rows = [], []

    for drv, sub in prod.groupby("_driver"):
        sub = sub.sort_values("_ts")
        trips = int(len(sub))
        mazareeb = int((sub["_pour_type"] == MAZAREEB_LABEL).sum())

        base = trip_value(trips)
        maz_pay = mazareeb * MAZAREEB_BONUS

        # البدلات اليومية
        withts = sub[sub["_ts"].notna()]
        sarwa_days = sahra_days = dinner_days = transfer_days = 0
        if not withts.empty:
            day = withts.groupby("_date").agg(
                first=("_ts", "min"), last=("_ts", "max"),
                plants=("_plant", "nunique"))
            sarwa_days = int((day["first"].dt.hour < EARLY_HOUR).sum())
            sahra_days = int((day["last"].dt.hour >= LATE_HOUR).sum())
            dinner_days = int((day["last"].dt.hour >= DINNER_HOUR).sum())
            transfer_days = int((day["plants"] > 1).sum())

            # فجوات تزيد على 3 ساعات — مرشّحة للمراجعة، السبب غير مسجّل بالشيت
            for date, dd in withts.groupby("_date"):
                dd = dd.sort_values("_ts")
                idx = list(dd.index)
                gaps = dd["_ts"].diff().dt.total_seconds().div(3600)
                for pos, i in enumerate(idx):
                    g = gaps.get(i)
                    if pos == 0 or pd.isna(g) or g <= DELAY_HOURS:
                        continue
                    prev = idx[pos - 1]
                    same_client = dd.loc[i, "_client"] == dd.loc[prev, "_client"]
                    delay_rows.append({
                        "driver": drv, "date": str(date),
                        "gap_h": round(float(g), 1),
                        "from": dd.loc[prev, "_ts"].strftime("%H:%M"),
                        "to": dd.loc[i, "_ts"].strftime("%H:%M"),
                        "client": dd.loc[i, "_client"],
                        "strong": bool(same_client),
                    })

        rows.append({
            "driver": drv,
            "trips": trips,
            "tier1": min(trips, TIER1_TRIPS),
            "tier2": max(0, trips - TIER1_TRIPS),
            "base": base,
            "mazareeb": mazareeb,
            "maz_pay": maz_pay,
            "sarwa_days": sarwa_days, "sarwa_pay": sarwa_days * SARWA,
            "sahra_days": sahra_days, "sahra_pay": sahra_days * SAHRA,
            "dinner_days": dinner_days, "dinner_pay": dinner_days * DINNER,
            "transfer_days": transfer_days, "transfer_pay": transfer_days * TRANSFER,
            "days": int(sub["_date"].nunique()),
            "volume": float(sub["_qty"].sum()),
        })

    t = pd.DataFrame(rows)
    t["confirmed"] = (t["base"] + t["maz_pay"] + t["sarwa_pay"] +
                      t["sahra_pay"] + t["dinner_pay"] + t["transfer_pay"])

    dl = pd.DataFrame(delay_rows)
    if not dl.empty:
        strong = dl[dl["strong"]].groupby("driver").size().rename("delay_strong")
        allc = dl.groupby("driver").size().rename("delay_all")
        t = t.merge(strong, left_on="driver", right_index=True, how="left")
        t = t.merge(allc, left_on="driver", right_index=True, how="left")
        t[["delay_strong", "delay_all"]] = t[["delay_strong", "delay_all"]].fillna(0).astype(int)
    else:
        t["delay_strong"] = t["delay_all"] = 0

    # تكلفة المرشّحات القوية لو اعتُمدت — لا تُضاف للراتب تلقائياً
    t["delay_est"] = t.apply(
        lambda r: r["delay_strong"] * DELAY_TRIPS * marginal_rate(r["trips"]), axis=1)
    t["per_trip"] = t["confirmed"] / t["trips"].replace(0, np.nan)
    t["per_m3"] = t["confirmed"] / t["volume"].replace(0, np.nan)

    return t.sort_values("confirmed", ascending=False), dl


def summary(t):
    if t.empty:
        return None
    return {
        "drivers": int(len(t)),
        "trips": int(t["trips"].sum()),
        "confirmed": float(t["confirmed"].sum()),
        "delay_est": float(t["delay_est"].sum()),
        "delay_strong": int(t["delay_strong"].sum()),
        "delay_all": int(t["delay_all"].sum()),
        "base": float(t["base"].sum()),
        "maz_pay": float(t["maz_pay"].sum()),
        "sarwa_pay": float(t["sarwa_pay"].sum()),
        "sahra_pay": float(t["sahra_pay"].sum()),
        "dinner_pay": float(t["dinner_pay"].sum()),
        "transfer_pay": float(t["transfer_pay"].sum()),
        "avg": float(t["confirmed"].mean()),
        "max_name": t.iloc[0]["driver"],
        "max_val": float(t.iloc[0]["confirmed"]),
        "min_name": t.iloc[-1]["driver"],
        "min_val": float(t.iloc[-1]["confirmed"]),
        "per_m3": float(t["confirmed"].sum() / max(t["volume"].sum(), 1)),
        "over50": int((t["trips"] > TIER1_TRIPS).sum()),
        
    }


# ── رواتب عنبر المضخات ──
OPERATOR_RATE = 0.20    # مشغل المضخة: 20 قرش للمتر
WORKER_RATE = 0.09      # عامل المضخة: 9 قروش للمتر
HOLIDAY_PAY = 10.0      # بدل دوام يوم عطلة
HOLIDAY_DOW = 4         # الجمعة


def compute_pumps(d, analytics):
    """رواتب مشغّلي المضخات. الاسم المسجّل في الشيت يُعامل كمشغّل."""
    by_driver, _ = analytics.pump_volumes(d)
    if by_driver.empty:
        return pd.DataFrame()

    p = d[(d["_qty"] <= 0) & (d["_vehicle_type"] == "مضخة")].copy()
    bond_vol = d[d["_qty"] > 0].groupby("_bond")["_qty"].sum()
    share = p.groupby("_bond")["_qty"].size()
    p["_pumped"] = p["_bond"].map(bond_vol).fillna(0) / p["_bond"].map(share)

    rows = []
    for drv, sub in p.groupby("_driver"):
        withts = sub[sub["_ts"].notna()]
        sarwa = sahra = dinner = holiday = 0
        if not withts.empty:
            day = withts.groupby("_date").agg(
                first=("_ts", "min"), last=("_ts", "max"), dow=("_dow", "first"))
            sarwa = int((day["first"].dt.hour < EARLY_HOUR).sum())
            sahra = int((day["last"].dt.hour >= LATE_HOUR).sum())
            dinner = int((day["last"].dt.hour >= DINNER_HOUR).sum())
            holiday = int((day["dow"] == HOLIDAY_DOW).sum())

        pumped = float(sub["_pumped"].sum())
        rows.append({
            "driver": drv,
            "jobs": int(len(sub)),
            "pumped": pumped,
            "operator_pay": pumped * OPERATOR_RATE,
            "worker_pay": pumped * WORKER_RATE,
            "days": int(sub["_date"].nunique()),
            "pumps": int(sub["_truck"].nunique()),
            "sarwa_days": sarwa, "sarwa_pay": sarwa * SARWA,
            "sahra_days": sahra, "sahra_pay": sahra * SAHRA,
            "dinner_days": dinner, "dinner_pay": dinner * DINNER,
            "holiday_days": holiday, "holiday_pay": holiday * HOLIDAY_PAY,
            "no_time": int(sub["_ts"].isna().sum()),
        })

    t = pd.DataFrame(rows)
    t["allowances"] = (t["sarwa_pay"] + t["sahra_pay"] +
                       t["dinner_pay"] + t["holiday_pay"])
    t["total_operator"] = t["operator_pay"] + t["allowances"]
    t["per_m3"] = t["total_operator"] / t["pumped"].replace(0, np.nan)
    return t.sort_values("total_operator", ascending=False)


def compute_pump_workers(d, analytics):
    """
    عمّال المضخات — لا تُسجَّل أسماؤهم، فيُنسب العامل إلى رقم المضخة.
    الأجر 9 قروش للمتر المضخوخ بتلك المضخة.
    البدلات اليومية تُحتسب من أوقات مهمات المضخة نفسها.
    """
    _, by_pump = analytics.pump_volumes(d)
    if by_pump.empty:
        return pd.DataFrame()

    p = d[(d["_qty"] <= 0) & (d["_vehicle_type"] == "مضخة")].copy()
    bond_vol = d[d["_qty"] > 0].groupby("_bond")["_qty"].sum()
    share = p.groupby("_bond")["_qty"].size()
    p["_pumped"] = p["_bond"].map(bond_vol).fillna(0) / p["_bond"].map(share)

    rows = []
    for truck, sub in p.groupby("_truck"):
        withts = sub[sub["_ts"].notna()]
        sarwa = sahra = dinner = holiday = 0
        if not withts.empty:
            day = withts.groupby("_date").agg(
                first=("_ts", "min"), last=("_ts", "max"), dow=("_dow", "first"))
            sarwa = int((day["first"].dt.hour < EARLY_HOUR).sum())
            sahra = int((day["last"].dt.hour >= LATE_HOUR).sum())
            dinner = int((day["last"].dt.hour >= DINNER_HOUR).sum())
            holiday = int((day["dow"] == HOLIDAY_DOW).sum())
        pumped = float(sub["_pumped"].sum())
        rows.append({
            "worker": f"عامل مضخة {truck}",
            "pump": truck,
            "jobs": int(len(sub)),
            "pumped": pumped,
            "base_pay": pumped * WORKER_RATE,
            "days": int(sub["_date"].nunique()),
            "sarwa_days": sarwa, "sarwa_pay": sarwa * SARWA,
            "sahra_days": sahra, "sahra_pay": sahra * SAHRA,
            "dinner_days": dinner, "dinner_pay": dinner * DINNER,
            "holiday_days": holiday, "holiday_pay": holiday * HOLIDAY_PAY,
        })
    t = pd.DataFrame(rows)
    t["allowances"] = (t["sarwa_pay"] + t["sahra_pay"] +
                       t["dinner_pay"] + t["holiday_pay"])
    t["total"] = t["base_pay"] + t["allowances"]
    return t.sort_values("total", ascending=False)


def distortions(d, top=6):
    """
    تحليل تشوّهات الحوافز: لماذا يتقاضى سائق نقل كمية أقل أكثر ممن نقل أكثر.

    الحوافز مربوطة بعدد النقلات وبساعات الدوام، لا بالكمية المنقولة. فسائق
    يحمل حمولات أصغر يحتاج نقلات أكثر لنقل الكمية نفسها فتزيد حوافزه، وسائق
    يداوم مبكراً أو يسهر يجمع بدلات لا علاقة لها بما نقل.
    """
    t, _ = compute(d)
    if t.empty:
        return None
    t = t.copy()
    t = t[t["trips"] >= 5]
    if len(t) < 4:
        return None

    t["avg_load"] = t["volume"] / t["trips"]
    t["jd_m3"] = t["confirmed"] / t["volume"].replace(0, np.nan)
    t["jd_trip"] = t["confirmed"] / t["trips"]
    t["allow"] = t["confirmed"] - t["base"]
    t["allow_pct"] = t["allow"] / t["confirmed"] * 100
    t["rank_vol"] = t["volume"].rank(ascending=False)
    t["rank_pay"] = t["confirmed"].rank(ascending=False)

    # أزواج انعكاس: نقل أكثر وتقاضى أقل
    pairs = []
    srt = t.sort_values("volume", ascending=False)
    for i, a in srt.iterrows():
        for j, b in srt.iterrows():
            if a["volume"] <= b["volume"] or a["confirmed"] >= b["confirmed"]:
                continue
            gap_v = a["volume"] - b["volume"]
            gap_p = b["confirmed"] - a["confirmed"]
            if gap_v >= 50 and gap_p >= 15:
                pairs.append({
                    "more_vol": a["driver"], "more_v": a["volume"],
                    "more_t": int(a["trips"]), "more_load": a["avg_load"],
                    "more_pay": a["confirmed"], "more_allow": a["allow"],
                    "less_vol": b["driver"], "less_v": b["volume"],
                    "less_t": int(b["trips"]), "less_load": b["avg_load"],
                    "less_pay": b["confirmed"], "less_allow": b["allow"],
                    "gap_v": gap_v, "gap_p": gap_p,
                    "score": gap_v * gap_p,
                })
    pairs = sorted(pairs, key=lambda x: -x["score"])[:top]

    corr = {}
    for col in ("avg_load", "allow_pct", "trips"):
        try:
            corr[col] = float(np.corrcoef(t["jd_m3"], t[col])[0, 1])
        except Exception:
            corr[col] = 0.0

    return {
        "table": t.sort_values("jd_m3", ascending=False),
        "pairs": pairs,
        "corr": corr,
        "spread_min": float(t["jd_m3"].min()),
        "spread_max": float(t["jd_m3"].max()),
        "spread_pct": float((t["jd_m3"].max() / max(t["jd_m3"].min(), 1e-9) - 1) * 100),
        "median": float(t["jd_m3"].median()),
        "allow_total": float(t["allow"].sum()),
        "allow_share": float(t["allow"].sum() / t["confirmed"].sum() * 100),
        "n": int(len(t)),
    }
