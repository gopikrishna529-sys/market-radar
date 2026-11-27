"""Zacks portfolio: a paper portfolio tracked forward from its start day (no backtest - Zacks history
only exists from the first saved CSV).

Buy  (next open) when, at a day's close:
       Zacks Rank <= max_rank  AND  average broker rating < max_broker
       AND Zacks average price target is >= min_upside_pct above the close
       AND the EMA uptrend is set (EMA9 > EMA20 > EMA50 > EMA100 > EMA200)
       AND RS rating >= min_rs  AND  the stock passes the scanner's liquidity filter
Sell (next open) after a close below the exit EMA (50), or when the Zacks Rank falls to sell_rank or worse.
After a sale the stock can only be bought again once it has stopped qualifying for at least one day
(stops it re-buying the day after every exit).

State lives in data/zportfolio.json and is committed by the daily workflow, so it builds up day by day.
"""
import json
from pathlib import Path

import numpy as np

from . import jsonsafe
from .config import CONFIG
from .indicators import rs_rating
from .scan import scan_universe
from .zacks import load_info
from .holdings import sector_of

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "zportfolio.json"

DEFAULTS = {"max_rank": 2, "max_broker": 2.0, "min_upside_pct": 10.0, "min_rs": 80,
            "sell_rank": 4, "dollars_per_trade": 10000, "max_zacks_age_days": 5}


def settings():
    s = dict(DEFAULTS)
    s.update(CONFIG.get("zacks_portfolio") or {})
    s["cost_pct"] = (CONFIG.get("backtest") or {}).get("cost_per_side_pct", 0.05)
    return s


def _f(x, n=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"start": None, "last_day": None, "positions": [], "blocked": [], "daily": []}


def candidates(w, ind, zi, s, i=-1):
    """Stocks meeting every buy rule at the close of row i."""
    c = w["close"]
    uni = scan_universe(w, ind)
    rs = rs_rating(ind["score"].iloc[i], uni)
    trend = ind["long_trend"].iloc[i]
    above = (c.iloc[i] > ind["ema_exit"].iloc[i])          # a close below EMA50 would be sold straight away
    out = {}
    for t in uni:
        z = zi.get(t) or {}
        r, b, tg = z.get("rank"), z.get("broker"), z.get("target")
        px = c[t].iloc[i]
        if not (isinstance(r, int) and r <= s["max_rank"]):
            continue
        if not (isinstance(b, (int, float)) and b < s["max_broker"]):
            continue
        if not (isinstance(tg, (int, float)) and np.isfinite(px) and px > 0):
            continue
        up = (tg / px - 1) * 100
        if up < s["min_upside_pct"] or not bool(trend.get(t, False)) or not bool(above.get(t, False)) or not (rs.get(t, 0) >= s["min_rs"]):
            continue
        out[t] = {"rank": r, "broker": _f(b), "target": _f(tg), "upside": _f(up, 1), "rs": int(rs[t]),
                  "signal_close": _f(px)}
    return out


