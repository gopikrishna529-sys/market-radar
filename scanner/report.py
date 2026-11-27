"""Builds the daily email and the static website."""
import json
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

from .config import CONFIG

ROOT = Path(__file__).resolve().parent.parent


def fmt_date(iso):
    """2026-11-25 -> 25-Nov-26 (the format used everywhere in emails and on the website)."""
    import datetime as _dt
    return _dt.date.fromisoformat(iso[:10]).strftime("%d-%b-%y")


# ---------------------------------------------------------------- email
def _color(x):
    if x is None:
        return "#666"
    return "#0a7d38" if x > 0 else "#c62828" if x < 0 else "#666"


def _fmt(x, suffix=""):
    return "–" if x is None else f"{x:+.2f}{suffix}" if suffix == "%" else f"{x}{suffix}"


def _stock_table(rows, limit, extra=None):
    if not rows:
        return '<p style="color:#666;margin:4px 0 16px">None today.</p>'
    th = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;font-size:12px;color:#555"'
    td = 'style="padding:5px 8px;border-bottom:1px solid #eee;font-size:13px"'
    head = ["Ticker", "Name", "Close", "Chg %", "RS", "Vol x", "Days in trend"]
    out = [f'<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:6px"><tr>'
           + "".join(f"<th {th}>{h}</th>" for h in head) + "</tr>"]
    for r in rows[:limit]:
        star = " ★" if r.get("rs_high") else ""
        out.append(
            f"<tr><td {td}><b>{escape(r['ticker'])}</b>{star}</td>"
            f"<td {td}>{escape((r.get('name') or '')[:28])}</td>"
            f"<td {td}>{r['close']}</td>"
            f'<td {td}><span style="color:{_color(r["chg"])}">{_fmt(r["chg"], "%")}</span></td>'
            f"<td {td}>{r['rs'] if r['rs'] is not None else '–'}</td>"
            f"<td {td}>{r['vol_ratio'] if r['vol_ratio'] is not None else '–'}</td>"
            f"<td {td}>{r['days_trend']}</td></tr>")
    out.append("</table>")
    if len(rows) > limit:
        out.append(f'<p style="font-size:12px;color:#666;margin:0 0 16px">+ {len(rows) - limit} more on the website</p>')
    return "".join(out)


def _sector_table(rows):
    th = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;font-size:12px;color:#555"'
    td = 'style="padding:5px 8px;border-bottom:1px solid #eee;font-size:13px"'
    qcol = {"Leading": "#0a7d38", "Improving": "#1565c0", "Weakening": "#ef6c00", "Lagging": "#c62828"}
    out = ['<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:16px"><tr>'
           + "".join(f"<th {th}>{h}</th>" for h in ["#", "Sector", "vs SPY 1W", "1M", "3M", "Quadrant", "Rank move"])
           + "</tr>"]
    for r in rows:
        mv = r["rank_change"]
        arrow = f'<span style="color:#0a7d38">▲{mv}</span>' if mv > 0 else \
            f'<span style="color:#c62828">▼{-mv}</span>' if mv < 0 else "–"
        out.append(
            f"<tr><td {td}>{r['rank']}</td><td {td}><b>{r['ticker']}</b> {escape(r['name'])}</td>"
            + "".join(f'<td {td}><span style="color:{_color(r[k])}">{_fmt(r[k], "%")}</span></td>'
                      for k in ["rel_1w", "rel_1m", "rel_3m"])
            + f'<td {td}><span style="color:{qcol[r["quadrant"]]};font-weight:600">{r["quadrant"]}</span></td>'
            f"<td {td}>{arrow}</td></tr>")
    out.append("</table>")
    return "".join(out)


