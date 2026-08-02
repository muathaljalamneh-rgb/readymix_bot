"""
مولّد تقرير HTML — لغة بصرية صناعية.
"""

import html
import datetime as dt
import pandas as pd
import analytics as A

CSS = """
:root{
  --bg:#EDEFF1; --surface:#FFFFFF; --ink:#16191D; --slate:#545C66;
  --hair:#D3D8DC; --steel:#1C4F73; --amber:#C77B18; --alert:#9E2F24;
  --good:#2E6E4F; --mute:#8A929B;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);direction:rtl;
  font-family:"IBM Plex Sans Arabic",system-ui,"Segoe UI",sans-serif;
  font-size:15px;line-height:1.65;padding:20px 14px 60px}
.wrap{max-width:960px;margin:0 auto}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
h1,h2,h3{font-family:"Noto Kufi Arabic",system-ui,sans-serif;font-weight:600;line-height:1.35}

.masthead{border-top:5px solid var(--steel);background:var(--surface);
  padding:22px 24px;margin-bottom:20px}
.masthead h1{font-size:26px;letter-spacing:-.3px}
.masthead .sub{color:var(--slate);font-size:13px;margin-top:6px}
.stamp{display:inline-block;border:1.5px solid var(--steel);color:var(--steel);
  padding:3px 10px;font-size:12px;letter-spacing:1px;margin-bottom:12px;
  font-family:"IBM Plex Mono",monospace}

.sec{margin:30px 0 0}
.sec > h2{font-size:13px;letter-spacing:2.5px;color:var(--slate);
  padding-bottom:8px;border-bottom:1.5px solid var(--ink);margin-bottom:16px}
.note{font-size:13px;color:var(--slate);margin-top:10px}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:1px;
  background:var(--hair);border:1px solid var(--hair)}
.kpi{background:var(--surface);padding:14px 16px}
.kpi .lbl{font-size:12px;color:var(--slate);margin-bottom:4px}
.kpi .val{font-size:25px;font-weight:600;letter-spacing:-.5px}
.kpi .unit{font-size:13px;color:var(--mute);font-weight:400}
.kpi .delta{font-size:11.5px;margin-top:4px;line-height:1.5}
.up{color:var(--good)} .down{color:var(--alert)} .flat{color:var(--mute)}
.crown{color:var(--steel);font-weight:600}

.band{background:var(--surface);border:1px solid var(--hair);padding:16px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;
  background:var(--hair)}
.slot{background:var(--surface);padding:14px}
.slot .p{font-size:13px;font-weight:600;margin-bottom:2px}
.slot .h{font-size:11.5px;color:var(--mute);font-family:"IBM Plex Mono",monospace}
.slot .v{font-size:23px;font-weight:600;margin-top:6px}
.slot .m{font-size:12px;color:var(--slate)}
.bar{height:5px;background:var(--steel);margin-top:8px}

.alert{background:var(--surface);border-right:4px solid var(--slate);
  padding:13px 16px;margin-bottom:9px}
.alert.high{border-right-color:var(--alert)}
.alert.mid{border-right-color:var(--amber)}
.alert.good{border-right-color:var(--good)}
.alert .t{font-weight:600;font-size:14.5px;margin-bottom:3px}
.alert .d{font-size:13.5px;color:var(--slate)}

.finds{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.find{background:var(--surface);border:1px solid var(--hair);padding:16px}
.find h3{font-size:14px;margin-bottom:8px}
.find p{font-size:13.5px;color:var(--slate)}
.find .big{font-size:22px;font-weight:600;color:var(--steel);
  font-family:"IBM Plex Mono",monospace;display:block;margin:6px 0}

table{width:100%;border-collapse:collapse;background:var(--surface);font-size:13px}
caption{text-align:right;font-weight:600;padding:10px 12px;font-size:14px;
  background:var(--surface);border:1px solid var(--hair);border-bottom:0;
  font-family:"Noto Kufi Arabic",sans-serif}
th{background:#F5F6F7;font-weight:600;font-size:11.5px;color:var(--slate);
  text-align:right;padding:8px 10px;border-bottom:1.5px solid var(--ink)}
td{padding:7px 10px;border-bottom:1px solid var(--hair)}
tr:last-child td{border-bottom:0}
.tbl{border:1px solid var(--hair);margin-bottom:18px;overflow-x:auto}
td.n,th.n{text-align:left;font-family:"IBM Plex Mono",monospace}
tr.bad td{background:#FBF2F1}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px}

.heat{background:var(--surface);border:1px solid var(--hair);padding:14px;overflow-x:auto}
.heat .row{display:grid;grid-template-columns:132px repeat(var(--cols),1fr);gap:2px;
  align-items:center;margin-bottom:2px}
.heat .lab{font-size:11.5px;color:var(--slate);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;padding-left:6px}
.heat .cell{height:26px;display:flex;align-items:center;justify-content:center;
  font-size:10.5px;font-family:"IBM Plex Mono",monospace;color:#fff}
.heat .hd{height:auto;color:var(--mute);background:none!important;font-size:11px}

.narr{background:var(--surface);border:1px solid var(--hair);padding:20px 22px}
.narr p{margin:0 0 10px;font-size:14.5px;line-height:1.75}
.narr .narr-h{font-size:15px;margin:18px 0 8px;color:var(--steel);
  padding-bottom:5px;border-bottom:1px solid var(--hair)}
.narr .narr-h:first-child{margin-top:0}
.narr .narr-ul{margin:0 0 10px;padding-right:18px}
.narr .narr-ul li{margin-bottom:8px;font-size:14.5px;line-height:1.7}
.pend{background:var(--surface);border:1px dashed var(--hair);padding:16px;
  color:var(--mute);font-size:13.5px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--hair);
  font-size:12px;color:var(--mute)}
@media(max-width:560px){body{font-size:14px;padding:12px 8px 40px}
  .kpi .val{font-size:21px}.masthead{padding:16px}
  .heat .row{grid-template-columns:96px repeat(var(--cols),1fr)}}
@media print{body{background:#fff}.sec{break-inside:avoid}}
"""


