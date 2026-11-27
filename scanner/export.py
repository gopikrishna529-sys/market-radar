"""Writes the static website: app page + JSON data files it reads."""
import json

from . import jsonsafe
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CONFIG
from .data_store import load_all_tickers
from .indicators import rs_rating, streak
from .scan import scan_universe

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
RESULTS = ROOT / "results"
TEMPLATE = Path(__file__).resolve().parent / "site_template.html"


def _num(x, nd):
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), nd)


def build_site(w, ind):
    if SITE.exists():
        shutil.rmtree(SITE)
    data = SITE / "data"
    (data / "scans").mkdir(parents=True)
    (data / "bars").mkdir()

    # scan history + backtest
    dates = []
    for f in sorted(RESULTS.glob("20*.json")):
        shutil.copy(f, data / "scans" / f.name)
        dates.append(f.stem)
    if (RESULTS / "latest.json").exists():
        shutil.copy(RESULTS / "latest.json", data / "latest.json")
    if (RESULTS / "backtest.json").exists():
        shutil.copy(RESULTS / "backtest.json", data / "backtest.json")
    (data / "dates.json").write_text(jsonsafe.dumps(sorted(dates, reverse=True)))

    # which tickers get a chart
    ch = CONFIG.get("chart", {})
    c = w["close"]
    tick = load_all_tickers().drop_duplicates("ticker").set_index("ticker")
    last, avgv = c.iloc[-1], ind["avg_vol"].iloc[-1]
    stocks = [t for t in c.columns if t in tick.index
              and last.get(t, 0) >= ch.get("min_price", 1) and avgv.get(t, 0) >= ch.get("min_avg_volume", 50000)]
    etfs = ([CONFIG["relative_strength"]["benchmark"]] + list(CONFIG["sectors"]) + list(CONFIG.get("industries") or {})
            + list(ch.get("extra_tickers", [])))
    chart_set = list(dict.fromkeys(stocks + [e for e in etfs if e in c.columns]))

    # calendar shared by all bar files
    cal = [d.strftime("%Y-%m-%d") for d in c.index]
    (data / "calendar.json").write_text(jsonsafe.dumps(cal))

    O, H, L, C, V = (w[k] for k in ("open", "high", "low", "close", "volume"))
    for t in chart_set:
        s = C[t]
        first = s.first_valid_index()
        if first is None:
            continue
        i0 = c.index.get_loc(first)
        px_nd = 2 if (last.get(t) or 0) >= 1 else 4

        def arr(df, nd):
            return [_num(x, nd) for x in df[t].to_numpy()[i0:]]
        bars = {"t": t, "i0": int(i0), "o": arr(O, px_nd), "h": arr(H, px_nd), "l": arr(L, px_nd),
                "c": arr(C, px_nd), "v": [None if not np.isfinite(x) else int(x) for x in V[t].to_numpy()[i0:]]}
        (data / "bars" / f"{t}.json").write_text(jsonsafe.dumps(bars, separators=(",", ":")))

    # one summary row per charted ticker (used by search, watchlist, chart header)
    uni = scan_universe(w, ind)
    rating = rs_rating(ind["score"].iloc[-1], uni)
    lt, st = ind["long_trend"][chart_set], ind["short_trend"][chart_set]
    l_days, s_days = streak(lt), streak(st)
    em = ind["ema_exit"].iloc[-1]
    uni_set = set(uni)
    names = tick["name"].to_dict()
    names.update({**CONFIG["sectors"], **(CONFIG.get("industries") or {}),
                  "SPY": "S&P 500 ETF", "QQQ": "Nasdaq-100 ETF", "IWM": "Russell 2000 ETF", "DIA": "Dow Jones ETF",
                  "TLT": "20+ Year Treasury ETF", "GLD": "Gold ETF"})
    from .profiles import EXCHANGES, load_profiles
    prof = load_profiles().drop_duplicates("ticker").set_index("ticker")
    ptype = tick["type"].to_dict() if "type" in tick else {}
    pexch = tick["exchange"].to_dict() if "exchange" in tick else {}
    adv = ind["avg_dollar_vol"].iloc[-1]
    def perf(t, n):
        s_ = c[t].dropna()
        return _num((s_.iloc[-1] / s_.iloc[-1 - n] - 1) * 100, 1) if len(s_) > n else None
    rows = []
    for t in chart_set:
        state = "L" if lt[t].iloc[-1] else "S" if st[t].iloc[-1] else ""
        days = int(l_days[t] if state == "L" else s_days[t] if state == "S" else 0)
        rows.append([t, names.get(t, "") or "", _num(last[t], 2), _num(ind["chg"][t].iloc[-1], 2),
                     None if pd.isna(rating.get(t)) else int(rating[t]), state, days,
                     _num((last[t] / em[t] - 1) * 100, 1) if np.isfinite(em.get(t, np.nan)) else None,
                     _num(avgv.get(t, np.nan) / 1000, 0), t in uni_set,
                     *_profile(t, prof, ptype, pexch, last, EXCHANGES),
                     perf(t, 21), perf(t, 63), _num(adv.get(t, np.nan) / 1e6, 1)])
    (data / "stocks.json").write_text(jsonsafe.dumps({
        "date": cal[-1],
        "fields": ["ticker", "name", "close", "chg", "rs", "state", "days", "vs_ema50", "avg_vol_k", "in_scan",
                   "type", "exchange", "sector", "industry", "mcap_m", "shares_m", "country",
                   "perf_1m", "perf_3m", "adv_m"],
        "groups": CONFIG.get("groups", {}),
        "rows": rows}, separators=(",", ":")))

    # Zacks saved-screen CSV, joined with our own trend/RS columns
    from .zacks import load_info, load_latest
    zi = load_info()
    (data / "zinfo.json").write_text(jsonsafe.dumps(zi, separators=(",", ":")))
    zp = ROOT / "data" / "zportfolio.json"                  # Zacks portfolio (tracked forward, see zportfolio.py)
    if zp.exists():
        (data / "zportfolio.json").write_text(zp.read_text())
    if zi:
        print(f"zacks: info for {len(zi)} tickers, {sum('target' in v for v in zi.values())} with a target price")
    z = load_latest()
    if z:
        ti = z["ticker_col"]
        _merge_screens(z)                       # add the other saved screens' columns (broker rating, price target ...)
        ours = {r[0]: r for r in rows}
        zr = []
        for r in z["rows"]:
            o = ours.get(r[ti])
            zr.append(r + ([o[5], o[6], o[4], o[3], o[17], o[9]] if o else ["", 0, None, None, None, False]))
        (data / "zacks.json").write_text(jsonsafe.dumps({
            "date": z["date"], "prev_date": z["prev_date"], "new": z["new"], "ticker_col": ti,
            "columns": z["columns"], "ours": ["state", "days", "rs", "chg", "perf_1m", "in_scan"],
            "rows": zr}, separators=(",", ":")))
        print(f"zacks: {len(zr)} stocks in the {z['date']} screen, {len(z['new'])} new")

    html = TEMPLATE.read_text().replace("__TITLE__", CONFIG["report"]["site_title"])
    html = html.replace("__EMA__", jsonsafe.dumps(CONFIG["ema"]))
    html = html.replace("__REPO__", os.environ.get("GITHUB_REPOSITORY", ""))
    (SITE / "index.html").write_text(html)
    (SITE / ".nojekyll").write_text("")
    print(f"website built: {len(chart_set)} charts, {len(dates)} days of scans")


