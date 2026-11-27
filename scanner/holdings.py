"""Index and sector ETF holdings (free, refreshed once a day).

State Street publishes a daily holdings spreadsheet for every SPDR fund (SPY, DIA, XLK ... XLRE, XBI, KRE ...):
    https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-<etf>.xlsx
Nasdaq-100 members come from Nasdaq's public list API.
Saved to data/reference/holdings.json as {etf: {"name", "source", "date", "tickers": [...]}}.
If a download fails the previous list is kept; funds never downloaded fall back to the sector groups in
config.yaml (matched on each stock's sector / industry) on the website.
"""
import io
import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .config import CONFIG

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reference" / "holdings.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept": "*/*"}
SSGA = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{}.xlsx"

INDEXES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow Jones 30"}
SSGA_EXTRA = ("XBI", "KRE", "XRT", "XOP")          # SPDR industry funds also in config.yaml "industries"


def funds():
    f = dict(INDEXES)
    f.update(CONFIG.get("sectors") or {})
    ind = CONFIG.get("industries") or {}
    f.update({k: v for k, v in ind.items()})
    return f


def _clean(t):
    t = str(t).strip().upper()
    if not t or t in ("NAN", "-", "CASH", "USD", "CASH_USD") or " " in t or len(t) > 7:
        return None
    return t.replace("/", ".")


def ssga(etf):
    r = requests.get(SSGA.format(etf.lower()), headers=UA, timeout=60)
    r.raise_for_status()
    raw = pd.read_excel(io.BytesIO(r.content), header=None, engine="openpyxl")
    hdr = next(i for i in range(min(30, len(raw))) if any(str(x).strip().lower() == "ticker" for x in raw.iloc[i]))
    df = raw.iloc[hdr + 1:]
    df.columns = [str(x).strip() for x in raw.iloc[hdr]]
    out = []
    for t in df["Ticker"]:
        t = _clean(t)
        if t and t not in out:
            out.append(t)
    return out


def nasdaq100():
    r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=UA, timeout=60)
    r.raise_for_status()
    rows = ((r.json().get("data") or {}).get("data") or {}).get("rows") or []
    return [t for t in (_clean(x.get("symbol")) for x in rows) if t]


def update_holdings(force=False):
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    today = date.today().isoformat()
    if not force and old and all(v.get("date") == today for v in old.values()):
        return old
    out = dict(old)
    ssga_funds = set(INDEXES) - {"QQQ"} | set(CONFIG.get("sectors") or {}) | set(SSGA_EXTRA)
    for etf, name in funds().items():
        if etf not in ssga_funds and etf != "QQQ":
            continue
        try:
            tk = nasdaq100() if etf == "QQQ" else ssga(etf)
            if len(tk) < 5:
                raise ValueError(f"only {len(tk)} holdings")
            out[etf] = {"name": name, "source": "Nasdaq" if etf == "QQQ" else "State Street", "date": today, "tickers": tk}
        except Exception as e:
            print(f"holdings {etf}: download failed ({str(e)[:100]}) - keeping the previous list")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print("holdings: " + ", ".join(f"{k} {len(v['tickers'])}" for k, v in out.items()))
    return out


def load_holdings():
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def sector_of(tickers_profile=None):
    """ticker -> sector ETF (XLK ...): SPDR holdings first, then the stock's own sector via config groups."""
    h = load_holdings()
    sec = CONFIG.get("sectors") or {}
    out = {}
    for etf in sec:
        for t in (h.get(etf) or {}).get("tickers", []):
            out.setdefault(t, etf)
    if tickers_profile is not None:
        by_name = {(g.get("sector") or "").lower(): etf for etf, g in (CONFIG.get("groups") or {}).items()
                   if etf in sec and g.get("sector")}
        for t, s in tickers_profile.items():
            if t not in out and isinstance(s, str) and s.lower() in by_name:
                out[t] = by_name[s.lower()]
    return out
