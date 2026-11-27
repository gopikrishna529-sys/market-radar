"""Extra facts for the morning email: market snapshot, breadth trend, fills at the open,
near-miss buy candidates, overnight Zacks changes, leaders in the leading sectors, portfolio risk."""
import numpy as np

from .config import CONFIG
from .indicators import rs_rating
from .scan import scan_universe
from .zacks import load_info
from .holdings import sector_of

SNAP = [("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Small caps"), ("DIA", "Dow 30"),
        ("TLT", "20y Treasury bonds"), ("HYG", "High-yield bonds"), ("UUP", "US dollar"),
        ("GLD", "Gold"), ("USO", "Oil"), ("IBIT", "Bitcoin")]


def _f(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def _pct(a, b):
    return _f((a / b - 1) * 100, 1) if b and np.isfinite(a) and np.isfinite(b) and b > 0 else None


def build(w, ind, result, zp):
    c, h, l, o = w["close"], w["high"], w["low"], w["open"]
    out = {}
    # 1) market snapshot
    snap = []
    lt, stt, e50 = ind["long_trend"].iloc[-1], ind["short_trend"].iloc[-1], ind["ema_exit"].iloc[-1]
    for t, name in SNAP:
        if t not in c or not np.isfinite(c[t].iloc[-1]):
            continue
        s = c[t]
        snap.append({"t": t, "name": name, "close": _f(s.iloc[-1]), "chg": _pct(s.iloc[-1], s.iloc[-2]),
                     "w1": _pct(s.iloc[-1], s.iloc[-6]), "m1": _pct(s.iloc[-1], s.iloc[-22]),
                     "trend": "up" if bool(lt.get(t, False)) else "down" if bool(stt.get(t, False)) else "",
                     "above50": bool(s.iloc[-1] > e50.get(t, np.nan))})
    out["snapshot"] = snap
    # 2) breadth now vs 1 week / 1 month / 3 months ago, and 52-week highs / lows today
    uni = scan_universe(w, ind)
    slow = ind["ema_slow"][uni]
    above = (c[uni] > slow)
    valid = slow.notna()
    pct = lambda i: _f(100 * above.iloc[i][valid.iloc[i]].mean(), 1) if len(c) > -i else None
    hi52 = h[uni].rolling(252, min_periods=120).max().shift(1).iloc[-1]
    lo52 = l[uni].rolling(252, min_periods=120).min().shift(1).iloc[-1]
    last = c[uni].iloc[-1]
    out["breadth"] = {"now": pct(-1), "w1": pct(-6), "m1": pct(-22), "m3": pct(-64),
                      "new_highs": int((last > hi52).sum()), "new_lows": int((last < lo52).sum()),
                      "trend_now": _f(100 * ind["long_trend"][uni].iloc[-1].mean(), 1),
                      "trend_w1": _f(100 * ind["long_trend"][uni].iloc[-6].mean(), 1)}
    # 3) fills at the open (the newest day)
    P = (zp or {}).get("positions", [])
    day = (zp or {}).get("last_day")
    fills = []
    for p in P:
        t = p["ticker"]
        if p.get("entry_date") == day:
            fills.append({"t": t, "side": "buy", "price": p.get("entry"), "ref": p.get("signal_close"),
                          "gap": _pct(p.get("entry") or np.nan, p.get("signal_close") or np.nan),
                          "close": _f(c[t].iloc[-1]) if t in c else None,
                          "day": _pct(c[t].iloc[-1], p.get("entry") or np.nan) if t in c else None})
        if p.get("exit_date") == day and p["status"] == "closed":
            prev = c[t].iloc[-2] if t in c else np.nan
            fills.append({"t": t, "side": "sell", "price": p.get("exit"), "ref": _f(prev), "gap": _pct(p.get("exit") or np.nan, prev),
                          "pnl": p.get("pnl"), "reason": p.get("reason")})
    out["fills"] = fills
    # 4) near misses: pass every buy rule but one
    s = (zp or {}).get("rules") or {}
    zi = load_info()
    rs = rs_rating(ind["score"].iloc[-1], uni)
    held = {p["ticker"] for p in P if p["status"] in ("pending", "open", "exit next open")}
    near = []
    for t in uni:
        z = zi.get(t) or {}
        r, b, tg = z.get("rank"), z.get("broker"), z.get("target")
        if not isinstance(r, int) or not isinstance(b, (int, float)) or not tg or t in held:
            continue
        px = c[t].iloc[-1]
        up = (tg / px - 1) * 100
        fails = []
        if r > s.get("max_rank", 2):
            fails.append(f"Zacks Rank {r} (needs ≤ {s.get('max_rank', 2)})")
        if b >= s.get("max_broker", 2.0):
            fails.append(f"brokers {b:.2f} (needs < {s.get('max_broker', 2.0)})")
        if up < s.get("min_upside_pct", 10):
            fails.append(f"target only {up:+.1f}% away (needs +{s.get('min_upside_pct', 10)}%)")
        if not bool(lt.get(t, False)):
            fails.append("EMA uptrend not set yet")
        elif not px > e50.get(t, np.nan):
            fails.append("close below EMA50")
        if rs.get(t, 0) < s.get("min_rs", 80):
            fails.append(f"RS {int(rs.get(t, 0))} (needs {s.get('min_rs', 80)})")
        if len(fails) == 1:
            near.append({"t": t, "why": fails[0], "rank": r, "broker": _f(b), "upside": _f(up, 1), "rs": int(rs.get(t, 0)),
                         "m1": _pct(px, c[t].iloc[-22])})
    near.sort(key=lambda x: (-x["rs"], -(x["upside"] or 0)))
    out["near"] = near[:15]
    out["near_total"] = len(near)
    # 5) overnight Zacks changes (latest Zacks file vs the one before)
    zdate = max((v.get("date", "") for v in zi.values()), default="")
    ch = []
    for t, z in zi.items():
        hst = z.get("hist") or []
        if len(hst) >= 2 and hst[-1][0] == zdate and hst[-1][1] != hst[-2][1]:
            ch.append({"t": t, "kind": "rank", "old": hst[-2][1], "new": hst[-1][1], "held": t in held, "liquid": t in set(uni)})
        bh = z.get("bhist") or []
        if len(bh) >= 2 and bh[-1][0] == zdate and t in held and bh[-1][1] is not None and bh[-2][1] is not None \
                and abs(bh[-1][1] - bh[-2][1]) >= 0.05:
            ch.append({"t": t, "kind": "broker", "old": bh[-2][1], "new": bh[-1][1], "held": True, "liquid": True})
    out["zchanges"] = {"date": zdate, "items": ch,
                       "new_rank1": sorted([x["t"] for x in ch if x["kind"] == "rank" and x["new"] == 1 and x["liquid"]],
                                           key=lambda t: -rs.get(t, 0))}
    # 6) earnings dates of held stocks (only if the Zacks screen has a "next report date" column)
    out["earnings"] = sorted([{"t": t, "date": zi[t]["next_report"]} for t in held if (zi.get(t) or {}).get("next_report")],
                             key=lambda x: x["date"])
    out["earnings_available"] = any(v.get("next_report") for v in zi.values())
    # 7) leaders in the two leading sectors that we don't own
    S = result.get("sectors") or []
    top = [r["ticker"] for r in S[:2]]
    try:
        from .profiles import load_profiles
        pr = load_profiles()
        sec = sector_of(dict(zip(pr["ticker"], pr["sector"])))
    except Exception:
        sec = sector_of()
    lead = []
    for t in uni:
        if sec.get(t) in top and t not in held and bool(lt.get(t, False)):
            z = zi.get(t) or {}
            lead.append({"t": t, "sector": sec[t], "rs": int(rs.get(t, 0)), "m1": _pct(c[t].iloc[-1], c[t].iloc[-22]),
                         "rank": z.get("rank"), "broker": _f(z.get("broker"))})
    lead.sort(key=lambda x: -x["rs"])
    out["leaders"] = lead[:10]
    # 8) portfolio risk: drawdown of total P&L
    daily = (zp or {}).get("daily") or []
    if daily:
        vals = [x[1] for x in daily]
        peak, mdd = -1e18, 0.0
        for v in vals:
            peak = max(peak, v)
            mdd = min(mdd, v - peak)
        out["risk"] = {"max_dd": round(mdd, 2), "cur_dd": round(vals[-1] - max(vals), 2), "peak": round(max(vals), 2)}
    return out