def _merge_screens(z):
    """Join the newest file of every other configured Zacks screen onto the main screen, by ticker."""
    from .zacks import _screens, broker_txt, load_latest
    ti, have = z["ticker_col"], {c.strip().lower() for c in z["columns"]}
    for pre in _screens()[1][1:]:
        o = load_latest(pre)
        if not o:
            continue
        oti = o["ticker_col"]
        add = [i for i, c in enumerate(o["columns"])
               if i != oti and c.strip().lower() not in have and c.strip().lower() not in ("company name", "company")]
        if not add:
            continue
        by = {r[oti]: r for r in o["rows"]}
        bi = next((i for i in add if "broker rec" in o["columns"][i].lower()), None)
        z["columns"] = z["columns"] + [o["columns"][i] for i in add] + (["Broker rating"] if bi is not None else [])
        have |= {o["columns"][i].strip().lower() for i in add}
        for k, r in enumerate(z["rows"]):
            src = by.get(r[ti])
            extra = [src[i] if src and i < len(src) else "" for i in add]
            if bi is not None:
                b = src[bi] if src and bi < len(src) else None
                extra.append(broker_txt(b) if isinstance(b, (int, float)) else "")
            z["rows"][k] = r + extra


def _profile(t, prof, ptype, pexch, last, EXCHANGES):
    """type, exchange, sector, industry, market cap ($M), shares outstanding (M), country."""
    ty = {"CS": "Stock", "ADRC": "ADR", "ETF": "ETF", "ETV": "ETF", "ETN": "ETN"}.get(ptype.get(t), "ETF" if t in
         (list(CONFIG["sectors"]) + list(CONFIG.get("industries") or {}) + ["SPY", "QQQ", "IWM", "DIA", "TLT", "GLD"]) else "Stock")
    code = pexch.get(t)
    code = code if isinstance(code, str) else ""           # blank exchange in the ticker list -> ""
    ex = EXCHANGES.get(code, code)
    if t in prof.index:
        p = prof.loc[t]
        mc = p.get("market_cap")
        mcap = _num(mc / 1e6, 0) if mc == mc and mc else None
        shares = _num(mc / last[t] / 1e6, 1) if mcap and last.get(t) else None
        sector = p.get("sector") if isinstance(p.get("sector"), str) else "Other"
        industry = p.get("industry") if isinstance(p.get("industry"), str) else "Other"
        country = p.get("country") if isinstance(p.get("country"), str) else ""
    else:
        mcap = shares = None
        sector, industry, country = ("ETF", "ETF", "") if ty in ("ETF", "ETN") else ("Other", "Other", "")
    if ty in ("ETF", "ETN"):
        sector, industry = "ETF", "ETF"
    return [ty, ex, sector, industry, mcap, shares, country]
