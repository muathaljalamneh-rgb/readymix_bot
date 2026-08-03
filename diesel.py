"""
تحليل الديزل — الوحدة الأساسية: لتر لكل م3.

الفكرة: لتر/م3 هو ما يهم الإدارة. لكنه يتأثر بعاملين خارجين عن الخلاطة:
بُعد المواقع التي تخدمها، وحجم الحمولات التي تحملها. لذلك يُفكَّك إلى
جزء مفسَّر بهذين العاملين وجزء متبقٍّ يخص الخلاطة نفسها.
"""

import numpy as np
import pandas as pd
import analytics as A

MIN_MONTHS_MODEL = 8      # أقل عدد مشاهدات لتقدير النموذج
STABLE_STD = 0.15         # تذبذب أقل من هذا يجعل الفجوة إشارة لا ضوضاء
GAP_ALERT = 0.15          # فجوة تستحق الفحص (لتر/م3)


def truck_month_table(months):
    """
    months: قائمة (d مُجهَّز، diesel، (سنة، شهر)).
    يرجع صفاً لكل خلاطة في كل شهر مع لتر/م3 وكم/م3 ومتوسط الحمولة.
    """
    rows = []
    for d, dz, (y, m) in months:
        if dz is None or len(dz) == 0:
            continue
        e = A.truck_efficiency(d, dz)
        e = e[(e["truck"] != "0") & (e["km"] > 0) & (e["total"] > 0)]
        if e.empty:
            continue
        prod = d[d["_qty"] > 0]
        loads = prod.groupby("_truck")["_qty"].agg(
            avg_load="mean", small_pct=lambda s: (s < 10).mean() * 100)
        e = e.merge(loads, left_on="truck", right_index=True, how="left")
        e = e.dropna(subset=["avg_load"])
        e = e.assign(year=y, month=m,
                     label=f"{A.MONTH_AR[m]} {y}",
                     l_per_m3=e["liters"] / e["total"],
                     km_per_m3=e["km"] / e["total"])
        rows.append(e)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def fit(tm):
    """
    لتر/م3 = a×(كم/م3) + b×(1/متوسط الحمولة) + c
    المعامل b يترجم أثر صغر الحمولة إلى لترات مباشرة.
    """
    if tm.empty or len(tm) < MIN_MONTHS_MODEL:
        return None
    X = np.column_stack([tm["km_per_m3"].values,
                         1.0 / tm["avg_load"].values,
                         np.ones(len(tm))])
    y = tm["l_per_m3"].values
    coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    # النموذج بدون عامل الحمولة، لقياس مساهمته
    X2 = np.column_stack([tm["km_per_m3"].values, np.ones(len(tm))])
    c2, _, _, _ = np.linalg.lstsq(X2, y, rcond=None)
    r2_dist = 1 - float(((y - X2 @ c2) ** 2).sum()) / ss_tot if ss_tot else 0
    return {
        "a_km": float(coef[0]), "b_load": float(coef[1]), "c": float(coef[2]),
        "r2": 1 - ss_res / ss_tot if ss_tot else 0.0,
        "r2_distance_only": r2_dist,
        "mape": float(np.mean(np.abs((y - pred) / y)) * 100),
        "n": int(len(tm)), "pred": pred,
    }


def truck_summary(tm, model):
    """ملخص لكل خلاطة عبر الشهور، مع اختبار ثبات الفجوة"""
    if tm.empty:
        return pd.DataFrame()
    t = tm.copy()
    if model:
        t["expected"] = model["pred"]
        t["gap"] = t["l_per_m3"] - t["expected"]
    else:
        t["expected"] = np.nan
        t["gap"] = np.nan

    g = t.groupby("truck").agg(
        months=("month", "nunique"),
        m3=("total", "sum"), liters=("liters", "sum"),
        cost=("cost", "sum"), km=("km", "sum"),
        avg_load=("avg_load", "mean"), small_pct=("small_pct", "mean"),
        gap_mean=("gap", "mean"), gap_std=("gap", "std"),
        expected=("expected", "mean"))
    g["l_per_m3"] = g["liters"] / g["m3"]
    g["km_per_m3"] = g["km"] / g["m3"]
    g["jd_per_m3"] = g["cost"] / g["m3"]
    g["gap_std"] = g["gap_std"].fillna(0)
    # مرشّح للفحص: فجوة موجبة معتبرة وثابتة عبر الشهور
    g["candidate"] = ((g["gap_mean"] >= GAP_ALERT) &
                      (g["gap_std"] <= STABLE_STD) &
                      (g["months"] >= 2))
    g["excess_l"] = g["gap_mean"] * g["m3"]
    return g.sort_values("l_per_m3", ascending=False)