def esc(s):
    return html.escape(str(s))


def kpi_vs_best(metric, k, all_kpis):
    lbl, unit, better_up, dd = A.COMPARE[metric]
    cur = k.get(metric)
    if cur is None:
        return ""
    b = A.best_month(all_kpis, metric)
    if b is None or b["label"] == k["label"]:
        d = '<div class="delta crown">أفضل قراءة مسجّلة</div>'
    else:
        diff = cur - b[metric]
        pct = diff / b[metric] * 100 if b[metric] else 0
        good = (diff > 0) == better_up
        cls = "up" if good else "down"
        arrow = "▲" if diff > 0 else "▼"
        ref = "أدنى نسبة مسجّلة" if not better_up else "أفضل شهر"
        d = (f'<div class="delta {cls}">{arrow} {abs(pct):.1f}% عن {ref}<br>'
             f'<span class="num">{b[metric]:,.{dd}f}</span> في {esc(b["label"])}</div>')
    return (f'<div class="kpi"><div class="lbl">{esc(lbl)}</div>'
            f'<div class="val num">{cur:,.{dd}f}<span class="unit"> {esc(unit)}</span></div>'
            f'{d}</div>')


def plain_kpi(lbl, val, unit=""):
    return (f'<div class="kpi"><div class="lbl">{esc(lbl)}</div>'
            f'<div class="val num">{val}<span class="unit"> {esc(unit)}</span></div></div>')


def table(title, df, cols, fmts=None, bad=None):
    if df is None or len(df) == 0:
        return (f'<div class="tbl"><caption>{esc(title)}</caption>'
                f'<table><tr><td>لا توجد بيانات كافية</td></tr></table></div>')
    fmts = fmts or {}
    head = "".join(f'<th class="n">{esc(c[1])}</th>' if i else f'<th>{esc(c[1])}</th>'
                   for i, c in enumerate(cols))
    body = []
    for idx, r in df.iterrows():
        tds = []
        for i, (key, _) in enumerate(cols):
            v = idx if key == "_index" else r.get(key, "")
            if key in fmts:
                v = fmts[key](v)
            elif isinstance(v, float):
                v = f"{v:,.1f}"
            tds.append(f'<td class="n">{esc(v)}</td>' if i else f"<td>{esc(v)}</td>")
        cls = ' class="bad"' if bad and bad(r) else ""
        body.append(f"<tr{cls}>" + "".join(tds) + "</tr>")
    return (f'<div class="tbl"><caption>{esc(title)}</caption><table>'
            f"<tr>{head}</tr>{''.join(body)}</table></div>")


def periods_band(pp):
    if pp is None or pp.empty:
        return ""
    hours = {p[0]: p[1] for p in A.PERIODS}
    mx = pp["vol_pct"].max()
    slots = []
    for name, r in pp.iterrows():
        slots.append(f"""<div class="slot"><div class="p">{esc(name)}</div>
<div class="h">{esc(hours.get(name,''))}</div>
<div class="v num">{r['vol_pct']:.1f}%</div>
<div class="m">{r['total']:,.0f}م3 · {int(r['moves'])} حركة · متوسط {r['avg']:.2f}</div>
<div class="bar" style="width:{r['vol_pct']/mx*100:.0f}%"></div></div>""")
    return f'<div class="band">{"".join(slots)}</div>'


def matrix_heat(m, title):
    """مصفوفة نسب مئوية: صفوف × فترات"""
    if m is None or m.empty:
        return ""
    periods = [p[0] for p in A.PERIODS]
    hd = "".join(f'<div class="cell hd">{esc(p.replace("الفترة ","").replace("فترة ",""))}</div>'
                 for p in periods) + '<div class="cell hd">م3</div>'
    rows = [f'<div class="row"><div class="lab"></div>{hd}</div>']
    for name, r in m.iterrows():
        cells = ""
        for p in periods:
            v = float(r.get(p, 0) or 0)
            a = 0.10 + 0.90 * (v / 100)
            cells += (f'<div class="cell" style="background:rgba(28,79,115,{a:.2f})">'
                      f'{v:.0f}</div>')
        cells += (f'<div class="cell" style="background:#F5F6F7;color:var(--slate)">'
                  f'{r["الإجمالي"]:,.0f}</div>')
        rows.append(f'<div class="row"><div class="lab" title="{esc(name)}">'
                    f'{esc(name)}</div>{cells}</div>')
    return (f'<div class="tbl" style="border:0"><caption>{esc(title)}</caption></div>'
            f'<div class="heat" style="--cols:5">{"".join(rows)}</div>')


def hours_heat(hp):
    if hp is None or hp.empty:
        return ""
    hours = sorted(hp.index.tolist())
    mx = hp["moves"].max()
    labs = "".join(f'<div class="cell hd">{h}</div>' for h in hours)
    mv = "".join(
        f'<div class="cell" style="background:rgba(28,79,115,'
        f'{0.15+0.85*hp.loc[h,"moves"]/mx:.2f})">{int(hp.loc[h,"moves"])}</div>'
        for h in hours)
    pc = "".join(
        f'<div class="cell" style="background:rgba(199,123,24,'
        f'{0.15+0.85*hp.loc[h,"pct"]/hp["pct"].max():.2f})">{hp.loc[h,"pct"]:.1f}</div>'
        for h in hours)
    return f"""<div class="heat" style="--cols:{len(hours)}">
<div class="row"><div class="lab">الساعة</div>{labs}</div>
<div class="row"><div class="lab">عدد الحركات</div>{mv}</div>
<div class="row"><div class="lab">% من الكمية</div>{pc}</div></div>"""