def build_email(result, bt=None, zp=None):
    lim = CONFIG["report"]["max_rows_email"]
    b = result["benchmark"]
    site = os.environ.get("SITE_URL", "")
    h2 = 'style="font-size:16px;margin:22px 0 6px;border-left:4px solid #1565c0;padding-left:8px"'
    rotating_in = [r for r in result["sectors"] + result["industries"] if r["quadrant"] == "Improving"]
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:820px;color:#222">',
        f'<h1 style="font-size:20px;margin:0 0 4px">Market Radar — {fmt_date(result["date"])}</h1>',
        f'<p style="margin:0 0 4px;color:#444">{b["ticker"]} {b["close"]} '
        f'<span style="color:{_color(b["chg"])}">{_fmt(b["chg"], "%")}</span> · '
        f'{result["universe_size"]} stocks scanned · {result["breadth"]["pct_in_trend"]}% in EMA trend · '
        f'{result["breadth"]["pct_above_ema200"]}% above 200 EMA</p>',
        f'<p style="margin:0 0 8px"><a href="{site}">Open the full website →</a></p>' if site else "",
        _zport_section(zp, h2, site),
        _strategy_section(bt, h2, site),
        _zacks_section(result, h2, lim),
        f'<h2 {h2}>🚀 EMA trend + {CONFIG["breakout"]["lookback_days"]}-day breakout ({len(result["breakouts"])})</h2>',
        _stock_table(result["breakouts"], lim),
        f'<h2 {h2}>🔻 EMA downtrend + {CONFIG["breakout"]["lookback_days"]}-day breakdown ({len(result.get("breakdowns", []))}, weakest RS first)</h2>',
        _stock_table(result.get("breakdowns", []), lim),
    ]
    if b["chg"] is not None and b["chg"] < 0:
        parts += [f'<h2 {h2}>💪 Up while SPY was down ({len(result["rs_days"])})</h2>',
                  _stock_table(result["rs_days"], lim)]
    else:
        parts += [f'<h2 {h2}>💪 Up while SPY was down</h2>',
                  '<p style="color:#666;margin:4px 0 16px">SPY was not down today.</p>']
    parts += [
        f'<h2 {h2}>⭐ RS leaders — RS rating ≥ {CONFIG["relative_strength"]["leaders_min_rating"]} '
        f'and RS line at 50-day high ({len(result["rs_leaders"])})</h2>',
        _stock_table(result["rs_leaders"], lim),
        f'<h2 {h2}>🔄 Sector rotation</h2>',
        ('<p style="margin:0 0 6px">Rotating in (Improving): <b>'
         + ", ".join(f'{r["ticker"]} {escape(r["name"])}' for r in rotating_in) + "</b></p>") if rotating_in else "",
        _sector_table(result["sectors"]),
        f'<h2 {h2}>🏭 Industry groups</h2>',
        _sector_table(result["industries"]),
        f'<h2 {h2}>📈 In EMA trend ({len(result["trend"])}, top {lim} by RS)</h2>',
        _stock_table(result["trend"], lim),
        '<p style="font-size:11px;color:#888;margin-top:20px">★ = RS line at a 50-day high. '
        'RS = relative strength rating 1–99 vs all scanned stocks. Vol x = volume vs 50-day average. '
        'Not financial advice.</p></div>',
    ]
    subject = (f"Market Radar {fmt_date(result['date'])}: {len(result['breakouts'])} breakouts, "
               f"{len(result.get('breakdowns', []))} breakdowns, "
               f"{len(result['rs_days'])} RS-day, {len(result['rs_leaders'])} RS leaders")
    return subject, "".join(parts)


def _zacks_section(result, h2, lim):
    """Stocks from the saved Zacks screen that are also in the EMA uptrend (breakouts first)."""
    try:
        from .zacks import load_latest
        z = load_latest()
    except Exception as e:
        print("zacks section skipped:", e)
        return ""
    if not z:
        return ""
    ti = z["ticker_col"]
    zset = {r[ti] for r in z["rows"]}
    new = set(z["new"])
    bo = {r["ticker"] for r in result["breakouts"]}
    rows = [dict(r, name=("🆕 " if r["ticker"] in new else "") + ("🚀 " if r["ticker"] in bo else "") + (r.get("name") or ""))
            for r in result["trend"] if r["ticker"] in zset]
    rows.sort(key=lambda r: (r["ticker"] not in bo, -(r["rs"] or 0)))
    return (f'<h2 {h2}>🎯 Zacks screen ({fmt_date(z["date"])}): {len(zset)} stocks, {len(rows)} in EMA uptrend</h2>'
            '<p style="font-size:12px;color:#666;margin:0 0 6px">🚀 = 20-day breakout today · 🆕 = new in the screen'
            + (f' since {fmt_date(z["prev_date"])}' if z["prev_date"] else "") + "</p>"
            + _stock_table(rows, lim))


