"""
توحيد أسماء الكيانات عبر الأشهر.

الشيتات القديمة فيها علّتان:
  • آذار–أيار: الأسماء مقصوصة عند نحو 43 حرفاً
  • حزيران: حرف "لا" محذوف من معظم الأسماء
بدونهما تظهر مقارنات كاذبة (عميل "توقّف" وآخر "جديد" وهما نفس الجهة).
"""

import re
import unicodedata

import pandas as pd

_DIAC = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
MIN_PREFIX = 10          # أقل طول للمقارنة بالبادئة


def norm(name):
    """مفتاح مقارنة: بلا تشكيل ولا مسافات ولا 'لا'، بأشكال حروف موحّدة"""
    s = unicodedata.normalize("NFKC", str(name)).strip()
    s = _DIAC.sub("", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"),
                 ("ة", "ه"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)
    s = s.replace("لا", "")          # العلّة الأساسية في حزيران
    s = re.sub(r"^ال", "", s)        # توحيد أداة التعريف: ظهير = الظهير
    s = re.sub(r"[^\w\u0600-\u06FF]", "", s)
    return s


def same(a, b):
    """اسمان لنفس الجهة إذا تطابق مفتاحاهما أو كان أحدهما بادئة الآخر"""
    ka, kb = norm(a), norm(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return len(short) >= MIN_PREFIX and long_.startswith(short)


def build_canonical(name_lists):
    """
    name_lists: قائمة قوائم أسماء (واحدة لكل شهر، الأحدث أخيراً).
    يرجع dict من كل اسم خام إلى الاسم المعتمد (الأطول والأحدث).
    """
    groups = []          # كل مجموعة: {"key": مفتاح أطول، "names": set، "best": اسم}
    for names in name_lists:
        for raw in names:
            raw = str(raw).strip()
            if not raw or raw == "غير محدد":
                continue
            k = norm(raw)
            if not k:
                continue
            hit = None
            for g in groups:
                gk = g["key"]
                short, long_ = (k, gk) if len(k) <= len(gk) else (gk, k)
                if k == gk or (len(short) >= MIN_PREFIX and long_.startswith(short)):
                    hit = g
                    break
            if hit is None:
                groups.append({"key": k, "names": {raw}, "best": raw})
            else:
                hit["names"].add(raw)
                if len(raw) > len(hit["best"]):
                    hit["best"] = raw
                if len(k) > len(hit["key"]):
                    hit["key"] = k

    out = {}
    for g in groups:
        for raw in g["names"]:
            out[raw] = g["best"]
    return out


def apply_canonical(d, mapping, keys=("client", "area", "driver")):
    """يستبدل الأسماء الخام بالمعتمدة داخل جدول مُجهَّز"""
    d = d.copy()
    for k in keys:
        col = "_" + k
        if col in d.columns:
            d[col] = d[col].map(lambda v: mapping.get(str(v).strip(), v))
    return d


# بادئات إدخال خاطئة من الأنظمة تُشطب من اسم المنطقة
_JUNK_WORDS = ["حسبان", "العقبة", "عقبة", "الاولى", "اولى", "الأولى", "أولى",
               "الطفيلة", "طفيلة", "قفقفا", "مادبا", "مأدبا"]
JUNK_PREFIX = re.compile(
    r"^\s*[كلوب]?\s*(?:" + "|".join(_JUNK_WORDS) + r")\s*(?=-|$|\s)", re.U)
_PHONE = re.compile(r"\b0?7[789]\d{7}\b|\b\d{7,}\b")


def clean_area(value):
    """
    تنظيف اسم المنطقة:
      • توحيد أشكال العرض (ﺣﺴﺒﺎﻥ ← حسبان)
      • حذف أرقام الهواتف الملتصقة
      • شطب بادئة «حسبان» بكل صورها، فهي إدخال خاطئ من الأنظمة
      • إزالة الشرطة المتبقية في المقدمة
    """
    s = unicodedata.normalize("NFKC", str(value)).strip()
    s = _PHONE.sub(" ", s)
    s = re.sub(r"^\s*\d+\s*[.\-]\s*", "", s)      # مثل «100.حسبان»
    for _ in range(3):                             # بادئات متداخلة أحياناً
        new = JUNK_PREFIX.sub("", s).lstrip(" -")
        if new == s:
            break
        s = new
    s = re.sub(r"^\s*-\s*", "", s)                 # شرطة بلا بادئة
    s = re.sub(r"\s*-\s*", " - ", s) if " - " not in s and "-" in s else s
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s or "غير محدد"


def unify_areas(series):
    """
    يوحّد أسماء المناطق داخل عمود واحد: ينظّفها ثم يدمج المتغيّرات التي
    تختلف بأداة التعريف أو المسافات، ويعتمد أكثر صورة تكراراً للعرض.
    """
    cleaned = series.map(clean_area)
    keys = cleaned.map(lambda v: re.sub(r"\s+", "", re.sub(r"^ال", "", str(v))))
    best = {}
    for key, grp in cleaned.groupby(keys):
        if not key:
            continue
        best[key] = grp.value_counts().idxmax()
    return pd.Series([best.get(k, c) for c, k in zip(cleaned, keys)],
                     index=series.index)
