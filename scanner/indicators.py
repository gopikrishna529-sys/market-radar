"""Indicators shared by the scanner, the backtest and the website export."""
import numpy as np
import pandas as pd

from .config import CONFIG


def ema(df, span):
    return df.ewm(span=span, adjust=False, min_periods=span).mean()


def compute(w):
    """Return a dict of wide DataFrames: EMAs, trend states, RS score, average volume."""
    e = CONFIG["ema"]
    c = w["close"]
    ef, em, es = ema(c, e["fast"]), ema(c, e["mid"]), ema(c, e["slow"])
    L = e["rising_lookback"]
    long_trend = (c > ef) & (ef > em) & (em > es) & (ef > ef.shift(L)) & (em > em.shift(L))
    short_trend = (c < ef) & (ef < em) & (em < es) & (ef < ef.shift(L)) & (em < em.shift(L))

    # RS score: IBD-style weighted 3/6/9/12-month performance (uses what history exists)
    parts, weights = [], []
    for n, wgt in ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)):
        r = c / c.shift(n) - 1
        parts.append(r.fillna(0) * wgt)
        weights.append(r.notna() * wgt)
    wsum = sum(weights)
    score = (sum(parts) / wsum).where(wsum > 0)

    return {
        "ema_fast": ef, "ema_mid": em, "ema_slow": es,
        "long_trend": long_trend, "short_trend": short_trend,
        "score": score,
        "avg_vol": w["volume"].rolling(50, min_periods=20).mean(),
        "chg": c.pct_change(fill_method=None) * 100,
    }


def streak(mask):
    """Consecutive True count ending at the last row, per column."""
    rev = mask.iloc[::-1].fillna(False).astype(int)
    return rev.cumprod().sum()


def rs_rating(score_row, universe):
    """1-99 rating of each ticker's score versus the scan universe."""
    ref = np.sort(score_row[universe].dropna().values)
    if len(ref) == 0:
        return pd.Series(np.nan, index=score_row.index)
    pos = np.searchsorted(ref, score_row.values, side="right") / len(ref)
    out = pd.Series(np.round(pos * 98 + 1), index=score_row.index)
    return out.where(score_row.notna())
