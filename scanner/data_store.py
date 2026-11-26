"""Keeps the local price history up to date.

Layout (everything lives inside the repository):
    data/daily/YYYY/YYYY-MM-DD.csv.gz   one file per trading day, whole US market, unadjusted
    data/reference/tickers.csv          common stocks + ADRs (refreshed weekly)
    data/reference/splits.csv           stock splits (used to adjust history)
    data/reference/closed_days.txt      weekdays the market was closed (holidays)
    data/meta.json                      bookkeeping
"""
import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .massive import MassiveError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAILY = DATA / "daily"
REF = DATA / "reference"
META = DATA / "meta.json"
NY = ZoneInfo("America/New_York")

BACKFILL_CALENDAR_DAYS = int(os.environ.get("BACKFILL_DAYS", "450"))  # ~310 trading days


def ny_today():
    return dt.datetime.now(NY).date()


def _load_meta():
    return json.loads(META.read_text()) if META.exists() else {}


def _save_meta(meta):
    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(meta, indent=2))


def stored_dates():
    return sorted(dt.date.fromisoformat(p.name[:10]) for p in DAILY.glob("*/*.csv.gz"))


def closed_days():
    f = REF / "closed_days.txt"
    return set(f.read_text().split()) if f.exists() else set()


def _add_closed_day(d):
    REF.mkdir(parents=True, exist_ok=True)
    days = closed_days() | {d.isoformat()}
    (REF / "closed_days.txt").write_text("\n".join(sorted(days)) + "\n")


def _day_path(d):
    return DAILY / str(d.year) / f"{d.isoformat()}.csv.gz"


def _save_day(d, results):
    df = pd.DataFrame(results)
    df = df.rename(columns={"T": "ticker", "o": "open", "h": "high", "l": "low",
                            "c": "close", "v": "volume", "vw": "vwap", "n": "trades"})
    cols = [c for c in ["ticker", "open", "high", "low", "close", "volume", "vwap", "trades"] if c in df]
    df = df[cols].dropna(subset=["close"])
    path = _day_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, compression="gzip", float_format="%.4f")
    return len(df)


def update_prices(client):
    """Download every missing trading day up to today (New York time).

    Returns the list of dates that were added.
    """
    today = ny_today()
    have = stored_dates()
    closed = closed_days()
    start = have[-1] + dt.timedelta(days=1) if have else today - dt.timedelta(days=BACKFILL_CALENDAR_DAYS)
    todo = [start + dt.timedelta(days=i) for i in range((today - start).days + 1)]
    todo = [d for d in todo if d.weekday() < 5 and d.isoformat() not in closed]
    if not have:
        print(f"No history yet - backfilling {len(todo)} weekdays "
              f"(free tier: ~{len(todo) * 13 // 60} minutes)")

    added, empty = [], []
    for i, d in enumerate(todo):
        recent = (today - d).days <= 3
        try:
            results = client.grouped_daily(d.isoformat())
        except MassiveError as e:
            if recent:
                print(f"{d}: not available yet ({e}); will retry on the next run")
                break
            print(f"{d}: skipped ({e})")
            continue
        if not results:
            if d == today:
                print(f"{d}: no data yet (or market holiday); will retry on the next run")
                break
            empty.append(d)            # holiday, unless it is simply not published yet
            if not recent:
                print(f"{d}: market closed")
                _add_closed_day(d)
            continue
        for e_day in empty:            # a later day has data, so the empty days were holidays
            print(f"{e_day}: market closed")
            _add_closed_day(e_day)
        empty = []
        n = _save_day(d, results)
        added.append(d)
        print(f"{d}: saved {n} tickers  [{i + 1}/{len(todo)}]")
    return added


def update_reference(client, force=False):
    """Refresh ticker list (weekly) and splits (every run)."""
    meta = _load_meta()
    REF.mkdir(parents=True, exist_ok=True)
    today = ny_today()

    # tickers - weekly
    last = meta.get("tickers_updated")
    if force or not last or (today - dt.date.fromisoformat(last)).days >= 7 or not (REF / "tickers.csv").exists():
        from .config import CONFIG
        rows = []
        for t in CONFIG["universe"]["ticker_types"]:
            for r in client.tickers(t):
                rows.append({"ticker": r.get("ticker"), "name": r.get("name"), "type": r.get("type"),
                             "exchange": r.get("primary_exchange")})
        if rows:
            pd.DataFrame(rows).drop_duplicates("ticker").to_csv(REF / "tickers.csv", index=False)
            meta["tickers_updated"] = today.isoformat()
            print(f"ticker list: {len(rows)} stocks")

    # splits - every run, small incremental window
    splits_file = REF / "splits.csv"
    last = meta.get("splits_updated")
    since = (dt.date.fromisoformat(last) - dt.timedelta(days=14)) if last else \
        today - dt.timedelta(days=BACKFILL_CALENDAR_DAYS + 30)
    try:
        new = client.splits(since.isoformat())
        new = pd.DataFrame(new, columns=["ticker", "execution_date", "split_from", "split_to"])
        old = pd.read_csv(splits_file) if splits_file.exists() else new.iloc[0:0]
        allsp = pd.concat([old, new]).drop_duplicates(["ticker", "execution_date"], keep="last")
        allsp.sort_values("execution_date").to_csv(splits_file, index=False)
        meta["splits_updated"] = today.isoformat()
        print(f"splits: {len(new)} fetched since {since}")
    except MassiveError as e:
        print(f"splits not updated ({e}) - continuing without new split adjustments")
    _save_meta(meta)


def load_panel(days=320):
    """Load the last `days` trading days as wide DataFrames (index=date, columns=ticker).

    Prices are adjusted for splits so EMAs and breakouts are not distorted.
    """
    files = sorted(DAILY.glob("*/*.csv.gz"))[-days:]
    if not files:
        raise RuntimeError("No price data stored yet")
    frames = []
    for f in files:
        df = pd.read_csv(f, usecols=["ticker", "high", "low", "close", "volume"])
        df["date"] = pd.Timestamp(f.name[:10])
        frames.append(df)
    long = pd.concat(frames, ignore_index=True).dropna(subset=["ticker"])
    long = long.drop_duplicates(["date", "ticker"])
    wide = {c: long.pivot(index="date", columns="ticker", values=c).sort_index()
            for c in ["high", "low", "close", "volume"]}

    splits_file = REF / "splits.csv"
    if splits_file.exists():
        sp = pd.read_csv(splits_file).dropna()
        sp = sp[sp["ticker"].isin(wide["close"].columns)]
        idx = wide["close"].index
        for row in sp.itertuples():
            if not row.split_to or not row.split_from:
                continue
            factor = row.split_from / row.split_to          # 2-for-1 -> 0.5
            before = idx < pd.Timestamp(row.execution_date)
            if not before.any() or before.all():
                continue
            for c in ["high", "low", "close"]:
                wide[c].loc[before, row.ticker] *= factor
            wide["volume"].loc[before, row.ticker] /= factor
    return wide


def load_tickers():
    f = REF / "tickers.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["ticker", "name", "type", "exchange"])