def update(w, ind):
    """Process every price day not yet seen. New buys are only decided on the newest day,
    because the Zacks file describes today, not the past."""
    s = settings()
    st = load_state()
    zi = load_info()
    c, o = w["close"], w["open"]
    dates = [d.strftime("%Y-%m-%d") for d in c.index]
    em = ind["ema_exit"]
    cost = s["cost_pct"] / 100
    last = st.get("last_day")
    todo = [k for k, d in enumerate(dates) if last is None or d > last]
    if last is None:
        todo = todo[-1:]                                    # first run: start tracking today
        st["start"] = dates[-1]
    blocked = set(st.get("blocked", []))
    pos = st["positions"]
    zdate = max((v.get("date", "") for v in zi.values()), default="")
    for k in todo:
        d = dates[k]
        # 1) fill yesterday's orders at today's open
        for p in pos:
            t = p["ticker"]
            px = o[t].iloc[k] if t in o else np.nan
            if p["status"] == "pending":
                if np.isfinite(px) and px > 0:
                    p.update(status="open", entry_date=d, entry=_f(px * (1 + cost), 4))
                elif k - dates.index(p["signal_date"]) > 3:
                    p.update(status="cancelled", reason="no price after the signal")
            elif p["status"] == "exit next open" and np.isfinite(px) and px > 0:
                p.update(status="closed", exit_date=d, exit=_f(px * (1 - cost), 4))
                p["pnl"] = _f((p["exit"] / p["entry"] - 1) * 100)
                p["days"] = k - dates.index(p["entry_date"])
                for x in ("last", "rank_now", "broker_now"):
                    p.pop(x, None)
                blocked.add(t)
        # 2) exit signals at today's close
        for p in pos:
            if p["status"] != "open":
                continue
            t = p["ticker"]
            cl, e50 = c[t].iloc[k], em[t].iloc[k]
            z = zi.get(t) or {}
            why = None
            if np.isfinite(cl) and np.isfinite(e50) and cl < e50:
                why = f"close below EMA{CONFIG['ema']['exit']}"
            elif k == len(dates) - 1 and isinstance(z.get("rank"), int) and z["rank"] >= s["sell_rank"]:
                why = f"Zacks Rank fell to {z['rank']}"
            if why:
                p.update(status="exit next open", exit_signal=d, reason=why)
        # 3) new buys (newest day only, with a recent Zacks file)
        if k == len(dates) - 1:
            fresh = zdate and (np.datetime64(d) - np.datetime64(zdate)).astype(int) <= s["max_zacks_age_days"]
            cand = candidates(w, ind, zi, s, k) if fresh else {}
            if not fresh:
                print(f"zacks portfolio: Zacks file {zdate or 'missing'} is too old for {d} - no new buys")
            held = {p["ticker"] for p in pos if p["status"] in ("pending", "open", "exit next open")}
            blocked &= set(cand)                            # stopped qualifying -> may be bought again later
            for t, v in cand.items():
                if t in held or t in blocked:
                    continue
                pos.append({"ticker": t, "status": "pending", "signal_date": d, "zacks_date": zdate, **v})
                blocked.add(t)
            print(f"zacks portfolio {d}: {len(cand)} qualify, "
                  f"{sum(1 for p in pos if p['status'] == 'pending' and p['signal_date'] == d)} new buy orders")
        # 4) daily value: total $ P&L of everything bought so far
        dl = s["dollars_per_trade"]
        tot, n_open = 0.0, 0
        for p in pos:
            if p["status"] == "closed":
                tot += p["pnl"] / 100 * dl
            elif p["status"] in ("open", "exit next open") and p.get("entry"):
                cl = c[p["ticker"]].iloc[k]
                if np.isfinite(cl):
                    tot += (cl / p["entry"] - 1) * dl
                    n_open += 1
        st["daily"] = [x for x in st.get("daily", []) if x[0] != d] + [[d, round(float(tot), 2), n_open]]
        st["last_day"] = d
    # sector of every position (SPDR sector fund it belongs to)
    try:
        from .profiles import load_profiles
        pr = load_profiles()
        sec = sector_of(dict(zip(pr["ticker"], pr["sector"])))
    except Exception as e:
        print(f"zacks portfolio: no sectors ({e})")
        sec = {}
    names = CONFIG.get("sectors") or {}
    for p in pos:
        e = sec.get(p["ticker"])
        p["sector_etf"], p["sector"] = (e, names.get(e, e)) if e else (None, None)
    # mark-to-market for the website
    for p in pos:
        if p["status"] in ("open", "exit next open", "pending"):
            t = p["ticker"]
            cl = c[t].iloc[-1] if t in c else np.nan
            p["last"] = _f(cl)
            if p.get("entry") and np.isfinite(cl):
                p["pnl"] = _f((cl / p["entry"] - 1) * 100)
                p["days"] = len(dates) - 1 - dates.index(p["entry_date"])
            z = zi.get(t) or {}
            p["rank_now"], p["broker_now"] = z.get("rank"), _f(z.get("broker"))
    st["blocked"] = sorted(blocked)
    st["rules"] = {k: s[k] for k in ("max_rank", "max_broker", "min_upside_pct", "min_rs", "sell_rank",
                                     "dollars_per_trade", "cost_pct")}
    st["data_date"] = dates[-1]
    STATE.write_text(jsonsafe.dumps(st, indent=1))
    return st