def monthly_pivot(tm):
    """لتر/م3 لكل خلاطة موزّعة على الشهور"""
    if tm.empty:
        return pd.DataFrame()
    p = tm.pivot_table(index="truck", columns="label",
                       values="l_per_m3", aggfunc="mean")
    order = (tm.sort_values(["year", "month"])["label"].drop_duplicates().tolist())
    return p[[c for c in order if c in p.columns]]


def load_penalty(months, model, key="client", min_vol=150.0, top=12):
    """
    الاستهلاك الإضافي العائد إلى صغر الحمولات لدى كل عميل أو منطقة أو سائق.
    يُحسب من معامل الحمولة في النموذج: b/متوسط الحمولة − b/متوسط الأسطول.
    """
    if not model:
        return pd.DataFrame()
    frames = [d[d["_qty"] > 0] for d, _, _ in months]
    if not frames:
        return pd.DataFrame()
    allp = pd.concat(frames)
    fleet_avg = float(allp["_qty"].mean())
    b = model["b_load"]

    g = allp.groupby("_" + key)["_qty"].agg(vol="sum", moves="size", avg="mean")
    g = g[g["vol"] >= min_vol].copy()
    g["small_pct"] = allp[allp["_qty"] < 10].groupby("_" + key).size() \
        .reindex(g.index).fillna(0) / g["moves"] * 100
    g["extra_l_per_m3"] = b / g["avg"] - b / fleet_avg
    g["extra_l"] = g["extra_l_per_m3"] * g["vol"]
    g.attrs["fleet_avg"] = fleet_avg
    return g.sort_values("extra_l", ascending=False).head(top)


def driver_fuel(months):
    """
    ربط الاستهلاك بالسائقين عبر الخلاطات التي قادوها.
    تنبيه: كل خلاطة لها سائق أساسي ثابت غالباً، فأثر السائق وأثر الخلاطة
    متداخلان ولا يمكن فصلهما من هذه البيانات.
    """
    rows = []
    for d, dz, (y, m) in months:
        if dz is None or len(dz) == 0:
            continue
        e = A.truck_efficiency(d, dz)
        e = e[(e["truck"] != "0") & (e["km"] > 0) & (e["total"] > 0)]
        if e.empty:
            continue
        rate = dict(zip(e["truck"], e["liters"] / e["total"]))
        prod = d[d["_qty"] > 0].copy()
        prod["_rate"] = prod["_truck"].map(rate)
        prod = prod.dropna(subset=["_rate"])
        prod["_l"] = prod["_rate"] * prod["_qty"]
        rows.append(prod)
    if not rows:
        return pd.DataFrame()
    allp = pd.concat(rows)
    g = allp.groupby("_driver").agg(
        m3=("_qty", "sum"), moves=("_qty", "size"),
        liters=("_l", "sum"), avg_load=("_qty", "mean"),
        trucks=("_truck", "nunique"))
    g["l_per_m3"] = g["liters"] / g["m3"]
    g["small_pct"] = allp[allp["_qty"] < 10].groupby("_driver").size() \
        .reindex(g.index).fillna(0) / g["moves"] * 100
    return g[g["m3"] >= 150].sort_values("l_per_m3", ascending=False)


# ────────────── فصل أثر السائق عن أثر الخلاطة ──────────────