def build(d, year, month, tab, all_kpis, diesel=None,
          findings=None, narrative_html=None):
    k = A.kpis(d, year, month)
    alerts = A.build_alerts(d, k, all_kpis, year, month)
    pp = A.period_profile(d)
    hp = A.hour_profile(d)
    wp = A.weekday_profile(d)
    tr = A.truck_report(d)
    dt_ = A.driver_trips(d)
    dv = A.driver_vehicle_matrix(d)
    con = A.concentration(d)
    lle = A.last_load_effect(d)
    ta = A.turnaround(d)
    errs = A.error_by_client(d, year, month)
    mname = f"{A.MONTH_AR[month]} {year}"

    # ── القراءة التنفيذية والتحوّلات ──
    narr_sec = ""
    if narrative_html:
        narr_sec = (f'<div class="sec"><h2>القراءة التنفيذية</h2>'
                    f'<div class="narr">{narrative_html}</div>'
                    f'<div class="note">كُتبت هذه القراءة آلياً اعتماداً على الأرقام '
                    f'المحسوبة في هذا التقرير. الأرقام مصدرها الحسابات لا النموذج.'
                    f'</div></div>')

    find_sec = ""
    if findings:
        cats = {}
        for f in findings:
            cats.setdefault(f["category"], []).append(f)
        blocks = ""
        for cat, items in cats.items():
            blocks += f'<h3 style="margin:16px 0 8px;font-size:14px">{esc(cat)}</h3>'
            blocks += "".join(
                f'<div class="alert {x["severity"]}">'
                f'<div class="t">{esc(x["title"])}</div>'
                f'<div class="d">{esc(x["detail"])}</div></div>' for x in items)
        find_sec = (f'<div class="sec"><h2>تحوّلات مقارنةً بالأشهر السابقة</h2>'
                    f'{blocks}<div class="note">مرصودة آلياً بمقارنة كل جهة بتاريخها '
                    f'الخاص. أسماء العملاء والمناطق والسائقين مُوحَّدة قبل المقارنة لأن '
                    f'الشيتات القديمة تحوي أسماء مقصوصة أو ناقصة الحروف.</div></div>')

    # ── تسوية الإنتاج ──
    rc = A.reconcile(d, year, month)
    recon_block = build_reconciliation(rc)

    # ── الديزل ──
    diesel_block = build_diesel(d, diesel)

    # ── خلاصة الرواتب ──
    import salary as S
    st, sdl = S.compute(d)
    ss = S.summary(st)
    if ss:
        pt = S.compute_pumps(d, A)
        pw = S.compute_pump_workers(d, A)
        op_total = float(pt["total_operator"].sum()) if len(pt) else 0.0
        wk_total = float(pw["total"].sum()) if len(pw) else 0.0
        grand = ss["confirmed"] + op_total + wk_total
        prod_m3 = float(d[d["_qty"] > 0]["_qty"].sum())

        cat = "".join([
            plain_kpi("إجمالي عنبر النقل", f"{grand:,.2f}", "دينار"),
            plain_kpi("سائقو الخلاطات", f"{ss['confirmed']:,.2f}", "دينار"),
            plain_kpi("مشغّلو المضخات", f"{op_total:,.2f}", "دينار"),
            plain_kpi("عمّال المضخات", f"{wk_total:,.2f}", "دينار"),
            plain_kpi("كلفة الحوافز لكل م3", f"{grand/max(prod_m3,1):.3f}", "دينار"),
            plain_kpi("عدد المستحقين",
                      f"{ss['drivers'] + len(pt) + len(pw)}", "شخص"),
        ])

        rows = "".join(
            f'<tr><td>{esc(r["driver"])}</td><td>سائق خلاطة</td>'
            f'<td class="n">{int(r["trips"])}</td>'
            f'<td class="n">{r["confirmed"]:,.2f}</td></tr>'
            for _, r in st.head(3).iterrows())
        if len(pt):
            rows += "".join(
                f'<tr><td>{esc(r["driver"])}</td><td>مشغّل مضخة</td>'
                f'<td class="n">{int(r["jobs"])}</td>'
                f'<td class="n">{r["total_operator"]:,.2f}</td></tr>'
                for _, r in pt.head(3).iterrows())

        # التشوّهات
        distort = []
        if ss["delay_all"]:
            distort.append(
                f"<b>فترات الانتظار — {ss['delay_all']} فجوة مرصودة، والرقم "
                f"لا يعني {ss['delay_all']} حالة تأخير:</b> رُصدت كل فجوة تتجاوز "
                f"{S.DELAY_HOURS:g} ساعات بين حركتين متتاليتين لنفس السائق في نفس "
                f"اليوم. بمعدل نحو {ss['delay_all']/max(ss['drivers'],1):.0f} فجوة "
                f"لكل سائق شهرياً، أي واحدة تقريباً في كل يوم عمل — وهذا يكشف أن "
                f"معظمها إيقاع عمل طبيعي وفراغ في الطلب لا انتظاراً. القاعدة تمنح "
                f"البدل عن التأخير الناتج عن أزمة أو عن العميل نفسه، وهذا السبب غير "
                f"مسجّل في الشيت إطلاقاً. لذلك أُخذ مؤشر أضيق: أن يكون العميل نفسه "
                f"قبل الفجوة وبعدها، أي أن السائق ظلّ مرتبطاً بذلك العميل طوال "
                f"المدة — وهذه {ss['delay_strong']} حالة فقط بتكلفة "
                f"{ss['delay_est']:,.2f} دينار. لم تُضف إلى المستحقات، وقائمتها "
                f"بالتواريخ والساعات والعملاء في كشف الحوافز لمراجعتها واعتماد ما "
                f"يستحق منها.")
        no_time = int(pt["no_time"].sum()) if len(pt) and "no_time" in pt else 0
        if no_time:
            distort.append(
                f"<b>أوقات ناقصة:</b> {no_time} مهمة مضخة بلا وقت مسجّل، فبدلات "
                f"السروة والسهرة والعشاء لتلك الأيام قد تكون ناقصة.")
        if len(pw):
            distort.append(
                "<b>عمّال المضخات:</b> أسماؤهم غير مسجّلة، فنُسب كل عامل إلى رقم "
                "مضخته وحُسبت بدلاته من أوقات عمل المضخة. إن كان دوام العامل يختلف "
                "عن دوام المضخة فالبدلات تحتاج تصحيحاً يدوياً.")
        if len(pt):
            multi = pt[pt["pumps"] > 1]
            if len(multi):
                distort.append(
                    f"<b>تنقّل بين المضخات:</b> {len(multi)} مشغّلاً شغّلوا أكثر من "
                    f"مضخة خلال الشهر، ما يجعل نسبة الاستهلاك والبدلات لكل مضخة "
                    f"غير محصورة بشخص واحد.")
        distort.append(
            "<b>بنود غير محسوبة:</b> تحويل سائق الخلاطة إلى سائق مضخة أو إلى مشغّل "
            "مضخة لا يظهر في البيانات ما لم يُسجَّل اسمه على صف مضخة.")

        dist_html = "".join(
            f'<div class="alert mid"><div class="d">{x}</div></div>' for x in distort)

        salary_block = f"""<div class="kpis">{cat}</div>
<div class="tbl" style="margin-top:14px"><caption>أعلى المستحقات</caption><table>
<tr><th>الاسم</th><th>الفئة</th><th class="n">نقلات/مهمات</th>
<th class="n">المستحق</th></tr>{rows}</table></div>
<div class="note">أعلى مستحق بين سائقي الخلاطات {esc(ss['max_name'])} بـ
{ss['max_val']:,.2f} دينار، وأدناه {esc(ss['min_name'])} بـ {ss['min_val']:,.2f}
دينار. التفصيل الكامل في كشف الحوافز المنفصل.</div>
<h3 style="margin:18px 0 10px;font-size:14px">تشوّهات تؤثر على دقة الاحتساب</h3>
{dist_html}"""
    else:
        salary_block = '<div class="pend">لا توجد بيانات لاحتساب الحوافز.</div>'

    # ── الفاقد والراجع أولاً ──
    if k["has_loss"]:
        loss_card = plain_kpi("الفاقد (كميات مُتلَفة)", f"{k['loss']:,.1f}", "م3")
    else:
        loss_card = ('<div class="kpi"><div class="lbl">الفاقد (كميات مُتلَفة)</div>'
                     '<div class="val" style="font-size:15px;color:var(--mute)">'
                     'عمود الفاقد غير موجود في هذا الشهر</div></div>')

    if k["err"] is not None:
        head_cards = f"""{loss_card}
{plain_kpi("أخطاء حركة — رُفض الاستلام", f"{k['err']:,.1f}", "م3")}
{plain_kpi("عدد حالات الرفض", f"{k['err_cases']}", "حالة")}
{plain_kpi("مرتجع أُعيد بيعه (مكسب)", f"{k['resold']:,.1f}", "م3")}"""
        loss_note = ("""<div class="note">الراجع المطالب به هو خطأ حركة: البضاعة خرجت
للعميل ورفض استلامها، فتحمّلت الشركة ديزلاً ذاهباً وعائداً وإعادة معالجة.
أما الراجع غير المطالب به فهو مكسب: رجع وبيع لعميل آخر دون خصمه من العميل الأصلي.
الفاقد خسارة كاملة لأنه أُتلف.</div>""")
    else:
        head_cards = loss_card
        loss_note = ('<div class="note">بيانات الراجع قبل حزيران 2026 مسجّلة بطريقة '
                     'مختلفة وأرقامها غير موثوقة، فاستُبعدت من هذا الشهر.</div>')

    # ── المؤشرات مقابل أفضل شهر ──
    main_kpis = "".join(kpi_vs_best(m, k, all_kpis) for m in
                        ("total", "moves", "avg", "per_day", "per_truck",
                         "lt10_pct", "lt5_pct"))
    main_kpis += plain_kpi("أيام العمل", f"{k['days']}", "يوم")

    # ── التنبيهات ──
    al = "".join(f'<div class="alert {a["level"]}"><div class="t">{esc(a["title"])}</div>'
                 f'<div class="d">{esc(a["detail"])}</div></div>' for a in alerts) or \
        '<div class="alert good"><div class="t">لا تنبيهات</div></div>'

    # ── استنتاجات ──
    finds = []
    if lle:
        finds.append(f"""<div class="find"><h3>أثر آخر حمولة في اليوم</h3>
<span class="big">{lle['last_lt10_pct']:.0f}% مقابل {lle['rest_lt10_pct']:.0f}%</span>
<p>آخر حركة لكل خلاطة ({lle['last_n']} حركة) نسبة الحمولات الناقصة فيها أعلى بكثير.
المتوسط ينزل من {lle['rest_avg']:.2f} إلى {lle['last_avg']:.2f}م3 — الطلبات المتبقية
تُصرف بحمولات جزئية آخر الدوام.</p></div>""")
    if con:
        finds.append(f"""<div class="find"><h3>تركّز قاعدة العملاء</h3>
<span class="big">{con['top5']:.1f}%</span>
<p>أكبر 5 عملاء من أصل {con['n']} يأخذون {con['top5']:.1f}% من الإنتاج، وأكبرهم وحده
{con['top1']:.1f}%. مؤشر HHI = {con['hhi']:.0f}
({'تركّز مرتفع' if con['hhi']>1500 else 'توزيع صحي نسبياً'}).</p></div>""")
    if not ta.empty:
        med = ta["median_min"].median()
        finds.append(f"""<div class="find"><h3>زمن الدورة بين الحركتين</h3>
<span class="big">{med:.0f} دقيقة</span>
<p>وسيط الأسطول. أبطأ خلاطة {esc(ta.iloc[0]['truck'])} بـ {ta.iloc[0]['median_min']:.0f}
دقيقة، وأسرعها {esc(ta.iloc[-1]['truck'])} بـ {ta.iloc[-1]['median_min']:.0f} دقيقة.
كل دقيقة فارق تعني حركات أقل في اليوم.</p></div>""")
    if not tr.empty:
        below = tr[tr["below_target"]]
        finds.append(f"""<div class="find"><h3>الخلاطات تحت الحد المطلوب</h3>
<span class="big">{len(below)} من {len(tr)}</span>
<p>الحد {A.MIN_TRUCK_MONTHLY:,.0f}م3 شهرياً لكل خلاطة. مجموع النقص
{below['shortfall'].sum():,.0f}م3، ومتوسط أيام التعطّل لديها
{below['idle_days'].mean() if len(below) else 0:.1f} يوم مقابل
{tr[~tr['below_target']]['idle_days'].mean():.1f} يوم لبقية الأسطول.</p></div>""")

    # ── الجداول ──
    F_T = {"total": lambda v: f"{v:,.0f}", "moves": lambda v: f"{int(v)}",
           "drivers": lambda v: f"{int(v)}", "other_moves": lambda v: f"{int(v)}",
           "active_days": lambda v: f"{int(v)}", "idle_days": lambda v: f"{int(v)}",
           "main_share": lambda v: f"{v:.0f}%", "shortfall": lambda v: f"{v:,.0f}"}

    trucks_tbl = table("كل الخلاطات — الإنتاج والالتزام بالحد", tr,
        [("truck", "الخلاطة"), ("total", "م3"), ("moves", "حركة"),
         ("active_days", "أيام عمل"), ("idle_days", "أيام تعطّل"),
         ("drivers", "عدد السائقين"), ("other_moves", "حركات بسائق آخر"),
         ("shortfall", "النقص عن 500")],
        F_T, bad=lambda r: r["below_target"] or r["other_moves"] > 0)

    swap_tbl = table("خلاطات قادها غير سائقها الأساسي",
        tr[tr["other_moves"] > 0].sort_values("other_moves", ascending=False),
        [("truck", "الخلاطة"), ("main_driver", "السائق الأساسي"),
         ("main_share", "حصته"), ("other_moves", "حركات بسائق آخر"),
         ("driver_list", "السائقون")], F_T)

    # جدول موحّد: كل مستحق حسب تصنيفه وحوافزه
    import pandas as _pd
    st2, _ = S.compute(d)
    pt2 = S.compute_pumps(d, A)
    pw2 = S.compute_pump_workers(d, A)
    people = []
    for _, r in st2.iterrows():
        people.append({"name": r["driver"], "role": "سائق خلاطة",
                       "count": int(r["trips"]), "vol": float(r["volume"]),
                       "days": int(r["days"]), "pay": float(r["confirmed"])})
    for _, r in pt2.iterrows():
        people.append({"name": r["driver"], "role": "مشغّل مضخة",
                       "count": int(r["jobs"]), "vol": float(r["pumped"]),
                       "days": int(r["days"]), "pay": float(r["total_operator"])})
    for _, r in pw2.iterrows():
        people.append({"name": r["worker"], "role": "عامل مضخة",
                       "count": int(r["jobs"]), "vol": float(r["pumped"]),
                       "days": int(r["days"]), "pay": float(r["total"])})
    ppl = _pd.DataFrame(people).sort_values("pay", ascending=False)
    ppl["per_unit"] = ppl["pay"] / ppl["count"].replace(0, 1)

    drivers_tbl = table("الحوافز والمخصصات لكل شخص حسب تصنيفه", ppl,
        [("name", "الاسم"), ("role", "التصنيف"), ("count", "نقلات/مهمات"),
         ("vol", "م3"), ("days", "أيام"), ("per_unit", "دينار للوحدة"),
         ("pay", "المستحق")],
        {"count": lambda v: f"{int(v)}", "days": lambda v: f"{int(v)}",
         "vol": lambda v: f"{v:,.0f}", "pay": lambda v: f"{v:,.2f}",
         "per_unit": lambda v: f"{v:.2f}"})

    C_RATE = [("name", ""), ("rate", "دقيقة/م3"), ("total", "م3"),
              ("moves", "حركة"), ("bonds", "سندات"),
              ("avg_duration", "متوسط مدة السند"), ("avg_load", "متوسط الحمولة"),
              ("morning_pct", "صباحية"), ("noon_pct", "ظهيرة"),
              ("peak_pct", "ذروة"), ("evening_pct", "مسائية")]
    F_RATE = {"rate": lambda v: f"{v:.2f}", "total": lambda v: f"{v:,.1f}",
              "moves": lambda v: f"{int(v)}", "bonds": lambda v: f"{int(v)}",
              "avg_duration": lambda v: f"{v:,.0f}",
              "avg_load": lambda v: f"{v:.2f}",
              "morning_pct": lambda v: f"{v:.0f}%",
              "noon_pct": lambda v: f"{v:.0f}%",
              "peak_pct": lambda v: f"{v:.0f}%",
              "evening_pct": lambda v: f"{v:.0f}%"}

    cli_rate = A.pour_rate_by(d, "client")
    area_rate = A.pour_rate_by(d, "area")

    wk_rows = "".join(
        f'<tr><td>{A.WEEKDAY_AR[int(i)]}</td><td class="n">{r["total"]:,.0f}</td>'
        f'<td class="n">{int(r["moves"]):,}</td><td class="n">{r["avg"]:.2f}</td>'
        f'<td class="n">{r["per_day"]:,.0f}</td></tr>'
        for i, r in wp.iterrows()) if not wp.empty else ""

    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>تقرير الإنتاج — {esc(mname)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600&family=Noto+Kufi+Arabic:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body><div class="wrap">

<div class="masthead"><div class="stamp">تقرير شهري</div>
<h1>إنتاج الخرسانة الجاهزة — {esc(mname)}</h1>
<div class="sub">{k['moves']:,} حركة · {k['trucks']} خلاطة · {k['drivers']} سائق ·
{k['clients']} عميل · {k['bonds']} سند · {k['areas']} منطقة<br>
التبويب {esc(tab)} · صدر في {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}</div></div>

{narr_sec}

<div class="sec"><h2>تسوية الإنتاج — من الحركة إلى صافي البيع</h2>
{recon_block}</div>

<div class="sec"><h2>الفاقد والكميات الراجعة</h2>
<div class="kpis">{head_cards}</div>{loss_note}</div>

<div class="sec"><h2>المؤشرات مقارنةً بأفضل شهر مسجّل</h2>
<div class="kpis">{main_kpis}</div>
<div class="note">كل مؤشر يُقارن بأفضل قيمة تحققت في أي شهر سابق، لا بالشهر السابق
مباشرةً — المرجع هو أفضل ما وصلت إليه الشركة فعلاً.</div></div>

<div class="sec"><h2>ما يحتاج انتباهك</h2>{al}</div>

{find_sec}

<div class="sec"><h2>فترات التحميل</h2>
{periods_band(pp)}
<div class="note">توزيع الكمية على فترات اليوم الأربع.</div></div>

<div class="sec"><h2>التحميل على مدار الساعة</h2>
{hours_heat(hp)}</div>

<div class="sec"><h2>سرعة الصب لدى العملاء</h2>
{table("كل العملاء — مرتّبين من الأبطأ إلى الأسرع", cli_rate, C_RATE, F_RATE,
       bad=lambda r: r["rate"] > 6)}
<div class="note">معدل الصب = مدة السند مقسومة على كميته. مدة السند تُحسب من أول
حركة إلى آخر حركة فيه شاملةً المضخة. الأقل أفضل: العميل الذي يستهلك دقائق أكثر لكل
متر يحتجز الخلاطات والمضخة وقتاً أطول لنفس الكمية. الصفوف المظللة تتجاوز 6 دقائق
للمتر. النسب توزيع كميته على فترات اليوم.</div></div>

<div class="sec"><h2>العملاء × الفترات</h2>
{matrix_heat(A.cross_period(d, "client", 12), "نسبة كمية كل عميل الموزّعة على الفترات (%)")}</div>

<div class="sec"><h2>سرعة الصب حسب المنطقة</h2>
{table("المناطق — مرتّبة من الأبطأ إلى الأسرع", area_rate, C_RATE, F_RATE,
       bad=lambda r: r["rate"] > 6)}
<div class="note">المنطقة البطيئة قد تعني صعوبة وصول أو ازدحاماً في الموقع لا
تقصيراً من السائق.</div></div>

<div class="sec"><h2>المناطق × الفترات</h2>
{matrix_heat(A.cross_period(d, "area", 12), "نسبة كمية كل منطقة الموزّعة على الفترات (%)")}</div>

<div class="sec"><h2>الخلاطات</h2>
{trucks_tbl}
<div class="note"><b>عمود «حركات بسائق آخر»:</b> لكل خلاطة يُحدَّد سائقها الأساسي
وهو الأكثر قيادةً لها خلال الشهر، ثم يُحصى كم حركة قادها غيره. الرقم لا يعني أن
الخلاطة تنقّلت بين سائقين بالتساوي — قد يكون سائقها الأساسي قادها 95% من الحركات
والباقي بديل ليوم أو يومين. المقصود رصد مخالفة قاعدة «كل سائق على خلاطته»،
والعدد هو حجم المخالفة لا عدد الأشخاص.
<br><b>أيام التعطّل:</b> أيام عمل المصنع التي لم تسجّل فيها الخلاطة أي حركة، سواء
لعطل أو لعدم تشغيلها.
<br>الصفوف المظللة إما تحت حد {A.MIN_TRUCK_MONTHLY:,.0f}م3 أو فيها حركات بسائق آخر.</div>
{swap_tbl}</div>

<div class="sec"><h2>الحوافز والمخصصات لكل شخص</h2>{drivers_tbl}
<div class="note">«نقلات/مهمات» عدد النقلات لسائقي الخلاطات وعدد مهمات الضخ
لعنبر المضخات. «م3» الكمية المنقولة للسائق والمضخوخة للمضخة. «دينار للوحدة» متوسط
ما يتقاضاه عن النقلة أو المهمة شاملاً البدلات. عمّال المضخات منسوبون إلى أرقام
مضخاتهم لأن أسماءهم غير مسجّلة. التفصيل الكامل في كشف الحوافز المنفصل.</div></div>

<div class="sec"><h2>أيام الأسبوع</h2>
<div class="tbl"><table><tr><th>اليوم</th><th class="n">م3</th><th class="n">حركة</th>
<th class="n">متوسط</th><th class="n">م3/يوم</th></tr>{wk_rows}</table></div></div>

<div class="sec"><h2>استنتاجات من البيانات</h2>
<div class="finds">{''.join(finds)}</div></div>

<div class="sec"><h2>أخطاء الحركة حسب العميل</h2>
{table("العملاء الأعلى في رفض الاستلام", errs,
   [("_index","العميل"),("err","خطأ حركة م3"),("cases","حالات"),
    ("resold","أُعيد بيعه"),("pct","% من كميته")],
   {"cases": lambda v: f"{int(v)}", "pct": lambda v: f"{v:.1f}%"})}</div>

<div class="sec"><h2>الديزل والمسافات</h2>
{diesel_block}</div>

<div class="sec"><h2>حوافز ومخصصات عنبر النقل — الخلاصة</h2>
{salary_block}</div>

<footer>صفوف المضخة (الكمية = صفر) مستبعدة من حسابات الإنتاج: {k['pumps']:,} حركة.
المصدر: ReadyMix_Production_Data / {esc(tab)}.</footer>
</div></body></html>"""


def build_diesel(d, diesel):
    """قسم الديزل: الكلفة لكل م3 أولاً، ثم تفسير الفروق بعد تحييد المسافة والحمولة"""
    import numpy as np
    import fleet as FL
    if diesel is None or len(diesel) == 0:
        return ('<div class="pend">لا يوجد جدول ديزل في هذا الشهر. الأعمدة المطلوبة: '
                'رقم السياره، اجمالي دينار، عدد اللترات، عدد الكيلومترات.</div>')

    e = A.truck_efficiency(d, diesel)
    e = e[(e["truck"] != "0") & (e["km"] > 0)].copy()
    if e.empty:
        return '<div class="pend">تعذّر مطابقة أرقام السيارات مع جدول الديزل.</div>'

    e["make"] = e["truck"].map(FL.make_of)
    tot_cost, tot_l, tot_km = e["cost"].sum(), e["liters"].sum(), e["km"].sum()
    tot_m3 = e["total"].sum()
    fleet_rate = tot_cost / max(tot_m3, 1)
    e["gap_jd"] = e["jd_per_m3"] - fleet_rate
    e["impact"] = e["gap_jd"] * e["total"]
    e = e.sort_values("jd_per_m3", ascending=False)
    over = e[e["impact"] > 0]["impact"].sum()

    head = "".join([
        plain_kpi("كلفة الديزل لكل م3", f"{fleet_rate:.3f}", "دينار"),
        plain_kpi("إجمالي كلفة الديزل", f"{tot_cost:,.0f}", "دينار"),
        plain_kpi("اللترات", f"{tot_l:,.0f}", "لتر"),
        plain_kpi("المسافة", f"{tot_km:,.0f}", "كم"),
        plain_kpi("م3 لكل لتر", f"{tot_m3/max(tot_l,1):.3f}", "م3"),
        plain_kpi("سعر اللتر الفعلي", f"{tot_cost/max(tot_l,1):.3f}", "دينار"),
    ])

    F = {"total": lambda v: f"{v:,.0f}", "liters": lambda v: f"{v:,.0f}",
         "cost": lambda v: f"{v:,.0f}", "km": lambda v: f"{v:,.0f}",
         "jd_per_m3": lambda v: f"{v:.2f}", "gap_jd": lambda v: f"{v:+.2f}",
         "impact": lambda v: f"{v:+,.0f}", "km_per_move": lambda v: f"{v:.1f}",
         "expected_l": lambda v: f"{v:,.0f}", "excess_l": lambda v: f"{v:+,.0f}",
         "excess_jd": lambda v: f"{v:+,.0f}", "l_per_m3": lambda v: f"{v:.2f}"}

    cost_tbl = table("كلفة الديزل لكل م3 — كل خلاطة", e,
        [("truck", "الخلاطة"), ("make", "الصانع"), ("total", "م3"),
         ("km", "كم"), ("km_per_move", "كم/حركة"), ("liters", "لتر"),
         ("cost", "دينار"), ("jd_per_m3", "دينار/م3"),
         ("gap_jd", "الفرق عن المتوسط"), ("impact", "الأثر بالدينار")],
        F, bad=lambda r: r["gap_jd"] > 0.4)

    # مقارنة الصانع
    g = e.groupby("make").agg(
        n=("truck", "size"), m3=("total", "sum"), km=("km", "sum"),
        liters=("liters", "sum"), cost=("cost", "sum"))
    g["jd_m3"] = g["cost"] / g["m3"]
    g["per_truck"] = g["m3"] / g["n"]
    g["km_l"] = g["km"] / g["liters"]
    g["m3_l"] = g["m3"] / g["liters"]
    g["km_per_m3"] = g["km"] / g["m3"]
    rows = "".join(
        f'<tr><td>{esc(i)}</td><td class="n">{int(r["n"])}</td>'
        f'<td class="n">{r["m3"]:,.0f}</td><td class="n">{r["per_truck"]:,.0f}</td>'
        f'<td class="n">{r["km_per_m3"]:.2f}</td><td class="n">{r["km_l"]:.3f}</td>'
        f'<td class="n">{r["m3_l"]:.3f}</td>'
        f'<td class="n">{r["jd_m3"]:.2f}</td></tr>'
        for i, r in g.sort_values("jd_m3").iterrows())

    worst, best = e.iloc[0], e.iloc[-1]

    # القسم التفسيري — ثانوي
    explain = ""
    if "excess_l" in e.columns and e["excess_l"].notna().sum() >= 5:
        m = e.attrs.get("model") or A.fuel_model(e[e["km"] > 0])
        adj = e.sort_values("excess_l", ascending=False)
        adj_tbl = table("الاستهلاك مقابل المتوقع لمسافة كل خلاطة وكميتها", adj,
            [("truck", "الخلاطة"), ("km", "كم"), ("total", "م3"),
             ("liters", "لتر فعلي"), ("expected_l", "لتر متوقع"),
             ("excess_l", "الفارق"), ("excess_jd", "دينار")], F,
            bad=lambda r: (r["excess_l"] or 0) > 150)
        explain = f"""<div class="narr" style="margin-top:20px">
<h3 class="narr-h">لماذا تختلف الكلفة بين خلاطة وأخرى</h3>
<p>الجدول أعلاه يقيس الكلفة كما تقع على الشركة فعلاً، وهو المقياس المعتمد. لكنه
لا يفصل بين سببين مختلفين تماماً: خلاطة تكلّف أكثر لأنها تخدم مواقع بعيدة، وخلاطة
تكلّف أكثر لأن محرّكها أو تشغيلها فيه خلل. الأولى تؤدي عملاً أصعب والثانية تهدر.</p>

<p>للفصل بينهما قُدِّرت من بيانات هذا الشهر معادلةٌ تربط اللترات بالمسافة والكمية
لدى {len(e)} خلاطة:</p>

<p style="text-align:center;font-family:'IBM Plex Mono',monospace;font-size:15px;
background:#F5F6F7;padding:12px;margin:12px 0;direction:ltr">
لتر = {m['per_km']:.3f} × كم + {m['per_m3']:.3f} × م3</p>

<p>أي نحو {m['per_km']:.2f} لتر لكل كيلومتر، ونحو {m['per_m3']:.2f} لتر لكل متر
مكعب منقول بصرف النظر عن المسافة — وهذا الثاني يمثّل دوران الحلة أثناء التحميل
والانتظار والتفريغ. المعادلة تفسّر {m['r2']*100:.1f}% من الفروق بمتوسط خطأ
{m['mape']:.1f}%. ثم يُقارن استهلاك كل خلاطة بالمتوقع لمسافتها وكميتها الفعليتين:
<b>الفارق الموجب</b> استهلاك لا تفسّره المسافة ولا الحمولة، و<b>السالب</b> كفاءة
أعلى من المتوقع.</p>

<p style="color:var(--slate);font-size:13px"><b>حدود هذا التقدير:</b> قُدِّر من
{len(e)} مشاهدة لشهر واحد فقط، والمسافة والكمية بينهما ارتباط ملموس، ما يجعل
المعاملين غير مستقرّين. تعامل معه كإشارة أولية لا كحكم، ولا تعتمد على فروق أصغر
من {m['mape']:.0f}% لأنها ضمن هامش الخطأ. عند توفّر بيانات ديزل لأشهر إضافية
سيُعاد التقدير على قاعدة أوسع ويصبح أوثق بكثير.</p>
</div>

{adj_tbl}
<div class="note">الصفوف المظللة تتجاوز 150 لتراً فوق المتوقع — مرشّحة للفحص
الفني. لاحظ أن الترتيب هنا يختلف عن ترتيب الكلفة لكل م3، وهذا مقصود: خلاطة قريبة
المسافة قد تبدو رخيصة بالمتر بينما استهلاكها فوق المتوقع لظروفها.</div>"""

    return f"""<div class="kpis">{head}</div>

<div class="finds" style="margin-top:16px">
<div class="find"><h3>الأعلى كلفة للمتر</h3>
<span class="big">{worst['jd_per_m3']:.2f} دينار/م3</span>
<p>{esc(worst['truck'])} — أعلى بـ {worst['gap_jd']:.2f} دينار عن متوسط الأسطول
({fleet_rate:.2f}). على إنتاجها البالغ {worst['total']:,.0f} م3 يعني ذلك
{worst['impact']:,.0f} دينار زيادة عمّا لو كانت بمستوى الأسطول.
تقطع {worst['km_per_move']:.1f} كم لكل حركة.</p></div>

<div class="find"><h3>الأدنى كلفة للمتر</h3>
<span class="big">{best['jd_per_m3']:.2f} دينار/م3</span>
<p>{esc(best['truck'])} — أقل بـ {abs(best['gap_jd']):.2f} دينار عن المتوسط،
أي وفّرت {abs(best['impact']):,.0f} دينار. تقطع {best['km_per_move']:.1f} كم
لكل حركة.</p></div>

<div class="find"><h3>الفجوة القابلة للإغلاق</h3>
<span class="big">{over:,.0f} دينار</span>
<p>مجموع ما كلّفته الخلاطات التي تجاوزت متوسط الأسطول، زيادةً عمّا لو عملت جميعها
بمعدّل {fleet_rate:.2f} دينار للمتر. هذا هو المبلغ المستهدف شهرياً، وليس كله قابلاً
للتوفير لأن جزءاً منه سببه بُعد المواقع لا الأداء.</p></div>
</div>

{cost_tbl}
<div class="note"><b>الأثر بالدينار</b> = فرق الخلاطة عن متوسط الأسطول مضروباً في
إنتاجها، أي ما كلّفته زيادةً أو وفّرته مقارنةً بأداء متوسط. الصفوف المظللة تتجاوز
المتوسط بأكثر من 0.40 دينار للمتر. <b>كم/حركة</b> مؤشر على بُعد المواقع التي
تخدمها — ارتفاعه يفسّر جزءاً من ارتفاع الكلفة.</div>

<div class="tbl" style="margin-top:18px"><caption>مقارنة الصانع</caption><table>
<tr><th>الصانع</th><th class="n">عدد</th><th class="n">م3</th>
<th class="n">م3/سيارة</th><th class="n">كم/م3</th><th class="n">كم/لتر</th>
<th class="n">م3/لتر</th><th class="n">دينار/م3</th></tr>{rows}</table></div>
<div class="note">عمود <b>كم/م3</b> يوضّح طبيعة المهام: ارتفاعه يعني مواقع أبعد
لنفس الكمية، وهو سبب مشروع لارتفاع الكلفة لا مؤشر ضعف. وانتبه أن فرق
<b>م3 لكل سيارة</b> يفسّر جزءاً من فرق الكلفة، لأن التي تنتج أقل توزّع استهلاكها
على أمتار أقل.</div>

{explain}"""


def build_reconciliation(rc):
    """جدول التسوية من إنتاج الحركة إلى صافي البيع"""
    if not rc["reliable"]:
        return ('<div class="pend">بيانات الكميات الراجعة لهذا الشهر غير موثوقة، '
                'فلا يمكن إجراء التسوية.</div>')

    rows = [
        ("الإنتاج المسجّل في نظام الحركة", rc["gross"], "", "base"),
        ("(−) إتلاف بسبب عطل في المصنع", -rc.get("loss_plant", 0),
         "صفوف بلا خلاطة ولا سائق ولا سند — الفاقد وقع في المصنع لا في النقل",
         "minus"),
        ("(−) إتلاف أثناء النقل", -rc.get("loss_transit", 0),
         "مسجّل على حركة حقيقية بخلاطة وسائق", "minus"),
        ("(−) راجع غير مطالب به", -rc["double"],
         "ازدواج تسجيل — رجعت وبيعت لعميل آخر فحُسبت مرتين", "minus"),
        ("(−) كميات محوّلة لعميل آخر", -rc["transferred"],
         "رفضها العميل الأول فسُجّلت مرة عنده ومرة عند من استلمها", "minus"),
        ("= صافي البيع", rc["net"], "الرقم المعتمد", "total"),
    ]
    if not rc["has_loss"]:
        rows = [r for r in rows if "إتلاف" not in r[0]]
        rows.insert(1, ("(−) إتلاف وفاقد", 0.0,
                        "عمود الإتلاف غير موجود في هذا الشهر", "minus"))

    body = ""
    for lbl, val, note, kind in rows:
        style = ""
        if kind == "total":
            style = ' style="font-weight:600;background:#F5F6F7"'
        elif kind == "minus":
            style = ' style="color:var(--alert)"'
        body += (f'<tr{style}><td>{esc(lbl)}</td>'
                 f'<td class="n">{val:,.1f}</td>'
                 f'<td style="font-size:12.5px;color:var(--slate)">{esc(note)}</td></tr>')

    return f"""<div class="tbl"><table>
<tr><th>البيان</th><th class="n">م3</th><th>ملاحظة</th></tr>{body}</table></div>

<div class="note">نسبة الإتلاف {rc['loss_pct']:.2f}% من إنتاج الحركة.
الكميات المحوّلة {rc['transferred']:,.1f}م3 تُخصم لأن البضاعة خرجت وسُجّلت للعميل
الأول ثم سُجّلت ثانيةً عند تحويلها، فتظهر مرتين في نظام الحركة رغم أنها بيعت
مرة واحدة. قبل خصمها كان الرقم {rc['net_before_transfer']:,.1f}م3.</div>"""
