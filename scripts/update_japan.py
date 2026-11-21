#!/usr/bin/env python3
"""日本経済ダッシュボード（japan.html）のデータを公開ソースから作り直す。

  python3 scripts/update_japan.py          # 取得して data/japan.json を更新

必要なもの:
  - e-Stat のアプリケーションID: 環境変数 ESTAT_APP_ID か ~/.config/estat/appid（値は表示しない）
  - Python パッケージ openpyxl（.xlsx 用）と xlrd（毎月勤労統計の .xls 用）

取得元:
  e-Stat API      消費者物価指数（2025年基準, statsDataId 0004052037）
  e-Stat ファイル 労働力調査 長期時系列 表1-a-1（季節調整値）/ 一般職業紹介状況 長期時系列 第3表（有効求人倍率）/
                  毎月勤労統計 長期時系列（現金給与総額・実質賃金）と最新の速報 指数要約表
                  ※ファイルは毎回 e-Stat の getDataCatalog で最新版の URL を探す
  内閣府          四半期別GDP速報 実質季節調整系列（CSV）
  日本銀行        時系列統計API（コールレート、ドル円、国内企業物価、短観、基準貸付利率）/
                  消費者物価のコア指標（cpirev.xlsx）/ 需給ギャップ（gap.xlsx）
  財務省          国債金利情報（jgbcm_all.csv・jgbcm.csv）
"""
import csv
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "japan.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JST = dt.timezone(dt.timedelta(hours=9))

CHART_FROM = "2022-01"      # 月次グラフの開始
DAILY_FROM = "2025-01-01"   # 日次グラフの開始
GDP_FROM = "2022-1"         # 四半期グラフの開始（年-四半期）。2020年の振れが大きいので外す

# 日銀の政策金利（無担保コールO/N物の誘導目標）。決定会合で変わったら手で更新する。
# 日銀APIの基準貸付利率（＝政策金利＋0.25%）と食い違えば警告を出す。
POLICY = {"rate": 1.25, "since": "2026-09-18", "prev": 1.00}

CPI_CATS = {
    "0001": "総合",
    "0161": "生鮮食品を除く総合",
    "0178": "生鮮食品及びエネルギーを除く総合",
    "0168": "食料（酒類を除く）及びエネルギーを除く総合",
    "0167": "エネルギー",
    "0172": "生鮮食品を除く食料",
    "0202": "財",
    "0220": "サービス",
    "0163": "持家の帰属家賃を除く総合",
    "0901": "総合（季節調整済）",
    "0902": "生鮮食品を除く総合（季節調整済）",
    "0906": "生鮮食品及びエネルギーを除く総合（季節調整済）",
}


# ---------- 共通 ----------
def get(url, binary=False):
    r = subprocess.run(["curl", "-sfL", "-m", "120", "-A", UA, url], capture_output=True)
    if r.returncode:
        # URL に appId が入ることがあるので、URL は表示しない
        host = urllib.parse.urlparse(url).netloc
        raise RuntimeError(f"取得に失敗しました（{host}, curl {r.returncode}）")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="ignore")


def num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").strip().strip("<>()（）")
    try:
        return float(s)
    except ValueError:
        return None


def zen2han(s):
    return str(s).translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def ym(y, m):
    return f"{int(y):04d}-{int(m):02d}"