MIN_DRIVER_M3 = 200.0     # أقل كمية تجعل تقدير السائق ذا معنى
DRIVER_ALERT = 0.15       # فرق يستحق المتابعة (لتر/م3)


def driver_effects(months, model):
    """
    ينسب الصرف الزائد أو الناقص إلى السائقين.

    المنطق: لكل خلاطة في كل شهر نعرف صرفها الفعلي والمتوقع من مشاويرها
    وحمولاتها، فالفرق بينهما هو ما لا تفسّره ظروف الشغل. يُوزَّع هذا الفرق
    على سائقي تلك الخلاطة في ذلك الشهر بنسبة الكميات التي نقلها كل منهم،
    ثم تُجمع حصص كل سائق عبر كل الخلاطات والأشهر.

    الفصل ممكن لأن السائقين يتنقّلون بين الخلاطات: سائق يظهر فرقه موجباً
    على عدة خلاطات مختلفة يكون الفرق منه لا منها.
    """
    if not model:
        return pd.DataFrame(), pd.DataFrame()

    tm = truck_month_table(months)
    if tm.empty:
        return pd.DataFrame(), pd.DataFrame()
    tm = tm.copy()
    tm["expected"] = model["pred"]
    tm["gap"] = tm["l_per_m3"] - tm["expected"]
    gap_map = {(r["truck"], r["month"]): r["gap"] for _, r in tm.iterrows()}
    lm3_map = {(r["truck"], r["month"]): r["l_per_m3"] for _, r in tm.iterrows()}

    rows = []
    for d, dz, (y, m) in months:
        if dz is None or len(dz) == 0:
            continue
        prod = d[d["_qty"] > 0]
        for (truck, drv), sub in prod.groupby(["_truck", "_driver"]):
            key = (truck, m)
            if key not in gap_map:
                continue
            truck_total = prod[prod["_truck"] == truck]["_qty"].sum()
            if truck_total <= 0:
                continue
            vol = float(sub["_qty"].sum())
            rows.append({
                "driver": drv, "truck": truck, "month": m,
                "m3": vol, "moves": int(len(sub)),
                "share": vol / truck_total,
                "gap": float(gap_map[key]),
                "l_per_m3": float(lm3_map[key]),
                "avg_load": float(sub["_qty"].mean()),
                "small_pct": float((sub["_qty"] < 10).mean() * 100),
            })
    detail = pd.DataFrame(rows)
    if detail.empty:
        return pd.DataFrame(), pd.DataFrame()

    def wavg(g, col):
        w = g["m3"]
        return float((g[col] * w).sum() / w.sum()) if w.sum() else np.nan

    out = []
    for drv, g in detail.groupby("driver"):
        vol = float(g["m3"].sum())
        if vol < MIN_DRIVER_M3:
            continue
        eff = wavg(g, "gap")
        # ثبات الأثر: انحراف الفروق عبر الخلاطات المختلفة التي قادها
        per_truck = g.groupby("truck").apply(
            lambda x: wavg(x, "gap"), include_groups=False)
        out.append({
            "driver": drv, "m3": vol, "moves": int(g["moves"].sum()),
            "trucks": int(g["truck"].nunique()),
            "months": int(g["month"].nunique()),
            "effect": eff,
            "spread": float(per_truck.std()) if len(per_truck) > 1 else 0.0,
            "n_trucks_pos": int((per_truck > 0).sum()),
            "n_trucks": int(len(per_truck)),
            "avg_load": wavg(g, "avg_load"),
            "small_pct": wavg(g, "small_pct"),
            "l_per_m3": wavg(g, "l_per_m3"),
        })
    t = pd.DataFrame(out)
    if t.empty:
        return t, detail
    t["extra_l"] = t["effect"] * t["m3"]
    # مرشّح للمتابعة: أثر موجب معتبر، متكرر على أغلب خلاطاته
    t["flag"] = ((t["effect"] >= DRIVER_ALERT) & (t["trucks"] >= 2) &
                 (t["n_trucks_pos"] >= t["n_trucks"] * 0.6))
    return t.sort_values("effect", ascending=False), detail
