"""
توحيد أسماء الكيانات عبر الأشهر.

الشيتات القديمة فيها علّتان:
  • آذار–أيار: الأسماء مقصوصة عند نحو 43 حرفاً
  • حزيران: حرف "لا" محذوف من معظم الأسماء
بدونهما تظهر مقارنات كاذبة (عميل "توقّف" وآخر "جديد" وهما نفس الجهة).
"""

import re
import unicodedata

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