def shift_ym(key, k):
    y, m = map(int, key.split("-"))
    t = y * 12 + (m - 1) + k
    return ym(t // 12, t % 12 + 1)


def yoy_from_index(series, lag=12):
    return {k: (v / series[shift_ym(k, -lag)] - 1) * 100
            for k, v in series.items() if shift_ym(k, -lag) in series and series[shift_ym(k, -lag)]}


def ann_from_index(series, months=3):
    return {k: ((v / series[shift_ym(k, -months)]) ** (12 / months) - 1) * 100
            for k, v in series.items() if shift_ym(k, -months) in series and series[shift_ym(k, -months)]}


def last(series):
    k = max(series)
    return k, series[k]


def r1(x, nd=1):
    return None if x is None else round(x, nd)


# ---------- e-Stat ----------
def estat_key():
    k = os.environ.get("ESTAT_APP_ID")
    if not k:
        p = Path.home() / ".config" / "estat" / "appid"
        if p.exists():
            k = p.read_text().strip()
    if not k:
        sys.exit("e-Stat のアプリケーションIDが見つかりません（ESTAT_APP_ID か ~/.config/estat/appid）")
    return k


def estat(api, **params):
    params["appId"] = estat_key()
    url = f"https://api.e-stat.go.jp/rest/3.0/app/json/{api}?" + urllib.parse.urlencode(params)
    return json.loads(get(url))


def estat_file(stats_code, pattern, search=None):
    """getDataCatalog で表題が pattern に合うファイルのうち最新のものを取得し、(bytes, 更新日, 表題) を返す。"""
    p = {"statsCode": stats_code}
    if search:
        p["searchWord"] = search
    d = estat("getDataCatalog", **p)["GET_DATA_CATALOG"]
    cats = d.get("DATA_CATALOG_LIST_INF", {}).get("DATA_CATALOG_INF", [])
    cats = cats if isinstance(cats, list) else [cats]
    hits = []
    for c in cats:
        rs = c["RESOURCES"]["RESOURCE"]
        for r in rs if isinstance(rs, list) else [rs]:
            t = r["TITLE"]
            name = t.get("NAME", "") if isinstance(t, dict) else str(t)
            if re.search(pattern, name):
                hits.append((r.get("LAST_MODIFIED_DATE", ""), name, r["URL"]))
    if not hits:
        raise RuntimeError(f"e-Stat のファイルが見つかりません: {pattern}")
    hits.sort(reverse=True)
    date, name, url = hits[0]
    return get(url, binary=True), date[:10], name


def fetch_cpi():
    d = estat("getStatsData", statsDataId="0004052037", cdArea="00000", cdTab="1,3",
              cdCat01=",".join(CPI_CATS), cdTimeFrom="2018000101")["GET_STATS_DATA"]
    if d["RESULT"]["STATUS"] != 0:
        raise RuntimeError("CPI: " + d["RESULT"]["ERROR_MSG"])
    out = {"index": {c: {} for c in CPI_CATS}, "yoy": {c: {} for c in CPI_CATS}}
    for v in d["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]:
        t = v["@time"]
        if t[4:6] != "00" or t[6:8] == "00" or t[6:8] != t[8:10]:
            continue  # 年・年度の値を除く
        x = num(v["$"])
        if x is None:
            continue
        out["index" if v["@tab"] == "1" else "yoy"][v["@cat01"]][ym(t[:4], t[6:8])] = x
    return out


def fetch_lfs():
    """労働力調査 表1-a-1 季節調整値。{系列: {YYYY-MM: 値}}"""
    import openpyxl
    raw, date, _ = estat_file("00200531", r"1-a-1_主要項目", "主要項目")
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        f.write(raw)
        f.flush()
        ws = openpyxl.load_workbook(f.name, read_only=True, data_only=True)["季節調整値"]
        rows = list(ws.iter_rows(values_only=True))
    cols = {"labour": 4, "employed": 7, "employee": 10, "unemployed": 13, "rate": 19}
    out = {k: {} for k in cols}
    year = None
    for i, r in enumerate(rows):
        m = re.match(r"(\d+)月", str(r[1] or ""))
        if not m:
            continue
        month = int(m.group(1))
        if month == 1:  # 1月の行は和暦、2月の行に西暦が入る
            nxt = rows[i + 1][0] if i + 1 < len(rows) else None
            year = int(nxt) if isinstance(nxt, (int, float)) else (year + 1 if year else None)
        if year is None:
            continue
        for k, c in cols.items():
            x = num(r[c])
            if x is not None:
                out[k][ym(year, month)] = x
    return out, date


def fetch_jobs():
    """有効求人倍率（パートタイムを含む一般, 季節調整値）"""
    import openpyxl
    raw, date, _ = estat_file("00450222", r"有効求人倍率（実数、季節調整値）", "有効求人倍率")
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        f.write(raw)
        f.flush()
        ws = openpyxl.load_workbook(f.name, read_only=True, data_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r[0] == "西暦")
    kind, head = rows[hi - 1], rows[hi]
    mcols = {}
    for j, h in enumerate(head):
        m = re.fullmatch(r"(\d+)月", zen2han(h or ""))
        if m and kind[j] == "季節調整値":
            mcols[int(m.group(1))] = j
    out = {}
    for r in rows[hi + 1:]:
        y = re.match(r"(\d{4})年", str(r[0] or ""))
        if not y:
            continue
        for mth, j in mcols.items():
            x = num(r[j])
            if x is not None:
                out[ym(y.group(1), mth)] = x
    return out, date


def _maikin_long(raw):
    """毎勤 長期時系列表（.xls, TLシート）の前年比ブロック。{YYYY-MM: 前年比}"""
    import xlrd
    sh = xlrd.open_workbook(file_contents=raw).sheet_by_name("TL")
    years = [(i, int(sh.cell_value(i, 0))) for i in range(sh.nrows) if isinstance(sh.cell_value(i, 0), float)]
    brk = next(k for k in range(1, len(years)) if years[k][1] < years[k - 1][1])
    out = {}
    for i, y in years[brk:]:
        for m in range(1, 13):
            x = num(sh.cell_value(i, 7 + m))
            if x is not None:
                out[ym(y, m)] = x
    return out


def _maikin_flash(raw):
    """速報 指数要約表（5人以上）から (YYYY-MM, 現金給与総額前年比, 実質賃金前年比)"""
    import xlrd
    sh = xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
    head = "".join(str(c.value) for c in sh.row(0))
    era = re.search(r"令\s*和\s*([\d.]+)\s*年\s*([０-９\d]+)\s*月", head.replace("　", ""))
    key = ym(2018 + int(float(era.group(1))), int(zen2han(era.group(2))))
    nominal = real = None
    in_real = False
    for i in range(sh.nrows):
        c0, c1 = str(sh.cell_value(i, 0)).replace("　", "").strip(), str(sh.cell_value(i, 1)).strip()
        if c0.startswith("実質賃金"):
            in_real = True
        if c0 == "TL" and c1 == "調査産業計":
            if in_real and real is None:
                real = num(sh.cell_value(i, 3))
            elif not in_real and nominal is None:
                nominal = num(sh.cell_value(i, 3))
    return key, nominal, real


def fetch_wages():
    nom_raw, nom_date, _ = estat_file("00450071", r"_現金給与総額　指数及び増減率－就業形態計（５人以上）$", "時系列")
    real_raw, real_date, _ = estat_file(
        "00450071", r"実質賃金（現金給与総額） 指数及び増減率－就業形態計（５人以上）.*持家の帰属家賃を除く総合", "時系列")
    nominal, real = _maikin_long(nom_raw), _maikin_long(real_raw)
    flash_raw, flash_date, flash_name = estat_file("00450071", r"指数要約表（令和.*速報）", "速報")
    fkey, fn, fr = _maikin_flash(flash_raw)
    flash = None
    if fn is not None and fkey not in nominal:
        nominal[fkey] = fn
        if fr is not None:
            real[fkey] = fr
        flash = fkey
    return {"nominal": nominal, "real": real, "flash": flash, "date": max(nom_date, flash_date)}


# ---------- 内閣府 GDP ----------
GDP_COLS = {
    "gdp": (1, "実質GDP"), "pc": (2, "民間最終消費支出"), "res": (5, "民間住宅"), "capex": (6, "民間企業設備"),
    "gov": (8, "政府最終消費支出"), "pubinv": (9, "公的固定資本形成"), "ex": (12, "輸出"), "im": (13, "輸入"),
    "dd": (23, "国内需要"), "pd": (24, "民間需要"),
}


def fetch_gdp():
    base = "https://www.esri.cao.go.jp"
    pat = r'href="(/jp/sna/data/data_list/sokuhou/files/\d{4}/(qe\d+)/)gdemenuja\.html"'
    path = re.search(pat, get(base + "/jp/sna/sokuhou/sokuhou_top.html"))
    if not path:  # トップに無ければ今年の一覧ページの先頭を使う
        year = dt.datetime.now(JST).year
        path = re.search(pat, get(f"{base}/jp/sna/data/data_list/sokuhou/files/{year}/toukei_{year}.html"))
    folder, qe = path.group(1), path.group(2)
    page = get(base + folder + "gdemenuja.html")
    title = re.search(r"<title>統計表一覧（(.*?)）", page)
    fname = re.search(r'href="tables/(gaku-jk\d+\.csv)"', page).group(1)
    text = get(base + folder + "tables/" + fname, binary=True).decode("cp932", errors="ignore")
    out = {k: {} for k in GDP_COLS}
    year = None
    for r in csv.reader(io.StringIO(text)):
        if not r:
            continue
        m = re.match(r"\s*(?:(\d{4})/)?\s*(\d+)-\s*(\d+)\.", r[0])
        if not m:
            continue
        if m.group(1):
            year = int(m.group(1))
        q = (int(m.group(2)) - 1) // 3 + 1
        for k, (c, _) in GDP_COLS.items():
            x = num(r[c]) if c < len(r) else None
            if x is not None:
                out[k][f"{year}-{q}"] = x
    return out, (title.group(1) if title else qe)


def qshift(key, k):
    y, q = map(int, key.split("-"))
    t = y * 4 + (q - 1) + k
    return f"{t // 4}-{t % 4 + 1}"


def qlabel(key):
    y, q = key.split("-")
    return f"{y}年{['1-3', '4-6', '7-9', '10-12'][int(q) - 1]}月期"


# ---------- 日本銀行 ----------
def boj(db, codes, start):
    url = ("https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=jp"
           f"&db={db}&code={','.join(codes)}&startDate={start}")
    d = json.loads(get(url))
    if d.get("STATUS") != 200:
        raise RuntimeError(f"日銀API {db}: {d.get('MESSAGE')}")
    out = {}
    for r in d["RESULTSET"]:
        v = r["VALUES"]
        out[r["SERIES_CODE"]] = {str(t): x for t, x in zip(v["SURVEY_DATES"], v["VALUES"]) if x is not None}
    return out


def boj_daily(s):
    return {f"{k[:4]}-{k[4:6]}-{k[6:8]}": v for k, v in s.items()}


def boj_monthly(s):
    return {f"{k[:4]}-{k[4:6]}": v for k, v in s.items()}


def boj_quarterly(s):
    return {f"{k[:4]}-{int(k[4:6])}": v for k, v in s.items()}


def fetch_boj_core():
    """消費者物価のコア指標（特殊要因除く・刈込平均など）。各指標は新しい基準年の値を優先してつなぐ。"""
    import openpyxl
    raw = get("https://www.boj.or.jp/research/research_data/cpi/cpirev.xlsx", binary=True)
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        f.write(raw)
        f.flush()
        rows = list(openpyxl.load_workbook(f.name, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    want = {"exff": "除く生鮮食品、特殊要因", "exffe": "除く生鮮食品・エネルギー、特殊要因",
            "trim": "刈込平均値", "median": "加重中央値", "mode": "最頻値"}
    groups = {k: [j for j, h in enumerate(rows[1]) if h and str(h).startswith(lbl + "（")] for k, lbl in want.items()}
    out = {k: {} for k in want}
    for r in rows[5:]:
        if not isinstance(r[0], dt.datetime):
            continue
        for k, cols in groups.items():
            x = next((r[j] for j in cols if isinstance(r[j], (int, float))), None)
            if x is not None:
                out[k][ym(r[0].year, r[0].month)] = float(x)
    return out


def fetch_gap():
    import openpyxl
    raw = get("https://www.boj.or.jp/research/research_data/gap/gap.xlsx", binary=True)
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
        f.write(raw)
        f.flush()
        ws = openpyxl.load_workbook(f.name, read_only=True, data_only=True)["data1"]
        out = {}
        for r in ws.iter_rows(values_only=True):
            m = re.fullmatch(r"(\d{4})\.(\d)Q", str(r[0] or ""))
            if m and isinstance(r[1], (int, float)):
                out[f"{m.group(1)}-{m.group(2)}"] = float(r[1])
    return out


# ---------- 財務省 国債金利 ----------
def wareki(s):
    m = re.fullmatch(r"([SHR])(\d+)\.(\d+)\.(\d+)", s.strip())
    if not m:
        return None
    y = int(m.group(2)) + {"S": 1925, "H": 1988, "R": 2018}[m.group(1)]
    return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def fetch_jgb():
    tenors = {"2Y": 2, "5Y": 5, "10Y": 10, "20Y": 12, "30Y": 14}
    out = {k: {} for k in tenors}
    for url in ("https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv",
                "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"):
        text = get(url, binary=True).decode("cp932", errors="ignore")
        for r in csv.reader(io.StringIO(text)):
            d = wareki(r[0]) if r else None
            if not d or d < "2024-01-01":
                continue
            for k, c in tenors.items():
                x = num(r[c])
                if x is not None:
                    out[k][d] = x
    return out


# ---------- 組み立て ----------
def fmt(x, nd=1, sign=False):
    if x is None:
        return "—"
    if round(x, nd) == 0:
        x = 0.0  # 「−0」「+0」を避ける
        return f"{x:.{nd}f}" if not sign else f"±{x:.{nd}f}"
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace("-", "−")


def mlabel(key):
    y, m = key.split("-")
    return f"{y}年{int(m)}月"


def dlabel(key):
    y, m, d = key.split("-")
    return f"{int(m)}/{int(d)}"


def value_at_or_before(series, key):
    ks = [k for k in series if k <= key]
    return series[max(ks)] if ks else None


def build():
    print("CPI（e-Stat API）…")
    cpi = fetch_cpi()
    print("日銀 コア指標・需給ギャップ…")
    core = fetch_boj_core()
    gap = fetch_gap()
    print("労働力調査・有効求人倍率・毎月勤労統計（e-Stat ファイル）…")
    lfs, lfs_date = fetch_lfs()
    jobs, jobs_date = fetch_jobs()
    wages = fetch_wages()
    print("GDP（内閣府）…")
    gdp, gdp_title = fetch_gdp()
    print("国債金利（財務省）・日銀API…")
    jgb = fetch_jgb()
    today = dt.datetime.now(JST).date()
    bj = boj("FM01", ["STRDCLUCON"], "202401")
    bj.update(boj("FM08", ["FXERD04"], "202401"))
    bj.update(boj("PR01", ["PRCG20_2200000000"], "202301"))
    bj.update(boj("CO", ["TK99F1000601GCQ01000", "TK99F2000601GCQ01000"], "201901"))
    bj.update(boj("IR01", ["MADR1Z@D"], f"{today.year}01"))
    call = boj_daily(bj["STRDCLUCON"])
    usdjpy = boj_daily(bj["FXERD04"])
    cgpi_yoy = yoy_from_index(boj_monthly(bj["PRCG20_2200000000"]))
    tankan_m = boj_quarterly(bj["TK99F1000601GCQ01000"])
    tankan_n = boj_quarterly(bj["TK99F2000601GCQ01000"])
    base_rate = boj_daily(bj["MADR1Z@D"])
    _, br = last(base_rate)
    if abs(br - 0.25 - POLICY["rate"]) > 1e-9:
        print(f"警告: 基準貸付利率 {br}% − 0.25 が POLICY の {POLICY['rate']}% と一致しません。政策金利の変更を確認してください。")

    # --- 物価 ---
    yo = cpi["yoy"]
    sa3 = {c: ann_from_index(cpi["index"][c]) for c in ("0901", "0902", "0906")}
    cpi_m, _ = last(yo["0161"])
    core_m = max(core["trim"])

    # --- 実質GDP ---
    def qq_ann(s, k):
        p = s.get(qshift(k, -1))
        return ((s[k] / p) ** 4 - 1) * 100 if p else None

    def q_yoy(s, k):
        p = s.get(qshift(k, -4))
        return (s[k] / p - 1) * 100 if p else None

    gq = max(gdp["gdp"], key=lambda k: tuple(map(int, k.split("-"))))
    gdp_ann = qq_ann(gdp["gdp"], gq)
    gap_q = max(gap, key=lambda k: tuple(map(int, k.split("-"))))

    # --- 雇用・賃金 ---
    lm, urate = last(lfs["rate"])
    jm, jratio = last(jobs)
    wm, wreal = last(wages["real"])
    wnom = wages["nominal"][wm]
    real6 = [wages["real"].get(shift_ym(wm, -i)) for i in range(6)]
    real6 = sum(real6) / 6 if None not in real6 else None

    # --- 金利・為替 ---
    jd = max(jgb["10Y"])
    ystart = f"{jd[:4]}-01-01"
    prev_year_end = max(k for k in jgb["10Y"] if k < ystart)
    j10, j2 = jgb["10Y"][jd], jgb["2Y"][jd]
    d10 = (j10 - jgb["10Y"][prev_year_end]) * 100
    d2 = (j2 - jgb["2Y"][prev_year_end]) * 100
    slope_now = (j10 - j2) * 100
    slope_prev = (jgb["10Y"][prev_year_end] - jgb["2Y"][prev_year_end]) * 100
    fd, fx = last(usdjpy)
    fx_prev = value_at_or_before(usdjpy, prev_year_end)
    tq = max(tankan_m, key=lambda k: tuple(map(int, k.split("-"))))

    def qname(k):
        y, q = k.split("-")
        return f"{y}年{['3', '6', '9', '12'][int(q) - 1]}月調査"

    cards = [
        {"label": "実質GDP", "value": fmt(gdp_ann, 1, True), "unit": "%", "sub": "前期比年率", "date": qlabel(gq)},
        {"label": "完全失業率", "value": fmt(urate), "unit": "%", "sub": "季節調整値", "date": mlabel(lm)},
        {"label": "有効求人倍率", "value": fmt(jratio, 2), "unit": "倍", "sub": "季節調整値", "date": mlabel(jm)},
        {"label": "CPI（生鮮食品を除く）", "value": fmt(yo["0161"][cpi_m], 1, True), "unit": "%", "sub": "前年同月比", "date": mlabel(cpi_m)},
        {"label": "CPI（生鮮食品・エネルギーを除く）", "value": fmt(yo["0178"][cpi_m], 1, True), "unit": "%", "sub": "前年同月比", "date": mlabel(cpi_m)},
        {"label": "実質賃金", "value": fmt(wreal, 1, True), "unit": "%", "sub": "前年同月比" + ("（速報）" if wm == wages["flash"] else ""), "date": mlabel(wm)},
        {"label": "政策金利", "value": f"{POLICY['rate']:.2f}", "unit": "%", "sub": "無担保コールO/N 誘導目標", "date": f"{int(POLICY['since'][5:7])}月{int(POLICY['since'][8:])}日〜"},
        {"label": "10年国債利回り", "value": fmt(j10, 3), "unit": "%", "sub": f"年初来 {fmt(d10, 0, True)}bp", "date": dlabel(jd)},
        {"label": "ドル円", "value": fmt(fx, 2), "unit": "円", "sub": f"年初来 {fmt((fx / fx_prev - 1) * 100, 1, True)}%（円安が＋）" if fx_prev else "", "date": dlabel(fd)},
        {"label": "日銀短観 大企業製造業", "value": fmt(tankan_m[tq], 0, True), "unit": "", "sub": "業況判断DI", "date": qname(tq)},
    ]

    questions = [
        {"id": "q-inflation", "q": "物価上昇は2%で落ち着くか",
         "rows": [
             ["生鮮食品を除く総合（前年同月比）", fmt(yo["0161"][cpi_m], 1, True) + "%", mlabel(cpi_m)],
             ["制度要因を除いた場合（日銀試算）", fmt(core["exff"].get(core_m), 1, True) + "%", mlabel(core_m)],
             ["生鮮食品・エネルギーを除く（3か月前比年率）", fmt(sa3["0906"].get(cpi_m), 1, True) + "%", mlabel(cpi_m)],
             ["刈込平均値（日銀）", fmt(core["trim"].get(core_m), 1, True) + "%", mlabel(core_m)],
         ],
         "note": "日銀の目標は総合CPIで前年比2%。ガソリン補助金や授業料の無償化などの制度要因は表面の上昇率を動かすため、日銀は制度要因を除いた試算も公表している。3か月前比年率は季節調整済み指数から計算（指数が小数第1位までなので誤差は大きめ）。",
         "link": "#t-prices"},
        {"id": "q-wages", "q": "賃金は物価に勝っているか",
         "rows": [
             ["名目賃金（現金給与総額, 前年同月比）", fmt(wnom, 1, True) + "%", mlabel(wm)],
             ["実質賃金（前年同月比）", fmt(wreal, 1, True) + "%", mlabel(wm)],
             ["実質賃金（直近6か月平均）", fmt(real6, 1, True) + "%", f"{mlabel(shift_ym(wm, -5))}〜"],
         ],
         "note": "毎月勤労統計（事業所規模5人以上）。実質賃金は名目賃金を消費者物価（持家の帰属家賃を除く総合）で割ったもの。ボーナス月（6〜7月, 12月）は振れが大きい。",
         "link": "#t-labor"},
        {"id": "q-demand", "q": "景気は強いか弱いか",
         "rows": [
             ["実質GDP（前期比年率）", fmt(gdp_ann, 1, True) + "%", qlabel(gq)],
             ["実質GDP（前年同期比）", fmt(q_yoy(gdp["gdp"], gq), 1, True) + "%", qlabel(gq)],
             ["民間需要（前年同期比）", fmt(q_yoy(gdp["pd"], gq), 1, True) + "%", qlabel(gq)],
             ["需給ギャップ（日銀推計）", fmt(gap[gap_q], 1, True) + "%", qlabel(gap_q)],
         ],
         "note": "民間需要は家計消費・住宅・設備投資・在庫の合計で、政府と海外の影響を除いた国内の基調を見る目安。需給ギャップはプラスなら需要超過（物価の押し上げ要因）。",
         "link": "#t-gdp"},
        {"id": "q-rates", "q": "長期金利はなぜ上がったか",
         "rows": [
             [f"10年国債利回り（{int(prev_year_end[:4])}年末から）", fmt(d10, 0, True) + "bp", dlabel(jd)],
             ["うち 2年国債（政策金利の見通し）", fmt(d2, 0, True) + "bp", dlabel(jd)],
             ["うち 10年−2年の差（期間プレミアムなど）", fmt(slope_now - slope_prev, 0, True) + "bp", dlabel(jd)],
         ],
         "note": "2年国債は今後2年間の政策金利の予想をおおむね反映する。10年との差の変化には、インフレ見通し、財政への懸念、日銀の国債買入れ減額などによる上乗せ分（期間プレミアム）が含まれる。物価連動国債の市場が小さいため、米国のように予想インフレ率と実質金利に分けるのは難しい。",
         "link": "#t-markets"},
    ]

    # --- 表 ---
    def mrow(label, s, nd=1, sign=True, unit="", note=None):
        k, v = last(s)
        return [label, fmt(v, nd, sign) + unit, fmt(s.get(shift_ym(k, -1)), nd, sign) + unit,
                fmt(s.get(shift_ym(k, -12)), nd, sign) + unit, mlabel(k)]

    t_prices = {
        "id": "t-prices", "title": "物価", "columns": ["", "最新", "前月", "1年前", "時点"],
        "rows": [mrow(f"CPI {CPI_CATS[c]}", yo[c]) for c in ("0001", "0161", "0178", "0168", "0172", "0167", "0202", "0220")]
        + [mrow("CPI 生鮮食品・エネルギーを除く（3か月前比年率）", sa3["0906"]),
           mrow("制度要因を除く 生鮮食品を除く総合（日銀）", core["exff"]),
           mrow("制度要因を除く 生鮮食品・エネルギーを除く（日銀）", core["exffe"]),
           mrow("刈込平均値（日銀）", core["trim"]),
           mrow("加重中央値（日銀）", core["median"]),
           mrow("最頻値（日銀）", core["mode"]),
           mrow("国内企業物価指数 総平均", cgpi_yoy)],
        "note": "CPIは全国・2025年基準、前年同月比（%）。日銀の指標は特殊要因（制度変更）を除いた試算と、品目の上昇率の分布から求める指標。国内企業物価は企業間で取引される財の価格。",
    }
    lfs_rows = [
        mrow("完全失業率", lfs["rate"], 1, False, "%"),
        mrow("有効求人倍率", jobs, 2, False, "倍"),
    ]
    for key, label in (("employed", "就業者数"), ("employee", "雇用者数")):
        k, v = last(lfs[key])
        lfs_rows.append([label + "（万人）", fmt(v, 0), fmt(lfs[key].get(shift_ym(k, -1)), 0),
                         fmt(lfs[key].get(shift_ym(k, -12)), 0), mlabel(k)])
    lfs_rows += [mrow("名目賃金（現金給与総額, 前年同月比）", wages["nominal"], 1, True, "%"),
                 mrow("実質賃金（前年同月比）", wages["real"], 1, True, "%")]
    t_labor = {
        "id": "t-labor", "title": "雇用と賃金", "columns": ["", "最新", "前月", "1年前", "時点"], "rows": lfs_rows,
        "note": "失業率・就業者数・雇用者数は労働力調査、有効求人倍率は一般職業紹介状況（パートタイムを含む）で、いずれも季節調整値。賃金は毎月勤労統計（5人以上, 前年同月比）"
                + (f"で、{mlabel(wages['flash'])}は速報値。" if wages["flash"] else "。"),
    }
    gdp_rows = []
    for k, (_, label) in GDP_COLS.items():
        s = gdp[k]
        gdp_rows.append([label, fmt(qq_ann(s, gq), 1, True), fmt(qq_ann(s, qshift(gq, -1)), 1, True), fmt(q_yoy(s, gq), 1, True)])
    t_gdp = {
        "id": "t-gdp", "title": f"実質GDPと需要項目（{gdp_title}）",
        "columns": ["", f"{qlabel(gq)}（前期比年率）", f"{qlabel(qshift(gq, -1))}（前期比年率）", "前年同期比"],
        "rows": gdp_rows,
        "note": "内閣府「四半期別GDP速報」の実質季節調整系列（2020暦年連鎖価格）から計算（%）。前期比年率は前期比を4乗したもの。",
    }

    def drow(label, s, nd, unit="%", bp=True):
        k, v = last(s)
        wk = value_at_or_before(s, (dt.date.fromisoformat(k) - dt.timedelta(days=7)).isoformat())
        ye = value_at_or_before(s, prev_year_end)
        if bp:
            return [label, fmt(v, nd) + unit, fmt((v - wk) * 100 if wk is not None else None, 0, True) + "bp",
                    fmt((v - ye) * 100 if ye is not None else None, 0, True) + "bp", dlabel(k)]
        return [label, fmt(v, nd) + unit, fmt((v / wk - 1) * 100 if wk else None, 1, True) + "%",
                fmt((v / ye - 1) * 100 if ye else None, 1, True) + "%", dlabel(k)]

    t_markets = {
        "id": "t-markets", "title": "金利と為替", "columns": ["", "最新", "1週間の変化", "年初来", "時点"],
        "rows": [drow("無担保コールO/N物（平均）", call, 3)]
        + [drow(f"国債 {t.replace('Y', '年')}", jgb[t], 3) for t in ("2Y", "5Y", "10Y", "20Y", "30Y")]
        + [drow("ドル円（17時）", usdjpy, 2, "円", bp=False),
           ["日銀短観 業況判断DI 大企業製造業", fmt(tankan_m[tq], 0, True), "", f"前回 {fmt(tankan_m.get(qshift(tq, -1)), 0, True)}", qname(tq)],
           ["日銀短観 業況判断DI 大企業非製造業", fmt(tankan_n.get(tq), 0, True), "", f"前回 {fmt(tankan_n.get(qshift(tq, -1)), 0, True)}", qname(tq)]],
        "note": f"国債は財務省「国債金利情報」、コールレート・ドル円・短観は日本銀行。年初来は{int(prev_year_end[:4])}年最終営業日との比較。ドル円の＋は円安。",
    }

    # --- グラフ ---
    def mseries(s, start=CHART_FROM):
        return {k: r1(v, 2) for k, v in s.items() if k >= start}

    def to_chart(labels, *series):
        return [[s.get(k) for k in labels] for s in series]

    m_labels = sorted({k for s in (yo["0161"], core["trim"]) for k in s if k >= CHART_FROM})
    c_cpi = {"id": "c-cpi", "title": "消費者物価（前年同月比, %）", "unit": "%", "labels": m_labels,
             "note": "2%は日銀の物価目標。制度要因を除く系列と刈込平均値は日本銀行の試算。",
             "target": 2,
             "series": [{"name": n, "data": d} for n, d in zip(
                 ["生鮮食品を除く総合", "生鮮食品・エネルギーを除く", "制度要因を除く（生鮮食品を除く総合）", "刈込平均値"],
                 to_chart(m_labels, mseries(yo["0161"]), mseries(yo["0178"]), mseries(core["exff"]), mseries(core["trim"])))]}
    w_labels = sorted(k for k in wages["nominal"] if k >= CHART_FROM)
    c_wage = {"id": "c-wage", "title": "賃金（前年同月比, %）", "unit": "%", "labels": w_labels,
              "note": "毎月勤労統計、事業所規模5人以上。実質賃金がプラスなら、賃金の伸びが物価を上回っている。",
              "zero": True,
              "series": [{"name": n, "data": d} for n, d in zip(
                  ["名目賃金（現金給与総額）", "実質賃金"], to_chart(w_labels, mseries(wages["nominal"]), mseries(wages["real"])))]}
    l_labels = sorted(k for k in lfs["rate"] if k >= "2019-01")
    c_labor = {"id": "c-labor", "title": "失業率と有効求人倍率（季節調整値）", "unit": "", "labels": l_labels,
               "note": "左軸が完全失業率（%）、右軸が有効求人倍率（倍）。",
               "series": [{"name": "完全失業率（%, 左軸）", "data": to_chart(l_labels, mseries(lfs["rate"], "2019-01"))[0], "axis": "y"},
                          {"name": "有効求人倍率（倍, 右軸）", "data": to_chart(l_labels, mseries(jobs, "2019-01"))[0], "axis": "y2"}]}
    q_keys = sorted((k for k in gdp["gdp"] if tuple(map(int, k.split("-"))) >= tuple(map(int, GDP_FROM.split("-")))),
                    key=lambda k: tuple(map(int, k.split("-"))))
    c_gdp = {"id": "c-gdp", "title": "実質GDP成長率（%）", "unit": "%", "labels": [qlabel(k).replace("月期", "") for k in q_keys],
             "note": "棒は前期比年率、線は前年同期比。",
             "zero": True,
             "series": [{"name": "前期比年率", "type": "bar", "data": [r1(qq_ann(gdp["gdp"], k), 2) for k in q_keys]},
                        {"name": "前年同期比", "data": [r1(q_yoy(gdp["gdp"], k), 2) for k in q_keys]}]}
    d_labels = sorted(k for k in jgb["10Y"] if k >= DAILY_FROM)
    c_jgb = {"id": "c-jgb", "title": "国債利回り（%）", "unit": "%", "labels": d_labels, "daily": True,
             "note": "財務省「国債金利情報」。破線は日銀の政策金利（誘導目標）ではなく、無担保コールO/N物の実際の平均値。",
             "series": [{"name": f"{t.replace('Y', '年')}", "data": [jgb[t].get(k) for k in d_labels]} for t in ("2Y", "10Y", "30Y")]
             + [{"name": "無担保コールO/N物", "data": [call.get(k) for k in d_labels], "dash": True}]}

    return {
        "lastUpdated": today.isoformat(),
        "policy": POLICY,
        "cards": cards,
        "questions": questions,
        "charts": [c_cpi, c_wage, c_labor, c_gdp, c_jgb],
        "tables": [t_prices, t_labor, t_gdp, t_markets],
        "sourceDates": {
            "CPI": mlabel(cpi_m), "日銀コア指標": mlabel(core_m), "労働力調査": mlabel(lm), "有効求人倍率": mlabel(jm),
            "毎月勤労統計": mlabel(wm), "GDP": gdp_title, "需給ギャップ": qlabel(gap_q), "国債": jd, "ドル円": fd,
        },
        "checks": {
            "cpi_exrent_yoy": yo["0163"].get(wm),
            "implied_deflator": r1(((1 + wnom / 100) / (1 + wreal / 100) - 1) * 100, 2),
        },
    }


def main():
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    print(f"書き出し: {OUT.relative_to(ROOT)}")
    for c in data["cards"]:
        print(f"  {c['label']}: {c['value']}{c['unit']}（{c['sub']}, {c['date']}）")
    ck = data["checks"]
    print(f"  確認: CPI持家の帰属家賃を除く総合 {ck['cpi_exrent_yoy']}% / 賃金から逆算した物価 {ck['implied_deflator']}%")


if __name__ == "__main__":
    main()
