"""Historical test of the EMA-trend strategy.

Rules (all on daily candles, split-adjusted):
  LONG   signal : EMA trend becomes set today (Close > EMA20 > EMA50 > EMA200, EMA20 & EMA50 rising)
                  and it was not set yesterday            -> buy at the NEXT day's open
         exit   : a daily close BELOW the EMA50            -> sell at the next day's open
  SHORT  signal : EMA downtrend becomes set (Close < EMA20 < EMA50 < EMA200, both falling)
                                                          -> short at the next day's open
         exit   : a daily close ABOVE the EMA50            -> cover at the next day's open
Only stocks that pass the scan filters on the signal day (price, average volume) are traded,
and only one position per stock at a time.

Two views are produced:
  * every signal  - statistics over every trade the rules generate
  * portfolio     - a realistic account with N equal slots; when more signals arrive than free slots,
                    the strongest relative strength (weakest for shorts) is taken first.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import CONFIG
from .data_store import load_tickers

OUT = Path(__file__).resolve().parent.parent / "results" / "backtest.json"

TRADE_FIELDS = ["ticker", "side", "signal", "entry_date", "entry", "exit_date", "exit", "bars",
                "pnl", "mfe", "mae", "status", "score", "in_pf"]


def _r(x, n=2):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def generate_trades(w, ind):
    bt = CONFIG["backtest"]
    u = CONFIG["universe"]
    cost = bt.get("cost_per_side_pct", 0.0) / 100.0
    sides = bt.get("sides", ["long", "short"])
    dates = w["close"].index
    n = len(dates)

    tick = set(load_tickers()["ticker"])
    cols = [t for t in w["close"].columns if t in tick]
    elig = (w["close"][cols] >= u["min_price"]) & (ind["avg_vol"][cols] >= u["min_avg_volume"])
    L, S = ind["long_trend"][cols], ind["short_trend"][cols]
    long_sig = L & ~L.shift(1, fill_value=False) & elig
    short_sig = S & ~S.shift(1, fill_value=False) & elig

    O, H, Lo, C = (w[k][cols].to_numpy() for k in ("open", "high", "low", "close"))
    EM = ind["ema_mid"][cols].to_numpy()
    SC = ind["score"][cols].to_numpy()
    LS, SS = long_sig.to_numpy(), short_sig.to_numpy()

    trades, pending = [], []
    for j, t in enumerate(cols):
        sig = []
        if "long" in sides:
            sig += [(i, 1) for i in np.flatnonzero(LS[:, j])]
        if "short" in sides:
            sig += [(i, -1) for i in np.flatnonzero(SS[:, j])]
        if not sig:
            continue
        sig.sort()
        free_from = 0
        o, h, lo, c, em = O[:, j], H[:, j], Lo[:, j], C[:, j], EM[:, j]
        for i, side in sig:
            if i < free_from:
                continue
            e = i + 1
            if e >= n:                                     # signal on the last day: entry is at the next open
                pending.append({"ticker": t, "side": "long" if side == 1 else "short",
                                "score": SC[i, j], "close": c[i]})
                continue
            if not np.isfinite(o[e]):
                continue
            entry = o[e] * (1 + cost * side)
            beyond = (c[e:] < em[e:]) if side == 1 else (c[e:] > em[e:])
            hit = np.flatnonzero(beyond)
            if len(hit):
                u_i = e + hit[0]                           # day of the exit signal (close beyond EMA50)
                x = u_i + 1
                if x < n and np.isfinite(o[x]):
                    exit_px, exit_i, status, last_i = o[x], x, "closed", u_i
                else:
                    exit_px, exit_i, status, last_i = c[u_i], u_i, "exit next open", u_i
            else:
                last_i = n - 1
                exit_px, exit_i, status = c[last_i], last_i, "open"
            exit_eff = exit_px * (1 - cost * side)
            hh = np.nanmax(h[e:last_i + 1]); ll = np.nanmin(lo[e:last_i + 1])
            if status == "closed":
                hh, ll = max(hh, exit_px), min(ll, exit_px)
            if side == 1:
                pnl, mfe, mae = exit_eff / entry - 1, hh / entry - 1, ll / entry - 1
            else:
                pnl, mfe, mae = (entry - exit_eff) / entry, (entry - ll) / entry, (entry - hh) / entry
            trades.append({
                "ticker": t, "side": "long" if side == 1 else "short",
                "signal_i": i, "entry_i": e, "exit_i": exit_i, "col": j,
                "entry": entry, "exit": exit_px, "bars": exit_i - e,
                "pnl": pnl * 100, "mfe": mfe * 100, "mae": mae * 100,
                "status": status, "score": SC[i, j], "in_pf": "",
            })
            free_from = exit_i if status == "closed" else n
    return trades, cols, C, O, pending


def simulate(trades, w, C, O, sides, max_pos, cost):
    """Equal-slot portfolio. Returns daily equity (start 100) and invested fraction."""
    dates = w["close"].index
    n = len(dates)
    by_entry = {}
    for k, tr in enumerate(trades):
        if tr["side"] in sides:
            by_entry.setdefault(tr["entry_i"], []).append(k)
    if not by_entry:
        return None
    start = min(by_entry) - 1
    cash, equity_prev = 100.0, 100.0
    pos = {}                     # trade index -> (alloc, last_value)
    eq, inv, taken = [], [], set()
    for d in range(start, n):
        # 1) exits at today's open
        for k in [k for k in pos if trades[k]["exit_i"] == d and trades[k]["status"] == "closed"]:
            tr = trades[k]
            alloc, _ = pos.pop(k)
            cash += alloc * (1 + tr["pnl"] / 100)
        # 2) entries at today's open
        cands = by_entry.get(d, [])
        if cands and len(pos) < max_pos:
            def strength(k):                 # strongest RS first for longs, weakest first for shorts
                s = trades[k]["score"]
                if not np.isfinite(s):
                    return -1e9
                return s if trades[k]["side"] == "long" else -s
            cands = sorted(cands, key=strength, reverse=True)
            for k in cands:
                if len(pos) >= max_pos:
                    break
                alloc = min(equity_prev / max_pos, cash)
                if alloc < equity_prev / max_pos * 0.5:
                    break
                cash -= alloc
                pos[k] = (alloc, alloc)
                taken.add(k)
        # 3) mark to market at today's close
        value = 0.0
        for k, (alloc, last) in pos.items():
            tr = trades[k]
            px = C[d, tr["col"]]
            if np.isfinite(px):
                r = px / tr["entry"] if tr["side"] == "long" else 2 - px / tr["entry"]
                last = alloc * r
                pos[k] = (alloc, last)
            value += last
        equity_prev = cash + value
        eq.append(equity_prev)
        inv.append(value / equity_prev if equity_prev > 0 else 0)
    return {"start": start, "equity": np.array(eq), "invested": np.array(inv), "taken": taken}


def equity_stats(eq, dates, invested=None):
    ret = pd.Series(eq, index=dates).pct_change().dropna()
    years = max(len(eq) - 1, 1) / 252
    total = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else None
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    mdd = dd.min()
    # longest drawdown (days below previous peak)
    longest = cur = 0
    for v in dd:
        cur = cur + 1 if v < 0 else 0
        longest = max(longest, cur)
    sd = ret.std()
    down = ret[ret < 0].std()
    return {
        "total": _r(total * 100), "cagr": _r(cagr * 100) if cagr is not None else None,
        "max_dd": _r(mdd * 100), "longest_dd_days": int(longest),
        "sharpe": _r(ret.mean() / sd * math.sqrt(252)) if sd and sd > 0 else None,
        "sortino": _r(ret.mean() / down * math.sqrt(252)) if down and down > 0 else None,
        "vol": _r(sd * math.sqrt(252) * 100) if sd == sd else None,
        "calmar": _r(cagr / abs(mdd)) if cagr is not None and mdd < 0 else None,
        "best_day": _r(ret.max() * 100), "worst_day": _r(ret.min() * 100),
        "exposure": _r(float(np.mean(invested)) * 100) if invested is not None else None,
        "days": int(len(eq)),
    }, dd * 100


def trade_stats(tr):
    closed = [t for t in tr if t["status"] == "closed"]
    out = {"trades": len(tr), "closed": len(closed), "open": len(tr) - len(closed)}
    if not closed:
        return out
    p = np.array([t["pnl"] for t in closed])
    wins, losses = p[p > 0], p[p <= 0]
    mfe = np.array([t["mfe"] for t in closed]); mae = np.array([t["mae"] for t in closed])
    bars = np.array([t["bars"] for t in closed])
    streak_w = streak_l = cw = cl = 0
    for t in sorted(closed, key=lambda t: t["exit_i"]):
        if t["pnl"] > 0:
            cw += 1; cl = 0
        else:
            cl += 1; cw = 0
        streak_w, streak_l = max(streak_w, cw), max(streak_l, cl)
    gross_w, gross_l = wins.sum(), -losses.sum()
    out.update({
        "win_rate": _r(len(wins) / len(p) * 100, 1),
        "avg_pnl": _r(p.mean()), "median_pnl": _r(np.median(p)),
        "avg_win": _r(wins.mean()) if len(wins) else None,
        "avg_loss": _r(losses.mean()) if len(losses) else None,
        "payoff": _r(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None,
        "profit_factor": _r(gross_w / gross_l) if gross_l > 0 else None,
        "expectancy": _r(p.mean()),
        "best": _r(p.max()), "worst": _r(p.min()),
        "avg_bars": _r(bars.mean(), 1), "avg_bars_win": _r(bars[p > 0].mean(), 1) if len(wins) else None,
        "avg_bars_loss": _r(bars[p <= 0].mean(), 1) if len(losses) else None,
        "avg_mfe": _r(mfe.mean()), "avg_mae": _r(mae.mean()),
        "avg_mfe_losers": _r(mfe[p <= 0].mean()) if len(losses) else None,
        "avg_mae_winners": _r(mae[p > 0].mean()) if len(wins) else None,
        "capture": _r(p[mfe > 0].mean() / mfe[mfe > 0].mean() * 100, 1) if (mfe > 0).any() else None,
        "pct_mfe_over_10": _r((mfe >= 10).mean() * 100, 1),
        "max_win_streak": int(streak_w), "max_loss_streak": int(streak_l),
    })
    return out


def run_backtest(w, ind):
    bt = CONFIG["backtest"]
    cost = bt.get("cost_per_side_pct", 0.0) / 100.0
    max_pos = int(bt.get("max_positions", 20))
    trades, cols, C, O, pending = generate_trades(w, ind)
    dates = w["close"].index
    spy = w["close"][CONFIG["relative_strength"]["benchmark"]]

    modes = {}
    for mode, sides in (("both", ("long", "short")), ("long", ("long",)), ("short", ("short",))):
        sim = simulate(trades, w, C, O, sides, max_pos, cost)
        if not sim:
            continue
        d = dates[sim["start"]:]
        eq = sim["equity"]
        stats, dd = equity_stats(eq, d, sim["invested"])
        s = spy.reindex(d).ffill()
        spy_eq = (s / s.iloc[0] * 100).to_numpy()
        spy_stats, _ = equity_stats(spy_eq, d)
        pf_trades = [trades[k] for k in sim["taken"]]
        for k in sim["taken"]:                 # b = in the long+short portfolio, l = long-only, s = short-only
            trades[k]["in_pf"] += mode[0]
        eqs = pd.Series(eq, index=d)
        monthly = (eqs.resample("ME").last() / eqs.resample("ME").last().shift(1).fillna(eqs.iloc[0]) - 1) * 100
        yearly = (eqs.resample("YE").last() / eqs.resample("YE").last().shift(1).fillna(eqs.iloc[0]) - 1) * 100
        modes[mode] = {
            "dates": [x.strftime("%Y-%m-%d") for x in d],
            "equity": [_r(x) for x in eq],
            "spy": [_r(x) for x in spy_eq],
            "drawdown": [_r(x) for x in dd],
            "invested": [_r(x * 100, 1) for x in sim["invested"]],
            "stats": stats, "spy_stats": spy_stats,
            "trade_stats": trade_stats(pf_trades),
            "monthly": {k.strftime("%Y-%m"): _r(v) for k, v in monthly.items()},
            "yearly": {k.strftime("%Y"): _r(v) for k, v in yearly.items()},
        }

    every = {
        "both": trade_stats(trades),
        "long": trade_stats([t for t in trades if t["side"] == "long"]),
        "short": trade_stats([t for t in trades if t["side"] == "short"]),
    }
    ds = [x.strftime("%Y-%m-%d") for x in dates]
    rows = []
    for t in sorted(trades, key=lambda t: (t["entry_i"], t["ticker"])):
        rows.append([t["ticker"], t["side"], ds[t["signal_i"]], ds[t["entry_i"]], _r(t["entry"]),
                     ds[t["exit_i"]], _r(t["exit"]), int(t["bars"]), _r(t["pnl"]), _r(t["mfe"]), _r(t["mae"]),
                     t["status"], _r(t["score"] * 100 if np.isfinite(t["score"]) else None, 1), t["in_pf"]])
    first_signal = min((t["signal_i"] for t in trades), default=None)
    out = {
        "generated": ds[-1],
        "data_start": ds[0], "data_end": ds[-1],
        "first_signal": ds[first_signal] if first_signal is not None else None,
        "settings": {
            "ema": CONFIG["ema"], "max_positions": max_pos, "cost_per_side_pct": bt.get("cost_per_side_pct", 0),
            "min_price": CONFIG["universe"]["min_price"], "min_avg_volume": CONFIG["universe"]["min_avg_volume"],
            "stocks_tested": len(cols),
        },
        "modes": modes, "every_signal": every,
        "fields": TRADE_FIELDS, "trades": rows,
        "next_open": {
            "enter": [{"ticker": p["ticker"], "side": p["side"], "close": _r(p["close"]),
                       "score": _r(p["score"] * 100 if np.isfinite(p["score"]) else None, 1)}
                      for p in sorted(pending, key=lambda p: -(p["score"] if np.isfinite(p["score"]) else -9)
                                      * (1 if p["side"] == "long" else -1))],
            "exit": [{"ticker": t["ticker"], "side": t["side"], "entry_date": ds[t["entry_i"]],
                      "pnl": _r(t["pnl"]), "in_pf": t["in_pf"]}
                     for t in trades if t["status"] == "exit next open"],
        },
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    m = modes.get("both", {}).get("stats", {})
    print(f"backtest: {len(trades)} trades on {len(cols)} stocks, {ds[first_signal] if first_signal is not None else '-'}"
          f" to {ds[-1]}; portfolio return {m.get('total')}%, max DD {m.get('max_dd')}%")
    return out
