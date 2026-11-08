#!/usr/bin/env python3
"""市場データ（PER・益回り・10年国債利回り・イールドスプレッド）を公開ソースから再構築する。

  python3 scripts/update_market.py                 # 取得して data/market.json と data/market_log.csv を更新
  python3 scripts/update_market.py --euro-pe 18.21 --euro-pe-date 2026-11-03
                                                   # EXW1.DE の PER（Yahoo Finance）を基準値に追加してから更新

指数ごとの算出方法:
  日経平均     nikkei225jp.com daily2.json の加重平均PER と日本国債10年利回り（いずれも日次）
  S&P500       multpl.com の月次表から月ごとの1株利益（=月次株価÷PER）を求め、CNBC の日次終値を割る
  ダウ平均     worldperatio.com の月次PER（DIA ETF、月末値）から月ごとの1株利益を求め、Nasdaq の DIA 日次終値を割る
  ユーロ50     EXW1.DE の PER 観測値（data/market_anchors.json）から1株利益を求めて日付で線形補間し、
               CNBC の ^STOXX50E 日次終値を割る
  国債         米国: 米財務省 Daily Treasury Par Yield Curve の10年 / ドイツ: 連邦銀行 BBSIS の残存10年 /
               日本: daily2.json
"""
import argparse
import csv
import datetime as dt
import html
import io
import json
import re
import subprocess
import sys
from bisect import bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "data" / "market.json"
OUT_CSV = ROOT / "data" / "market_log.csv"
ANCHORS = ROOT / "data" / "market_anchors.json"

START = "2025-01-01"
EURO_START = "2026-08-25"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
JST = dt.timezone(dt.timedelta(hours=9))


def get(url, headers=None):
    # python.org 版 Python は証明書ストアを持たないことがあるため curl で取得する
    cmd = ["curl", "-sfL", "-m", "60", "-A", UA, url]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    return subprocess.run(cmd, check=True, capture_output=True).stdout.decode("utf-8", errors="ignore")


def page_text(raw):
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


# ---------- 取得 ----------
def fetch_nikkei():
    """{date: (per, jgb10)}。daily2.json の 12列目=加重平均PER、16列目=10年国債利回り。"""
    raw = get("https://nikkei225jp.com/_data/_nfsDATA/data_DAY/daily2.json",
              {"Referer": "https://nikkei225jp.com/data/per.php"})
    out = {}
    for line in re.findall(r"^\[(.*)\],?$", raw, flags=re.M):
        f = line.split(",")
        d = dt.datetime.fromtimestamp(int(f[0]) / 1000, JST).date().isoformat()
        per, jgb = num(f[12].strip('"')), num(f[16].strip('"'))
        if d >= START and per and jgb:
            out[d] = (per, jgb)
    return out


def fetch_cnbc(symbol, start):
    url = (f"https://ts-api.cnbc.com/harmony/app/bars/{symbol}/1D/"
           f"{start.replace('-', '')}000000/{dt.date.today():%Y%m%d}235959/adjusted/EST5EDT.json")
    bars = json.loads(get(url))["barData"]["priceBars"]
    return {f"{b['tradeTime'][:4]}-{b['tradeTime'][4:6]}-{b['tradeTime'][6:8]}": float(b["close"]) for b in bars}


def fetch_dia(start):
    url = (f"https://api.nasdaq.com/api/quote/DIA/historical?assetclass=etf"
           f"&fromdate={start}&todate={dt.date.today()}&limit=9999")
    rows = json.loads(get(url, {"Accept": "application/json"}))["data"]["tradesTable"]["rows"]
    out = {}
    for r in rows:
        m, d, y = r["date"].split("/")
        out[f"{y}-{m}-{d}"] = num(r["close"])
    return out


def fetch_treasury():
    out = {}
    for y in range(int(START[:4]), dt.date.today().year + 1):
        raw = get("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                  f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
                  f"&field_tdr_date_value={y}&page&_format=csv")
        for r in csv.DictReader(io.StringIO(raw)):
            m, d, yy = r["Date"].split("/")
            if num(r.get("10 Yr")) is not None:
                out[f"{yy}-{m}-{d}"] = num(r["10 Yr"])
    return out


def fetch_bund(start):
    raw = get("https://api.statistiken.bundesbank.de/rest/data/BBSIS/"
              f"D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?startPeriod={start}&format=csv",
              {"Accept": "text/csv"})
    out = {}
    for m in re.finditer(r"^(\d{4}-\d{2}-\d{2});(-?\d+,\d+);", raw, flags=re.M):
        out[m.group(1)] = float(m.group(2).replace(",", "."))
    return out


def fetch_multpl_eps():
    """{YYYY-MM: EPS}。multpl.com の月次表（月初行）の 株価÷PER。"""
    def table(path):
        t = page_text(get(f"https://www.multpl.com/{path}/table/by-month"))
        rows = {}
        for d, v in re.findall(r"([A-Z][a-z]{2} \d{1,2}, \d{4}) †? ?([\d,]+\.\d+)", t):
            day = dt.datetime.strptime(d, "%b %d, %Y").date()
            if day.day == 1:
                rows[day.isoformat()[:7]] = num(v)
        return rows
    pe, px = table("s-p-500-pe-ratio"), table("s-p-500-historical-prices")
    return {m: px[m] / pe[m] for m in pe if m in px and pe[m]}


