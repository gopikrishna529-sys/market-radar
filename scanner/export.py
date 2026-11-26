"""Writes the static website: app page + JSON data files it reads."""
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CONFIG
from .data_store import load_tickers
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
    (data / "dates.json").write_text(json.dumps(sorted(dates, reverse=True)))

    # which tickers get a chart
    ch = CONFIG.get("chart", {})
    c = w["close"]
    tick = load_tickers().set_index("ticker")
    last, avgv = c.iloc[-1], ind["avg_vol"].iloc[-1]
    stocks = [t for t in c.columns if t in tick.index
              and last.get(t, 0) >= ch.get("min_price", 1) and avgv.get(t, 0) >= ch.get("min_avg_volume", 50000)]
    etfs = ([CONFIG["relative_strength"]["benchmark"]] + list(CONFIG["sectors"]) + list(CONFIG.get("industries") or {})
            + list(ch.get("extra_tickers", [])))
    chart_set = list(dict.fromkeys(stocks + [e for e in etfs if e in c.columns]))

    # calendar shared by all bar files
    cal = [d.strftime("%Y-%m-%d") for d in c.index]
    (data / "calendar.json").write_text(json.dumps(cal))

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
        (data / "bars" / f"{t}.json").write_text(json.dumps(bars, separators=(",", ":")))

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
    rows = []
    for t in chart_set:
        state = "L" if lt[t].iloc[-1] else "S" if st[t].iloc[-1] else ""
        days = int(l_days[t] if state == "L" else s_days[t] if state == "S" else 0)
        rows.append([t, names.get(t, "") or "", _num(last[t], 2), _num(ind["chg"][t].iloc[-1], 2),
                     None if pd.isna(rating.get(t)) else int(rating[t]), state, days,
                     _num((last[t] / em[t] - 1) * 100, 1) if np.isfinite(em.get(t, np.nan)) else None,
                     _num(avgv.get(t, np.nan) / 1000, 0), t in uni_set])
    (data / "stocks.json").write_text(json.dumps({
        "date": cal[-1],
        "fields": ["ticker", "name", "close", "chg", "rs", "state", "days", "vs_ema50", "avg_vol_k", "in_scan"],
        "rows": rows}, separators=(",", ":")))

    html = TEMPLATE.read_text().replace("__TITLE__", CONFIG["report"]["site_title"])
    html = html.replace("__EMA__", json.dumps(CONFIG["ema"]))
    html = html.replace("__REPO__", os.environ.get("GITHUB_REPOSITORY", ""))
    (SITE / "index.html").write_text(html)
    (SITE / ".nojekyll").write_text("")
    print(f"website built: {len(chart_set)} charts, {len(dates)} days of scans")