def _zport_section(zp, h2, site):
    """Zacks portfolio: today's buy and sell orders, and how the open positions are doing."""
    if not zp:
        return ""
    P = zp.get("positions", [])
    day = zp.get("last_day")
    buys = [p for p in P if p["status"] == "pending" and p.get("signal_date") == day]
    sells = [p for p in P if p["status"] == "exit next open" and p.get("exit_signal") == day]
    opn = [p for p in P if p["status"] in ("open", "exit next open")]
    closed = [p for p in P if p["status"] == "closed"]
    r = zp.get("rules", {})

    def b(p):
        return (f'<b>{escape(p["ticker"])}</b> (#{p["rank"]}, brokers {p["broker"]}, '
                f'target +{p["upside"]}%, RS {p["rs"]})')
    sell_txt = ", ".join(f'<b>{escape(p["ticker"])}</b> (<span style="color:{_color(p.get("pnl"))}">'
                         f'{_fmt(p.get("pnl"), "%")}</span>, {escape(p.get("reason", ""))})' for p in sells) or "none"
    avg = sum(p["pnl"] for p in opn if p.get("pnl") is not None) / max(1, sum(p.get("pnl") is not None for p in opn))
    wins = sum(p["pnl"] > 0 for p in closed)
    return (f'<h2 {h2}>🎯 Zacks portfolio — orders for the next open</h2>'
            f'<p style="margin:2px 0">Buy: {", ".join(b(p) for p in buys) or "none"}</p>'
            f'<p style="margin:2px 0 8px">Sell: {sell_txt}</p>'
            f'<p style="margin:0 0 16px;font-size:12px;color:#555">Tracking since {fmt_date(zp.get("start"))} · '
            f'{len(opn)} open (average <span style="color:{_color(avg)}">{_fmt(avg, "%")}</span>) · '
            f'{len(closed)} closed, {wins} winners · rules: Zacks Rank ≤ {r.get("max_rank")}, brokers &lt; {r.get("max_broker")}, '
            f'target ≥ +{r.get("min_upside_pct")}%, EMA uptrend, RS ≥ {r.get("min_rs")}'
            + (f' · <a href="{site}#zpf">details</a>' if site else "") + "</p>")


def _strategy_section(bt, h2, site):
    if not bt:
        return ""
    nx = bt.get("next_open", {})
    m = (bt.get("modes", {}).get("both") or {})
    st, sp = m.get("stats", {}), m.get("spy_stats", {})
    longs = [x for x in nx.get("enter", []) if x["side"] == "long"]
    shorts = [x for x in nx.get("enter", []) if x["side"] == "short"]
    exits = [x for x in nx.get("exit", []) if "b" in (x.get("in_pf") or "")]   # portfolio positions

    def names(rows, k=25):
        s = ", ".join(f'<b>{escape(r["ticker"])}</b>' for r in rows[:k])
        return s + (f" … +{len(rows) - k} more" if len(rows) > k else "") if rows else "none"
    exit_txt = ", ".join(f'<b>{escape(r["ticker"])}</b> ({r["side"]}, '
                         f'<span style="color:{_color(r["pnl"])}">{_fmt(r["pnl"], "%")}</span>)'
                         for r in exits[:30]) or "none"
    since = fmt_date(m["dates"][0]) if m.get("dates") else "–"
    return (f'<h2 {h2}>📋 Strategy — orders for the next open</h2>'
            f'<p style="margin:2px 0">Buy (EMA trend just set, strongest RS first): {names(longs)}</p>'
            f'<p style="margin:2px 0">Short (EMA downtrend just set): {names(shorts)}</p>'
            f'<p style="margin:2px 0 8px">Close portfolio positions (closed beyond EMA50 today): {exit_txt}</p>'
            f'<p style="margin:0 0 16px;font-size:12px;color:#555">Backtest portfolio since {since}: '
            f'<b style="color:{_color(st.get("total"))}">{_fmt(st.get("total"), "%")}</b> vs SPY '
            f'{_fmt(sp.get("total"), "%")} · max drawdown {_fmt(st.get("max_dd"), "%")} · '
            f'win rate {m.get("trade_stats", {}).get("win_rate", "–")}%'
            + (f' · <a href="{site}#portfolio">details</a>' if site else "") + "</p>")


def send_email(result, bt=None, zp=None):
    user, pwd, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    subject, html = build_email(result, bt, zp)
    (ROOT / "results" / "last_email.html").write_text(html)
    if not (user and pwd and to):
        print("email not sent: SMTP_USER / SMTP_PASSWORD / EMAIL_TO not set (saved results/last_email.html)")
        return
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print(f"email sent to {to}")
