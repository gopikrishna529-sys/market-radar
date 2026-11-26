"""Read the Zacks screen CSVs saved by zacks_fetch.py (data/zacks/YYYY-MM-DD.csv)."""
import csv
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZDIR = ROOT / "data" / "zacks"


def _day(path):
    """zacks_rank_27-11-2026.csv -> 2026-11-27"""
    d, m, y = path.stem.rsplit("_", 1)[-1].split("-")
    return f"{y}-{m}-{d}"


def _files(prefix="zacks_rank"):
    if not ZDIR.exists():
        return []
    return sorted(ZDIR.glob(f"{prefix}_??-??-????.csv"), key=_day)


def _read(path):
    text = path.read_text(errors="replace").lstrip("﻿")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return None
    head = [h.strip() for h in rows[0]]
    ti = next((i for i, h in enumerate(head) if h.lower() in ("ticker", "symbol")), 0)
    body = []
    for r in rows[1:]:
        r = (r + [""] * len(head))[:len(head)]
        t = r[ti].strip().upper()
        if t:
            r[ti] = t
            body.append([_num(v) for v in r])
    return {"date": _day(path), "columns": head, "ticker_col": ti, "rows": body}


def _num(v):
    s = v.strip()
    try:
        x = float(s.replace(",", "").replace("%", "").replace("$", ""))
        return int(x) if x.is_integer() and "." not in s else x
    except ValueError:
        return s


def load_latest(prefix="zacks_rank"):
    """Latest screen plus the tickers that were not in the previous day's screen."""
    files = _files(prefix)
    if not files:
        return None
    cur = _read(files[-1])
    if not cur:
        return None
    prev = _read(files[-2]) if len(files) > 1 else None
    prev_set = {r[prev["ticker_col"]] for r in prev["rows"]} if prev else set()
    cur["new"] = sorted({r[cur["ticker_col"]] for r in cur["rows"]} - prev_set) if prev else []
    cur["prev_date"] = prev["date"] if prev else None
    return cur


def _screens():
    from .config import CONFIG
    zc = CONFIG.get("zacks") or {}
    pre = [x.get("file") or "zacks_rank" for x in (zc.get("screens") or [])] or ["zacks_rank"]
    return zc, list(dict.fromkeys(pre))


def _find(cols, *need, avoid=()):
    for i, c in enumerate(cols):
        l = c.lower()
        if all(n in l for n in need) and not any(a in l for a in avoid):
            return i
    return None


RANK_TXT = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}


def broker_txt(x):
    return ("Strong Buy" if x < 1.5 else "Buy" if x < 2.5 else "Hold" if x < 3.5 else "Sell" if x < 4.5 else "Strong Sell")


def load_info():
    """Per-ticker Zacks facts merged from the newest file of every configured screen:
    {ticker: {rank, value, growth, momentum, vgm, broker, upside, target, zclose, date}}"""
    zc, prefixes = _screens()
    words = [w.lower() for w in (zc.get("chart_levels") or ["target"])]
    info = {}
    for pre in prefixes:
        z = load_latest(pre)
        if not z:
            continue
        cols, ti = z["columns"], z["ticker_col"]
        ix = {"rank": _find(cols, "zacks rank", avoid=("industry",)),
              "value": _find(cols, "value score"), "growth": _find(cols, "growth score"),
              "momentum": _find(cols, "momentum score"), "vgm": _find(cols, "vgm score"),
              "broker": _find(cols, "broker rec"), "upside": _find(cols, "upside", "target"),
              "zclose": _find(cols, "last close"),
              "target_exact": _find(cols, "target price", avoid=("upside", "%"))}
        price_cols = [(i, c) for i, c in enumerate(cols) if i not in (ti, ix["target_exact"])
                      and any(w in c.lower() for w in words) and "%" not in c and "upside" not in c.lower()]
        for r in z["rows"]:
            d = info.setdefault(r[ti], {})
            d["date"] = max(d.get("date", ""), z["date"])
            for k, i in ix.items():
                if i is not None and i < len(r) and r[i] not in ("", None, "NA", "N/A"):
                    d[k] = r[i]
            for i, c in price_cols:
                v = r[i] if i < len(r) else None
                if isinstance(v, (int, float)) and v > 0:
                    d.setdefault("levels", []).append([c, round(float(v), 2)])
    for t, d in info.items():
        if isinstance(d.get("rank"), (int, float)):
            d["rank"] = int(d["rank"])
            d["rank_txt"] = RANK_TXT.get(d["rank"], "")
        if isinstance(d.get("broker"), (int, float)):
            d["broker_txt"] = broker_txt(d["broker"])
        ex = d.pop("target_exact", None)
        up, zcl = d.get("upside"), d.get("zclose")
        if isinstance(ex, (int, float)) and ex > 0:          # Zacks' own Average Target Price
            d["target"] = round(float(ex), 2)
        elif isinstance(up, (int, float)) and isinstance(zcl, (int, float)) and zcl > 0:
            d["target"] = round(zcl * (1 + up / 100), 2)      # fallback: worked out from the upside %
    return info
