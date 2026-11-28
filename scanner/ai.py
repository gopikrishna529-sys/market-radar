"""AI picks: a daily scoring engine that combines trend, relative strength, momentum, chart patterns, volume,
Zacks ratings, sector strength and market mood (VIX / put-call) into ranked long and short ideas.

Each day's picks are saved to data/ai/<YYYY-MM-DD>.json (plus data/ai/index.json) and later re-scored with what
actually happened (return after 1 / 5 / 10 days, stop hit or not), so the tab shows an honest track record.
Nothing here looks ahead: every input is what was known at that day's close (Zacks files dated on/before it).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CONFIG
from .data_store import load_tickers
from .indicators import rs_rating
from .zacks import load_info

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "ai"
MAX_PICKS = 15
BACKFILL_DAYS = 20


def _f(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def _run(mask):
    """Consecutive-True count ending on each row (per column)."""
    s = mask.fillna(False).astype(int)
    cs = s.cumsum()
    return cs - cs.where(s == 0).ffill().fillna(0)


def prepare(w, ind):
    c, h, l, o, v = w["close"], w["high"], w["low"], w["open"], w["volume"]
    e = ind["emas"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max()
    atr = tr.rolling(14, min_periods=10).mean()
    rng10 = (h.rolling(10).max() - l.rolling(10).min()) / c
    u = CONFIG["universe"]
    tick = set(load_tickers()["ticker"])
    elig = ((c >= u["min_price"]) & (ind["avg_vol"] >= u["min_avg_volume"])
            & (ind["avg_dollar_vol"] >= u.get("min_avg_dollar_volume", 0)) & (c.notna().cumsum() >= u["min_history_days"]))
    elig = elig.loc[:, elig.columns.isin(tick)]
    return {
        "c": c, "h": h, "l": l, "o": o, "atr": atr, "atrp": atr / c * 100,
        "e9": e[9], "e20": e[20], "e50": e[50], "e200": e[200],
        "lt": ind["long_trend"], "st": ind["short_trend"],
        "lt_run": _run(ind["long_trend"]), "st_run": _run(ind["short_trend"]),
        "hh20": h.rolling(20).max().shift(1), "ll20": l.rolling(20).min().shift(1),
        "hh50": h.rolling(50).max().shift(1), "ll50": l.rolling(50).min().shift(1),
        "hi252": h.rolling(252, min_periods=120).max(), "lo252": l.rolling(252, min_periods=120).min(),
        "tight20": (h.rolling(20).max() - l.rolling(20).min()) / c,
        "rng10": rng10, "rng10_prev": rng10.shift(10),
        "r5": c / c.shift(5) - 1, "r20": c / c.shift(20) - 1, "r63": c / c.shift(63) - 1,
        "volr": v / v.rolling(50, min_periods=20).mean(),
        "score": ind["score"], "elig": elig,
    }


def _sector_info(c, i):
    """Sector ETF -> rank by 1-month performance vs SPY at row i (1 = best)."""
    sec = CONFIG.get("sectors") or {}
    bench = CONFIG["relative_strength"]["benchmark"]
    rel = {}
    for etf in sec:
        if etf in c and bench in c and i >= 21:
            a, b = c[etf].iloc[i] / c[etf].iloc[i - 21] - 1, c[bench].iloc[i] / c[bench].iloc[i - 21] - 1
            if np.isfinite(a) and np.isfinite(b):
                rel[etf] = (a - b) * 100
    order = sorted(rel, key=lambda k: -rel[k])
    return {k: n + 1 for n, k in enumerate(order)}, rel


def _mood_at(day):
    """Market mood on a past day from the saved VIX / put-call history."""
    try:
        from .sentiment import OUT, summarize
        st = json.loads(OUT.read_text())
    except Exception:
        return None
    cut = lambda d: {k: v for k, v in (d or {}).items() if k <= day}
    st2 = {"vix": {n: cut(s) for n, s in (st.get("vix") or {}).items()}, "pcr": cut(st.get("pcr"))}
    try:
        m = summarize(st2)
    except Exception:
        return None
    return {k: m.get(k) for k in ("mood", "vix", "vix_chg", "term", "pcr_total", "pcr_avg5", "action", "checks", "why_text")}


def _verdict(lsc, ssc, r, z, ext, atrp, sr, nsec, sname, lt, st):
    """One easy number per stock: 0 (strong short setup) … 50 (neutral) … 100 (strong long setup), with the good and bad points."""
    good, bad, pats = [], [], []
    if lsc:
        s, why, pats = lsc
        score = 50 + max(0, s) / 2
        for w in why:
            (bad if w.startswith("⚠") else good).append(w.lstrip("⚠ ").strip())
    elif ssc:
        s, why, pats = ssc
        score = 50 - max(0, s) / 2
        for w in why:                                   # a short setup's reasons are bad news for a holder
            (good if w.startswith("⚠") else bad).append(w.lstrip("⚠ ").strip())
    else:
        score = 50.0
        bad.append("No clear trend — the EMAs are mixed")
        if np.isfinite(r):
            score += (r - 50) * 0.3
            (good if r >= 70 else bad if r <= 30 else []).append(f"RS {int(r)} ({'stronger' if r >= 50 else 'weaker'} than {int(r if r >= 50 else 100 - r)}% of stocks)")
        zr = z.get("rank")
        if zr:
            score += {1: 6, 2: 3, 3: 0, 4: -3, 5: -6}[zr]
            if zr <= 2:
                good.append(f"Zacks Rank #{zr}")
            elif zr >= 4:
                bad.append(f"Zacks Rank #{zr}")
        if sr and sr <= 3:
            good.append(f"Leading sector ({sname})")
        elif sr and sr > nsec - 3:
            bad.append(f"Weak sector ({sname})")
        score = max(30, min(70, score))
    score = int(round(max(1, min(99, score))))
    label = ("Strong long setup" if score >= 80 else "Bullish" if score >= 65 else "Neutral" if score >= 45
             else "Bearish" if score >= 30 else "Strong short setup")
    return {"s": score, "l": label, "g": good[:5], "b": bad[:5], "p": pats}


def picks_for(P, i, sector_of_t, zi, mood):
    c, h, l, o = P["c"], P["h"], P["l"], P["o"]
    day = c.index[i]
    uni = P["elig"].columns[P["elig"].iloc[i].fillna(False).values]
    rs = rs_rating(P["score"].iloc[i], uni).to_dict()
    srank, srel = _sector_info(c, i)
    nsec = max(1, len(srank))
    mname = (mood or {}).get("mood")
    mood_adj = {"Bullish": (3, -6), "Neutral": (0, 0), "Cautious": (-4, 3), "Bearish": (-8, 6)}.get(mname, (0, 0))
    sec_names = CONFIG.get("sectors") or {}
    R = {k: P[k].iloc[i].to_dict() for k in ("c", "h", "l", "o", "atr", "e9", "e20", "e50", "e200", "lt", "st", "lt_run", "st_run",
                                               "hh20", "ll20", "hh50", "ll50", "hi252", "lo252", "tight20", "rng10", "rng10_prev",
                                               "r20", "r63", "volr")}
    prev = {k: P[k].iloc[i - 1].to_dict() for k in ("h", "l", "o", "c")}
    row = lambda k: R[k]
    C, H, L, O, A = row("c"), row("h"), row("l"), row("o"), row("atr")
    E9, E20, E50, E200 = row("e9"), row("e20"), row("e50"), row("e200")
    LT, ST = row("lt"), row("st")
    pH, pL, pO, pC = prev["h"], prev["l"], prev["o"], prev["c"]
    longs, shorts, allsc = [], [], {}
    for t in uni:
        cl, a = C.get(t), A.get(t)
        if not (np.isfinite(cl) and np.isfinite(a) and a > 0):
            continue
        e9, e20, e50, e200 = E9.get(t), E20.get(t), E50.get(t), E200.get(t)
        z = zi.get(t) or {}
        r = rs.get(t, np.nan)
        sec = sector_of_t.get(t)
        sr = srank.get(sec)
        atrp = a / cl * 100
        ext = (cl / e20 - 1) * 100
        volr = R["volr"].get(t, np.nan)
        green = cl > O.get(t, np.nan)
        info = dict(t=t, close=_f(cl), atrp=_f(atrp, 1), rs=None if not np.isfinite(r) else int(r),
                    sector=sec_names.get(sec), sector_rank=sr, zr=z.get("rank"), zb=_f(z.get("broker")), vgm=z.get("vgm"),
                    r20=_f(R["r20"].get(t) * 100, 1), ext=_f(ext, 1))
        lsc = ssc = None
        # ---------------- long side ----------------
        if cl > e50 and e9 > e20 > e50:
            s, why, pat = 0.0, [], []
            if bool(LT.get(t, False)):
                d = int(R["lt_run"].get(t, 0))
                s += 22
                why.append(f"Full EMA uptrend for {d} day{'s' if d != 1 else ''}" + (" (fresh)" if d <= 10 else ""))
            else:
                s += 12
                why.append("Short-term EMAs stacked up (9 > 20 > 50); longer trend still forming")
            if np.isfinite(r):
                s += r * 0.20
                if r >= 85:
                    why.append(f"Leader: stronger than {int(r)}% of stocks (RS {int(r)})")
            r63 = R["r63"].get(t)
            if np.isfinite(r63):
                s += max(-5, min(8, r63 * 30))
            tight = R["tight20"].get(t)
            if cl > R["hh20"].get(t, np.inf):
                if np.isfinite(tight) and tight < 0.15:
                    s += 18
                    pat.append("Base breakout")
                    why.append(f"Broke out of a tight {tight * 100:.0f}% 20-day range")
                else:
                    s += 10
                    pat.append("20-day breakout")
                    why.append("Closed above its 20-day high")
                if cl > R["hh50"].get(t, np.inf):
                    s += 3
            elif L.get(t, np.nan) <= e20 * 1.01 and cl > e20 and green:
                s += 15
                pat.append("Pullback to EMA20")
                why.append("Pulled back to the 20-day EMA and bounced (buy-the-dip in an uptrend)")
            elif L.get(t, np.nan) <= e50 * 1.01 and cl > e50 and green:
                s += 13
                pat.append("Pullback to EMA50")
                why.append("Tested the 50-day EMA and held")
            r20 = R["r20"].get(t)
            if np.isfinite(r20) and r20 > 0.15 and np.isfinite(R["rng10"].get(t)) and R["rng10"].get(t) < 0.08:
                s += 12
                pat.append("Bull flag")
                why.append(f"Strong run (+{r20 * 100:.0f}% in a month) followed by a tight pause")
            r10, r10p = R["rng10"].get(t), R["rng10_prev"].get(t)
            if np.isfinite(r10) and np.isfinite(r10p) and r10 < r10p * 0.6 and r10 < 0.1:
                s += 6
                pat.append("Volatility squeeze")
                why.append("Daily swings have narrowed sharply — often comes before a big move")
            if green and pC.get(t) < pO.get(t) and O.get(t) <= pC.get(t) and cl >= pO.get(t) and abs(cl - e20) / cl < 0.03:
                s += 5
                pat.append("Bullish engulfing")
            hi = R["hi252"].get(t)
            if np.isfinite(hi) and cl >= hi * 0.97:
                s += 5
                why.append("Within 3% of its 52-week high")
            if np.isfinite(volr) and volr >= 1.5 and green:
                s += 5
                why.append(f"Volume {volr:.1f}× normal on an up day (buyers stepping in)")
            if z.get("rank") in (1, 2):
                s += 10 if z["rank"] == 1 else 6
                why.append(f"Zacks Rank #{z['rank']} ({'Strong Buy' if z['rank'] == 1 else 'Buy'})")
            elif z.get("rank") in (4, 5):
                s -= 10
                why.append(f"⚠ Zacks Rank #{z['rank']} (Sell) — analysts cutting estimates")
            if isinstance(z.get("broker"), (int, float)) and z["broker"] < 2:
                s += 3
            if z.get("target") and z["target"] / cl - 1 >= 0.15:
                s += 2
                why.append(f"Analysts' target {z['target']:.2f} is {(z['target'] / cl - 1) * 100:.0f}% above")
            if z.get("vgm") in ("A", "B"):
                s += 2
            if sr:
                if sr <= 3:
                    s += 5
                    why.append(f"In a leading sector ({sec_names.get(sec)}, #{sr} of {nsec})")
                elif sr > nsec - 3:
                    s -= 5
                    why.append(f"⚠ Weak sector ({sec_names.get(sec)}, #{sr} of {nsec})")
            if ext > 10:
                s -= 10
                why.append(f"⚠ Stretched {ext:.0f}% above EMA20 — wait for a pullback")
            if atrp > 7:
                s -= 5
                why.append(f"⚠ Very volatile (moves ~{atrp:.0f}% a day)")
            s += mood_adj[0]
            lsc = (s, why, pat)
            if s >= 50:
                entry = max(H.get(t), cl) + 0.01
                stop = max(e50 * 0.99, entry - 2 * a)
                if stop >= entry - 0.6 * a:
                    stop = entry - 1.5 * a
                risk = entry - stop
                longs.append({**info, "side": "long", "score": int(round(min(99, s))), "pat": pat, "why": why,
                              "entry": _f(entry), "stop": _f(stop), "target": _f(entry + 2 * risk),
                              "t2": _f(z.get("target")) if z.get("target") and z["target"] > entry + 2 * risk else None,
                              "risk": _f(risk / entry * 100, 1)})
        # ---------------- short side ----------------
        if cl < e50 and e9 < e20 < e50:
            s, why, pat = 0.0, [], []
            if bool(ST.get(t, False)):
                d = int(R["st_run"].get(t, 0))
                s += 22
                why.append(f"Full EMA downtrend for {d} day{'s' if d != 1 else ''}")
            else:
                s += 12
                why.append("Short-term EMAs stacked down (9 < 20 < 50)")
            if np.isfinite(r):
                s += (100 - r) * 0.20
                if r <= 15:
                    why.append(f"Laggard: weaker than {100 - int(r)}% of stocks (RS {int(r)})")
            r63 = R["r63"].get(t)
            if np.isfinite(r63):
                s += max(-5, min(8, -r63 * 30))
            if cl < R["ll20"].get(t, -np.inf):
                s += 14
                pat.append("20-day breakdown")
                why.append("Closed below its 20-day low")
                if cl < R["ll50"].get(t, -np.inf):
                    s += 3
            elif H.get(t, np.nan) >= e20 * 0.99 and cl < e20 and not green:
                s += 15
                pat.append("Failed rally at EMA20")
                why.append("Bounced into the 20-day EMA and got rejected (sell-the-rip in a downtrend)")
            elif H.get(t, np.nan) >= e50 * 0.99 and cl < e50 and not green:
                s += 13
                pat.append("Rejected at EMA50")
                why.append("Rallied to the 50-day EMA and failed")
            r20 = R["r20"].get(t)
            if np.isfinite(r20) and r20 < -0.15 and np.isfinite(R["rng10"].get(t)) and R["rng10"].get(t) < 0.08:
                s += 10
                pat.append("Bear flag")
                why.append(f"Sharp drop ({r20 * 100:.0f}% in a month) followed by a weak pause")
            if not green and pC.get(t) > pO.get(t) and O.get(t) >= pC.get(t) and cl <= pO.get(t):
                s += 5
                pat.append("Bearish engulfing")
            lo = R["lo252"].get(t)
            if np.isfinite(lo) and cl <= lo * 1.03:
                s += 4
                why.append("Near its 52-week low")
            if np.isfinite(volr) and volr >= 1.5 and not green:
                s += 5
                why.append(f"Volume {volr:.1f}× normal on a down day (sellers in control)")
            if z.get("rank") in (4, 5):
                s += 10 if z["rank"] == 5 else 6
                why.append(f"Zacks Rank #{z['rank']} ({'Strong Sell' if z['rank'] == 5 else 'Sell'})")
            elif z.get("rank") in (1, 2):
                s -= 10
                why.append(f"⚠ Zacks Rank #{z['rank']} (Buy) — analysts are positive")
            if isinstance(z.get("broker"), (int, float)) and z["broker"] >= 3:
                s += 3
            if sr:
                if sr > nsec - 3:
                    s += 5
                    why.append(f"In a weak sector ({sec_names.get(sec)}, #{sr} of {nsec})")
                elif sr <= 3:
                    s -= 5
                    why.append(f"⚠ Strong sector ({sec_names.get(sec)}) — shorts there fight the tide")
            if ext < -10:
                s -= 10
                why.append(f"⚠ Already {-ext:.0f}% below EMA20 — oversold, bounce risk")
            if atrp > 7:
                s -= 5
                why.append(f"⚠ Very volatile (moves ~{atrp:.0f}% a day) — squeezes hurt")
            s += mood_adj[1]
            ssc = (s, why, pat)
            if s >= 50:
                entry = min(L.get(t), cl) - 0.01
                stop = min(e50 * 1.01, entry + 2 * a)
                if stop <= entry + 0.6 * a:
                    stop = entry + 1.5 * a
                risk = stop - entry
                shorts.append({**info, "side": "short", "score": int(round(min(99, s))), "pat": pat, "why": why,
                               "entry": _f(entry), "stop": _f(stop), "target": _f(entry - 2 * risk),
                               "risk": _f(risk / entry * 100, 1)})
        allsc[t] = _verdict(lsc, ssc, r, z, ext, atrp, sr, nsec, sec_names.get(sec), bool(LT.get(t, False)), bool(ST.get(t, False)))
    for L_ in (longs, shorts):
        L_.sort(key=lambda x: -x["score"])
        for x in L_:
            x["grade"] = "A" if x["score"] >= 75 else "B" if x["score"] >= 63 else "C"
    picks_for.allsc = allsc
    return longs[:MAX_PICKS], shorts[:MAX_PICKS], len(longs), len(shorts), srank, srel


def _view(day, mood, br, srank, srel, nl, ns):
    sec_names = CONFIG.get("sectors") or {}
    m = (mood or {}).get("mood")
    if m in ("Bearish",) or (br is not None and br < 35):
        stance, tip = "Defensive", "Fear is high — favour short ideas or cash; only A-grade longs, at smaller size."
    elif m == "Cautious" or (br is not None and br < 50):
        stance, tip = "Selective", "Mixed market — take only the best setups, in the strongest sectors, with tight stops."
    else:
        stance, tip = "Lean long", "Healthy market — long setups in leading sectors have the wind behind them."
    top = [sec_names.get(k, k) for k in sorted(srank, key=srank.get)[:2]]
    bot = [sec_names.get(k, k) for k in sorted(srank, key=srank.get)[-2:]]
    return {"stance": stance, "tip": tip, "breadth": br, "mood": mood, "top_sectors": top, "weak_sectors": bot,
            "n_long": nl, "n_short": ns}


def _evaluate(P, rec):
    """Fill in what happened after each pick (entry assumed at the next open)."""
    c, h, l, o = P["c"], P["h"], P["l"], P["o"]
    idx = {d.strftime("%Y-%m-%d"): k for k, d in enumerate(c.index)}
    i = idx.get(rec["date"])
    if i is None:
        return rec
    n = len(c) - 1
    tot = {"long": [], "short": []}
    for side in ("longs", "shorts"):
        for p in rec.get(side, []):
            t, sgn = p["t"], (1 if side == "longs" else -1)
            if t not in c or i + 1 > n:
                p.pop("res", None)
                continue
            e = o[t].iloc[i + 1]
            if not np.isfinite(e) or e <= 0:
                continue
            res = {"in": _f(e)}
            for k in (1, 5, 10):
                if i + k <= n:
                    res[f"d{k}"] = _f(sgn * (c[t].iloc[i + k] / e - 1) * 100)
            res["now"] = _f(sgn * (c[t].iloc[n] / e - 1) * 100)
            seg_l, seg_h = l[t].iloc[i + 1:n + 1], h[t].iloc[i + 1:n + 1]
            hit = (seg_l <= p["stop"]).values if sgn > 0 else (seg_h >= p["stop"]).values
            tgt = (seg_h >= p["target"]).values if sgn > 0 else (seg_l <= p["target"]).values
            fs = int(np.argmax(hit)) if hit.any() else None
            ft = int(np.argmax(tgt)) if tgt.any() else None
            res["out"] = ("target" if ft is not None and (fs is None or ft < fs) else "stop" if fs is not None else "open")
            res["days"] = n - i
            p["res"] = res
            tot["long" if sgn > 0 else "short"].append(res["now"])
    rec["perf"] = {k: {"n": len(v), "avg": _f(np.mean(v)) if v else None, "win": _f(100 * np.mean([x > 0 for x in v]), 0) if v else None}
                   for k, v in tot.items()}
    return rec


def update(w, ind):
    DIR.mkdir(parents=True, exist_ok=True)
    P = prepare(w, ind)
    c = P["c"]
    dates = [d.strftime("%Y-%m-%d") for d in c.index]
    have = sorted(p.stem for p in DIR.glob("????-??-??.json"))
    todo = [d for d in dates[-BACKFILL_DAYS:] if d not in have] if len(have) < BACKFILL_DAYS else [d for d in dates[-3:] if d not in have]
    have_sc = {p.stem[7:] for p in DIR.glob("scores-????-??-??.json")}
    todo += [d for d in dates[-BACKFILL_DAYS:] if d in have and d not in have_sc and d not in todo]
    try:
        from .profiles import load_profiles
        from .holdings import sector_of
        pr = load_profiles()
        sec = sector_of(dict(zip(pr["ticker"], pr["sector"])))
    except Exception:
        sec = {}
    slow = ind["ema_slow"]
    for d in todo:
        i = dates.index(d)
        if i < 70:
            continue
        zi = load_info(as_of=d)
        mood = _mood_at(d)
        longs, shorts, nl, ns, srank, srel = picks_for(P, i, sec, zi, mood)
        uni = P["elig"].columns[P["elig"].iloc[i].fillna(False).values]
        br = _f(100 * (c[uni].iloc[i] > slow[uni].iloc[i]).mean(), 0) if len(uni) else None
        rec = {"date": d, "view": _view(d, mood, br, srank, srel, nl, ns), "longs": longs, "shorts": shorts,
               "zacks": bool(zi and any(v.get("rank") for v in zi.values()))}
        (DIR / f"{d}.json").write_text(json.dumps(rec, separators=(",", ":")))
        (DIR / f"scores-{d}.json").write_text(json.dumps(getattr(picks_for, "allsc", {}), separators=(",", ":")))
    # re-score every saved day with what has happened since, and rebuild the index
    index = []
    for f in sorted(DIR.glob("scores-????-??-??.json"))[:-60]:        # keep 60 days of per-stock scores
        f.unlink()
    for f in sorted(DIR.glob("????-??-??.json"))[-120:]:
        rec = _evaluate(P, json.loads(f.read_text()))
        f.write_text(json.dumps(rec, separators=(",", ":")))
        index.append({"date": rec["date"], "stance": rec["view"]["stance"], "mood": (rec["view"].get("mood") or {}).get("mood"),
                      "nl": len(rec["longs"]), "ns": len(rec["shorts"]), "perf": rec.get("perf")})
    (DIR / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    print(f"AI picks: {len(todo)} new day(s), {len(index)} days saved; latest {index[-1]['date'] if index else '-'}")
    return index