def fetch_worldperatio():
    """({YYYY-MM: 月末PER}, (最新PER, 最新日付))。"""
    raw = get("https://worldperatio.com/index/dow-jones/")
    # 月次PERの配列（detailPE_data = [[Date.UTC(y, m0, 1),値],...,];）は1行に収まっている
    block = re.search(r"detailPE_data = \[.*", raw).group(0)
    monthly = {}
    for y, mo, _d, v in re.findall(r"Date\.UTC\((\d+),\s*(\d+),\s*(\d+)\)\s*,\s*([\d.]+)", block):
        monthly[f"{int(y):04d}-{int(mo) + 1:02d}"] = float(v)
    t = page_text(raw)
    m = re.search(r"Ratio for Dow Jones Index is ([\d.]+) , calculated on (\d{2} \w+ \d{4})", t)
    latest = (float(m.group(1)), dt.datetime.strptime(m.group(2), "%d %B %Y").date().isoformat()) if m else None
    return monthly, latest


# ---------- 算出 ----------
def row(date, idx, per, bond, method, bond_src):
    ey = 100 / per
    return {"date": date, "index": idx, "per": round(per, 2), "ey_pct": round(ey, 2),
            "bond_yield_pct": round(bond, 3), "ys_pct": round(ey - bond, 2),
            "per_method": method, "bond_source": bond_src}


def build_sp500(closes, eps_by_month, ust):
    months = sorted(eps_by_month)
    rows = []
    for d, px in sorted(closes.items()):
        if d < START or d not in ust:
            continue
        m = d[:7] if d[:7] in eps_by_month else months[bisect_right(months, d[:7]) - 1]
        rows.append(row(d, "SP500", px / eps_by_month[m], ust[d],
                        f"CNBC終値÷multpl月次EPS({m})", "米財務省10年"))
    return rows


def build_dow(dia, wpr_monthly, wpr_latest, ust):
    days = sorted(dia)
    eps = {}
    for m, pe in wpr_monthly.items():
        if m < "2024-12":
            continue
        # 月次値は月末時点。当月分は worldperatio の最新算出日の値を使う
        if wpr_latest and m == wpr_latest[1][:7]:
            ref, pe = wpr_latest[1], wpr_latest[0]
        else:
            ref = f"{m}-31"
        i = bisect_right(days, ref) - 1
        if i >= 0 and days[i][:7] == m:
            eps[m] = dia[days[i]] / pe
    months = sorted(eps)
    rows = []
    for d in days:
        if d < START or d not in ust:
            continue
        m = d[:7] if d[:7] in eps else months[bisect_right(months, d[:7]) - 1]
        rows.append(row(d, "DowJones", dia[d] / eps[m], ust[d],
                        f"DIA終値÷worldperatio月次EPS({m})", "米財務省10年"))
    return rows


def build_euro(stoxx, anchors, bund):
    pts = sorted((a["date"], a["index_close"] / a["pe"]) for a in anchors)
    def eps_at(d):
        if d <= pts[0][0]:
            return pts[0][1]
        if d >= pts[-1][0]:
            return pts[-1][1]
        i = bisect_right([p[0] for p in pts], d)
        (d0, e0), (d1, e1) = pts[i - 1], pts[i]
        f = (dt.date.fromisoformat(d) - dt.date.fromisoformat(d0)).days / \
            (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days
        return e0 + (e1 - e0) * f
    rows = []
    for d, px in sorted(stoxx.items()):
        if d < EURO_START or d not in bund:
            continue
        rows.append(row(d, "EuroStoxx50", px / eps_at(d), bund[d],
                        "CNBC終値÷EXW1.DE基準EPS(補間)", "独連邦銀行10年"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--euro-pe", type=float, help="EXW1.DE の PER (TTM)")
    ap.add_argument("--euro-pe-date", help="その PER の基準日 (YYYY-MM-DD)")
    args = ap.parse_args()

    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    stoxx = fetch_cnbc(".STOXX50E", "2026-08-01")
    if args.euro_pe:
        d = args.euro_pe_date
        if d not in stoxx:
            sys.exit(f"{d} の ^STOXX50E 終値がありません")
        anchors["euro"] = [a for a in anchors["euro"] if a["date"] != d] + [
            {"date": d, "pe": args.euro_pe, "index_close": stoxx[d], "source": "Yahoo Finance EXW1.DE PE Ratio (TTM)"}]
        anchors["euro"].sort(key=lambda a: a["date"])
        ANCHORS.write_text(json.dumps(anchors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ust = fetch_treasury()
    nk = fetch_nikkei()
    wpr_monthly, wpr_latest = fetch_worldperatio()
    rows = [row(d, "Nikkei225", per, jgb, "nikkei225jp加重平均PER", "nikkei225jp日本10年")
            for d, (per, jgb) in sorted(nk.items())]
    rows += build_sp500(fetch_cnbc(".SPX", "2024-12-01"), fetch_multpl_eps(), ust)
    rows += build_dow(fetch_dia("2024-12-01"), wpr_monthly, wpr_latest, ust)
    rows += build_euro(stoxx, anchors["euro"], fetch_bund("2026-08-01"))

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["index"])))

    series = {}
    for r in rows:
        series.setdefault(r["index"], []).append(
            {"date": r["date"], "per": r["per"], "ey": r["ey_pct"], "bond": r["bond_yield_pct"], "ys": r["ys_pct"]})
    for v in series.values():
        v.sort(key=lambda p: p["date"])
    OUT_JSON.write_text(json.dumps(
        {"lastUpdated": dt.datetime.now(JST).isoformat(timespec="seconds"), "series": series},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for k, v in series.items():
        print(f"{k:12s} {len(v):4d}点  {v[0]['date']}〜{v[-1]['date']}  最新 PER {v[-1]['per']} YS {v[-1]['ys']}")
    if wpr_latest:
        print(f"worldperatio 最新: {wpr_latest[0]} ({wpr_latest[1]})")
    print(f"ユーロ基準値: {[(a['date'], a['pe']) for a in anchors['euro']]}")


if __name__ == "__main__":
    main()
