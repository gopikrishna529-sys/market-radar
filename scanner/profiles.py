"""Company profiles: sector, industry, market cap, country (free sources, refreshed daily).

1st choice : Nasdaq stock screener - one request returns every US stock with sector, industry, market cap.
Fallback   : SEC company data - official industry (SIC) per company, ~10 minutes, used only if Nasdaq fails
             and there is no recent profile file.
Exchange comes from the Massive ticker list. ETFs get sector "ETF".
"""
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import requests

REF = Path(__file__).resolve().parent.parent / "data" / "reference"
OUT = REF / "profiles.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36", "Accept": "application/json, text/plain, */*",
      "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}


def _num(x):
    try:
        return float(str(x).replace("$", "").replace(",", "").strip() or "nan")
    except ValueError:
        return float("nan")


def from_nasdaq():
    r = requests.get("https://api.nasdaq.com/api/screener/stocks",
                     params={"tableonly": "true", "limit": "25000", "offset": "0", "download": "true"},
                     headers=UA, timeout=60)
    r.raise_for_status()
    rows = (r.json().get("data") or {}).get("rows") or []
    if len(rows) < 3000:
        raise RuntimeError(f"Nasdaq screener returned only {len(rows)} rows")
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "ticker": df["symbol"].str.strip().str.replace("/", ".", regex=False).str.replace("^", "p", regex=False),
        "sector": df.get("sector", "").replace("", "Other").fillna("Other"),
        "industry": df.get("industry", "").replace("", "Other").fillna("Other"),
        "market_cap": df.get("marketCap", "").map(_num),
        "country": df.get("country", "").fillna(""),
        "ipo_year": df.get("ipoyear", "").fillna(""),
        "source": "nasdaq",
    })
    return out


SIC_SECTOR = [  # SIC code ranges -> broad sector (used only by the SEC fallback)
    (100, 999, "Basic Materials"), (1000, 1499, "Basic Materials"), (1300, 1399, "Energy"),
    (1500, 1799, "Industrials"), (2000, 2199, "Consumer Staples"), (2200, 2799, "Consumer Discretionary"),
    (2800, 2836, "Health Care"), (2837, 2899, "Basic Materials"), (2900, 2999, "Energy"),
    (3000, 3569, "Industrials"), (3570, 3579, "Technology"), (3580, 3599, "Industrials"),
    (3600, 3699, "Technology"), (3700, 3799, "Industrials"), (3800, 3851, "Health Care"),
    (3852, 3999, "Industrials"), (4000, 4799, "Industrials"), (4800, 4899, "Telecommunications"),
    (4900, 4999, "Utilities"), (5000, 5199, "Industrials"), (5200, 5999, "Consumer Discretionary"),
    (6000, 6499, "Finance"), (6500, 6799, "Real Estate"), (7000, 7369, "Consumer Discretionary"),
    (7370, 7379, "Technology"), (7380, 7999, "Consumer Discretionary"), (8000, 8099, "Health Care"),
    (8100, 8999, "Industrials"),
]


def _sic_sector(code):
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "Other"
    hit = "Other"
    for a, b, name in SIC_SECTOR:      # later, narrower ranges override earlier ones
        if a <= c <= b:
            hit = name
    return hit


def from_sec(tickers, email="market-radar@users.noreply.github.com"):
    h = {"User-Agent": f"Market Radar {email}"}
    m = requests.get("https://www.sec.gov/files/company_tickers_exchange.json", headers=h, timeout=60).json()
    cik = {row[2]: row[0] for row in m["data"]}            # ticker -> CIK
    rows, todo = [], [t for t in tickers if t.replace(".", "-") in cik or t in cik]
    print(f"profiles: SEC fallback for {len(todo)} companies (~{len(todo) // 8 // 60 + 1} min)")
    for t in todo:
        c = cik.get(t, cik.get(t.replace(".", "-")))
        try:
            j = requests.get(f"https://data.sec.gov/submissions/CIK{int(c):010d}.json", headers=h, timeout=30).json()
            rows.append({"ticker": t, "sector": _sic_sector(j.get("sic")),
                         "industry": (j.get("sicDescription") or "Other").title(), "market_cap": float("nan"),
                         "country": j.get("addresses", {}).get("business", {}).get("stateOrCountryDescription", ""),
                         "ipo_year": "", "source": "sec"})
        except Exception:
            pass
        time.sleep(0.13)                                     # SEC allows 10 requests a second
    return pd.DataFrame(rows)


def update_profiles(tickers_df, meta):
    """Refresh profiles.csv (daily from Nasdaq; SEC only as a fallback)."""
    today = dt.date.today().isoformat()
    if meta.get("profiles_updated") == today and OUT.exists():
        return
    try:
        df = from_nasdaq()
        print(f"profiles: {len(df)} companies from the Nasdaq screener")
    except Exception as e:
        print(f"profiles: Nasdaq screener unavailable ({str(e)[:120]})")
        last = meta.get("profiles_updated")
        if OUT.exists() and last and (dt.date.today() - dt.date.fromisoformat(last)).days < 30:
            print("profiles: keeping the existing file")
            return
        try:
            stocks = tickers_df[tickers_df["type"].isin(["CS", "ADRC"])]["ticker"].tolist()
            df = from_sec(stocks)
        except Exception as e2:
            print(f"profiles: SEC fallback failed too ({str(e2)[:120]}) - sectors stay as before")
            return
    REF.mkdir(parents=True, exist_ok=True)
    df.drop_duplicates("ticker").to_csv(OUT, index=False)
    meta["profiles_updated"] = today


EXCHANGES = {"XNAS": "Nasdaq", "XNYS": "NYSE", "ARCX": "NYSE Arca", "XASE": "NYSE American",
             "BATS": "Cboe BZX", "IEXG": "IEX", "XNCM": "Nasdaq", "XNGS": "Nasdaq", "XNMS": "Nasdaq"}


def load_profiles():
    return pd.read_csv(OUT, dtype={"ticker": str}) if OUT.exists() else pd.DataFrame(
        columns=["ticker", "sector", "industry", "market_cap", "country", "ipo_year", "source"])
