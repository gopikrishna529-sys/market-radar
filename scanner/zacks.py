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


def _files():
    if not ZDIR.exists():
        return []
    return sorted(ZDIR.glob("zacks_rank_??-??-????.csv"), key=_day)


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


def load_latest():
    """Latest screen plus the tickers that were not in the previous day's screen."""
    files = _files()
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
