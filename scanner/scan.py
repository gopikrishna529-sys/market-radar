"""The daily scan: EMA trend, 20-day breakouts, relative strength, sector rotation."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CONFIG
from .data_store import load_tickers
from .indicators import rs_rating, streak

RESULTS = Path(__file__).resolve().parent.parent / "results"


def _pct(a, b):
    return (a / b - 1.0) * 100.0


def _rnd(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def scan_universe(w, ind):
    """Tickers that pass the scan filters on the last day."""
    u = CONFIG["universe"]
    c = w["close"]
    tick = load_tickers()
    ok = (c.columns.isin(tick["ticker"])
          & (c.iloc[-1] >= u["min_price"]).values
          & (ind["avg_vol"].iloc[-1] >= u["min_avg_volume"]).values
          & (ind["avg_dollar_vol"].iloc[-1] >= u.get("min_avg_dollar_volume", 0)).values
          & (c.notna().sum() >= u["min_history_days"]).values)
    return c.columns[ok]


def run_scan(w, ind):
    cfg = CONFIG
    c, h, v = w["close"], w["high"], w["volume"]
    bench = cfg["relative_strength"]["benchmark"]
    if bench not in c:
        raise RuntimeError(f"Benchmark {bench} missing from data")
    date = c.index[-1]
    spy = c[bench]
    ef, es = ind["ema20"], ind["ema_slow"]
    trend = ind["long_trend"]

    N = cfg["breakout"]["lookback_days"]
    prior_high = h.shift(1).rolling(N, min_periods=N).max()
    breakout = trend & (c > prior_high)
    prior_low = w["low"].shift(1).rolling(N, min_periods=N).min()
    breakdown = ind["short_trend"] & (c < prior_low)
    avg_vol, chg = ind["avg_vol"], ind["chg"]
    last = c.iloc[-1]

    rs_line = c.div(spy, axis=0)
    rs_new_high = rs_line.iloc[-1] >= rs_line.iloc[-50:].max()

    uni = scan_universe(w, ind)
    names = load_tickers().set_index("ticker")["name"].to_dict()
    rating = rs_rating(ind["score"].iloc[-1], uni)

    spy_chg = float(chg[bench].iloc[-1])
    days_trend = streak(trend[uni])
    days_down = streak(ind["short_trend"][uni])
    t_last, b_last = trend[uni].iloc[-1], breakout[uni].iloc[-1]

    def row(t, short=False):
        r = rating.get(t)
        return {
            "ticker": t,
            "name": names.get(t, ""),
            "close": _rnd(last[t]),
            "chg": _rnd(chg[t].iloc[-1]),
            "vol_ratio": _rnd(v[t].iloc[-1] / avg_vol[t].iloc[-1]),
            "rs": int(r) if pd.notna(r) else None,
            "rs_high": bool(rs_new_high.get(t, False)),
            "days_trend": int((days_down if short else days_trend).get(t, 0)),
            "ext_ema20": _rnd(_pct(last[t], ef[t].iloc[-1]), 1),
            "perf_1m": _rnd(_pct(last[t], c[t].iloc[-22]), 1) if len(c) > 22 else None,
        }

    by_rs = lambda r: -(r["rs"] or 0)
    trend_list = sorted((row(t) for t in uni[t_last.fillna(False).values]), key=by_rs)
    breakout_list = sorted((row(t) for t in uni[b_last.fillna(False).values]), key=by_rs)
    d_last = breakdown[uni].iloc[-1]
    breakdown_list = sorted((row(t, True) for t in uni[d_last.fillna(False).values]), key=lambda r: (r["rs"] or 0))
    rs_days = []
    if spy_chg < 0:
        up = chg[uni].iloc[-1] > cfg["relative_strength"]["rs_day_min_gain_pct"]
        rs_days = sorted((row(t) for t in uni[up.fillna(False).values]), key=lambda r: -(r["chg"] or 0))
    min_rs = cfg["relative_strength"]["leaders_min_rating"]
    leaders = [row(t) for t in rating[uni][rating[uni] >= min_rs].index]
    leaders = sorted((r for r in leaders if r["rs_high"]), key=by_rs)

    result = {
        "date": date.strftime("%Y-%m-%d"),
        "benchmark": {"ticker": bench, "close": _rnd(spy.iloc[-1]), "chg": _rnd(spy_chg)},
        "universe_size": int(len(uni)),
        "trend": trend_list,
        "breakouts": breakout_list,
        "breakdowns": breakdown_list,
        "rs_days": rs_days,
        "rs_leaders": leaders,
        "sectors": sector_rotation(c, spy, cfg["sectors"], "Sector"),
        "industries": sector_rotation(c, spy, cfg.get("industries") or {}, "Industry"),
        "breadth": {
            "pct_in_trend": _rnd(100 * t_last.mean(), 1),
            "pct_above_ema200": _rnd(100 * (last[uni] > es[uni].iloc[-1]).mean(), 1),
            "new_trend_today": int((days_trend == 1).sum()),
        },
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{result['date']}.json").write_text(json.dumps(result))
    (RESULTS / "latest.json").write_text(json.dumps(result))
    return result


def sector_rotation(c, spy, etfs, kind):
    """Relative performance vs the benchmark + RRG-style quadrant for each ETF."""
    out = []
    for etf, name in etfs.items():
        if etf not in c or c[etf].notna().sum() < 80:
            continue
        ratio = c[etf] / spy
        rs_ratio = 100 * ratio / ratio.ewm(span=50, adjust=False).mean()
        rs_mom = 100 * rs_ratio / rs_ratio.shift(10)
        rr, rm = rs_ratio.iloc[-1], rs_mom.iloc[-1]
        quadrant = ("Leading" if rr >= 100 and rm >= 100 else
                    "Weakening" if rr >= 100 else
                    "Improving" if rm >= 100 else "Lagging")

        def rel(n):
            return _rnd(100 * (ratio.iloc[-1] / ratio.iloc[-1 - n] - 1), 1)
        tail = [{"x": _rnd(rs_ratio.iloc[i], 2), "y": _rnd(rs_mom.iloc[i], 2)} for i in range(-26, -1, 5)]
        prev_1m = _rnd(100 * (ratio.iloc[-6] / ratio.iloc[-27] - 1), 1)
        out.append({"ticker": etf, "name": name, "kind": kind, "close": _rnd(c[etf].iloc[-1]),
                    "rel_1w": rel(5), "rel_1m": rel(21), "rel_3m": rel(63), "_prev_1m": prev_1m,
                    "rs_ratio": _rnd(rr), "rs_mom": _rnd(rm), "quadrant": quadrant, "tail": tail})
    out.sort(key=lambda r: -(r["rel_1m"] if r["rel_1m"] is not None else -999))
    prev = sorted(out, key=lambda r: -(r["_prev_1m"] if r["_prev_1m"] is not None else -999))
    prev_rank = {r["ticker"]: i + 1 for i, r in enumerate(prev)}
    for i, r in enumerate(out):
        r["rank"] = i + 1
        r["rank_change"] = prev_rank[r["ticker"]] - (i + 1)
        del r["_prev_1m"]
    return out
