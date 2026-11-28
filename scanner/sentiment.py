"""Market mood: VIX (fear index), VIX term structure and Cboe put/call ratios -> plain-English alerts.

Sources (free, published by Cboe after each session):
  VIX / VIX3M / VIX9D history: https://cdn.cboe.com/api/global/us_indices/daily_prices/<NAME>_History.csv
  Daily put/call ratios:       https://cdn.cboe.com/data/us/options/market_statistics/daily/<YYYY-MM-DD>_daily_options
Saved to data/sentiment.json (kept between runs, so put/call history builds up).
"""
import io
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sentiment.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
IDX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"
PCR = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{}_daily_options"


def _vix(name):
    r = requests.get(IDX.format(name), headers=UA, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"], format="mixed")
    return df.set_index("DATE")["CLOSE"].astype(float).iloc[-400:]


def _pcr(day):
    r = requests.get(PCR.format(day), headers=UA, timeout=30)
    if r.status_code != 200:
        return None
    js = r.json()
    rat = {x.get("name", "").upper(): x.get("value") for x in js.get("ratios") or []}
    g = lambda k: next((float(v) for n, v in rat.items() if n.startswith(k) and v not in (None, "")), None)
    out = {"total": g("TOTAL PUT/CALL"), "equity": g("EQUITY PUT/CALL"), "index": g("INDEX PUT/CALL"),
           "etp": g("EXCHANGE TRADED PRODUCTS PUT/CALL"), "spx": g("SPX")}
    return out if out["total"] else None


def update(trading_days):
    """trading_days: list of 'YYYY-MM-DD' (our price calendar). Fetches what is missing; never raises."""
    st = json.loads(OUT.read_text()) if OUT.exists() else {}
    pc = st.get("pcr", {})
    for d in trading_days[-25:]:
        if d in pc:
            continue
        try:
            v = _pcr(d)
            if v:
                pc[d] = v
        except Exception as e:
            print(f"put/call {d}: {str(e)[:80]}")
    st["pcr"] = dict(sorted(pc.items())[-260:])
    vx = {}
    for name in ("VIX", "VIX3M", "VIX9D"):
        try:
            s = _vix(name)
            vx[name] = {d.strftime("%Y-%m-%d"): round(v, 2) for d, v in s.items()}
        except Exception as e:
            print(f"{name}: download failed ({str(e)[:80]})")
            vx[name] = (st.get("vix") or {}).get(name, {})
    st["vix"] = vx
    st["summary"] = summarize(st)
    OUT.write_text(json.dumps(st, separators=(",", ":")))
    s = st["summary"]
    print(f"market mood: {s.get('mood')} · VIX {s.get('vix')} · put/call {s.get('pcr_total')} · {len(s.get('alerts', []))} alerts")
    return st


def _series(d):
    return pd.Series({pd.Timestamp(k): v for k, v in (d or {}).items()}).sort_index()


def summarize(st):
    out = {"alerts": [], "notes": [], "why": []}
    v, v3 = _series((st.get("vix") or {}).get("VIX")), _series((st.get("vix") or {}).get("VIX3M"))
    pc = st.get("pcr") or {}
    days = sorted(pc)
    score = 0                                            # + = bullish / calm, - = fearful
    if len(v) >= 21:
        now, prev = v.iloc[-1], v.iloc[-2]
        avg20 = v.iloc[-21:-1].mean()
        pctl = (v.iloc[-252:] < now).mean() * 100
        out.update(vix=round(now, 2), vix_chg=round((now / prev - 1) * 100, 1), vix_avg20=round(avg20, 2),
                   vix_pctl=round(pctl), vix_date=v.index[-1].strftime("%Y-%m-%d"),
                   vix_hist=[[k.strftime("%Y-%m-%d"), x] for k, x in v.iloc[-60:].items()])
        zone = ("calm" if now < 15 else "normal" if now < 20 else "elevated" if now < 25 else "high" if now < 30 else "panic")
        out["vix_zone"] = zone
        out["notes"].append({
            "calm": f"VIX {now:.1f} is low — markets are calm. Good for trend trades, but complacency can precede pullbacks.",
            "normal": f"VIX {now:.1f} is in its normal range (15–20).",
            "elevated": f"VIX {now:.1f} is elevated (20–25) — swings are bigger; use smaller positions.",
            "high": f"VIX {now:.1f} is high (25–30) — fear is building; be careful with new buys and tighten stops.",
            "panic": f"VIX {now:.1f} is above 30 — panic. Avoid new buys until VIX turns down; big bottoms form when it falls back from a spike."}[zone])
        pts = {"calm": 1, "normal": 1, "elevated": -1, "high": -2, "panic": -3}[zone]
        score += pts
        out["why"].append([pts, {"calm": f"Fear index is low ({now:.1f}) — markets are calm",
                                 "normal": f"Fear index is normal ({now:.1f})",
                                 "elevated": f"Fear index is elevated ({now:.1f}) — bigger daily swings",
                                 "high": f"Fear index is high ({now:.1f}) — traders are nervous",
                                 "panic": f"Fear index is in panic territory ({now:.1f})"}[zone]])
        last5 = v.iloc[-5:]
        d5 = last5.iloc[-1] - last5.iloc[0]
        out["vix_5d"] = [[k.strftime("%Y-%m-%d"), round(x, 2)] for k, x in last5.items()]
        out["vix_trend"] = "rising" if d5 > 1 else "falling" if d5 < -1 else "flat"
        out["notes"].insert(0, f"VIX over the last 5 sessions: " + " → ".join(f"{x:.1f}" for x in last5)
                            + f" ({'rising — fear growing' if d5 > 1 else 'falling — fear easing' if d5 < -1 else 'roughly flat'}).")
        if now / prev - 1 >= 0.15:
            out["alerts"].append({"level": "red", "text": f"VIX jumped {(now / prev - 1) * 100:.0f}% to {now:.1f} — a fear spike. Be cautious today."})
            score -= 1
            out["why"].append([-1, f"Fear index jumped {(now / prev - 1) * 100:.0f}% today"])
        elif now > avg20 * 1.2 and now >= 20:
            out["alerts"].append({"level": "amber", "text": f"VIX {now:.1f} is {(now / avg20 - 1) * 100:.0f}% above its 20-day average — fear is rising."})
        if last5.max() >= 28 and now <= last5.max() * 0.8:
            out["notes"].append(f"VIX has dropped {(1 - now / last5.max()) * 100:.0f}% from its recent spike ({last5.max():.1f}) — fear is easing.")
            score += 1
            out["why"].append([1, f"Fear is easing — down {(1 - now / last5.max()) * 100:.0f}% from last week's spike ({last5.max():.1f})"])
        elif prev > avg20 * 1.2 and now < prev * 0.9 and now >= 18:
            out["notes"].append("VIX is falling back after a spike — fear is fading, which often marks a short-term low.")
            score += 1
            out["why"].append([1, "Fear is easing after a spike"])
        if len(v3) and abs((v3.index[-1] - v.index[-1]).days) <= 4:          # the two files can be a day apart
            v3 = v3[v3.index <= v.index[-1]]                                  # same day as the VIX reading (or the day before)
            ts = now / v3.iloc[-1]
            al = pd.concat([v, v3], axis=1, join="inner").iloc[-60:]
            out["term_hist"] = [[k.strftime("%Y-%m-%d"), round(a / b, 3)] for k, (a, b) in al.iterrows() if b]
            out.update(vix3m=round(v3.iloc[-1], 2), term=round(ts, 3))
            if ts >= 1:
                out["alerts"].append({"level": "red", "text": f"VIX ({now:.1f}) is above 3-month VIX ({v3.iloc[-1]:.1f}) — "
                                      "near-term fear is higher than longer-term (backwardation). This happens in sell-offs; stay defensive."})
                score -= 2
                out["why"].append([-2, f"Traders fear the next few weeks more than the next 3 months ({ts:.2f}) — typical of sell-offs"])
            elif ts >= 0.92:
                out["notes"].append(f"VIX term structure is flattening ({ts:.2f}) — worth watching; above 1.00 is a stress signal.")
    if days:
        t = [pc[d].get("total") for d in days]
        e = [pc[d].get("equity") for d in days]
        now = t[-1]
        avg5 = sum(x for x in t[-5:] if x) / max(1, len([x for x in t[-5:] if x]))
        avg10 = sum(x for x in t[-10:] if x) / max(1, len([x for x in t[-10:] if x]))
        out.update(pcr_total=now, pcr_equity=e[-1], pcr_index=pc[days[-1]].get("index"), pcr_date=days[-1],
                   pcr_avg5=round(avg5, 2), pcr_avg10=round(avg10, 2), pcr_prev=t[-2] if len(t) > 1 else None,
                   pcr_hist=[[d, pc[d].get("total"), pc[d].get("equity")] for d in days[-60:]])
        if now and now > 1.0:
            out["alerts"].append({"level": "red", "text": f"Put/call ratio {now:.2f} is above 1 — more puts than calls were traded. "
                                  "Traders are buying protection / betting on a fall. Be cautious with new buys."})
            score -= 2
            out["why"].append([-2, f"More puts than calls today ({now:.2f}) — traders are hedging against a fall"])
            if len(t) > 1 and t[-2] and t[-2] <= 1.0:
                rest = out["alerts"][-1]["text"].split(" — ", 1)[1]
                out["alerts"][-1]["text"] = f"Put/call ratio crossed above 1 today ({t[-2]:.2f} → {now:.2f}). " + rest[0].upper() + rest[1:]
        elif now and now < 0.7:
            out["notes"].append(f"Put/call ratio {now:.2f} is low — lots of call buying (greed). Fine in an uptrend, but very low readings often come near short-term tops.")
            score += 0
        elif now:
            out["notes"].append(f"Put/call ratio {now:.2f} is below 1 — more calls than puts, normal optimism.")
            score += 1
            out["why"].append([1, f"More calls than puts today ({now:.2f}) — traders lean optimistic"])
        out["pcr_5d"] = [[d, pc[d].get("total")] for d in days[-5:]]
        out["notes"].insert(1 if out["notes"] else 0, "Put/call over the last 5 sessions: " + " → ".join(f"{x[1]:.2f}" for x in out["pcr_5d"] if x[1]))
        if avg5 > 1.0 and not (now and now > 1.0):
            out["alerts"].append({"level": "amber", "text": f"The 5-day average put/call ratio is {avg5:.2f} (above 1) — fear has been building all week."})
            score -= 1
            out["why"].append([-1, f"Over the past week puts outweighed calls on average ({avg5:.2f})"])
        if e[-1] and e[-1] > 0.85:
            out["notes"].append(f"Equity put/call {e[-1]:.2f} is high — stock traders are fearful. Extreme fear (above ~1.0) has often marked bottoms (contrarian).")
        elif e[-1] and e[-1] < 0.5:
            out["notes"].append(f"Equity put/call {e[-1]:.2f} is very low — heavy speculation in calls; be careful chasing.")
    out["score"] = score
    good = [w for p, w in out["why"] if p > 0]
    bad = [w for p, w in out["why"] if p < 0]
    out["checks"] = [{"ok": p > 0, "w": abs(p), "text": w} for p, w in sorted(out["why"], key=lambda x: x[0])]
    out["why_text"] = f"{len(good)} signal{'s' if len(good) != 1 else ''} look fine, {len(bad)} look worrying" if out["why"] else ""
    out["gauge"] = max(-6, min(4, score))                    # for the needle: -6 (very bearish) … +4 (very bullish)
    out["mood"] = ("Bullish" if score >= 2 else "Neutral" if score >= 0 else "Cautious" if score >= -2 else "Bearish")
    out["action"] = {"Bullish": "Conditions support taking new buy signals at normal size.",
                     "Neutral": "Mixed signals — take buys, but prefer the strongest sectors.",
                     "Cautious": "Fear is rising — smaller positions, fewer new buys, respect stops.",
                     "Bearish": "Defensive: skip most new buys and protect open positions until VIX and put/call calm down."}[out["mood"]]
    prev = st.get("summary") or {}
    if prev.get("mood") and prev.get("mood") != out["mood"] and prev.get("vix_date") != out.get("vix_date"):
        out["changed"] = f"Market mood changed from {prev['mood']} to {out['mood']}."
    else:
        out["changed"] = prev.get("changed") if prev.get("vix_date") == out.get("vix_date") else None
    out["alert_key"] = "|".join([out.get("mood", ""), out.get("vix_date") or "", out.get("pcr_date") or ""]
                                + sorted(a["text"][:40] for a in out["alerts"] if a["level"] == "red"))
    return out
