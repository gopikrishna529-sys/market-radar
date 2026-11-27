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
    """Daily email: the Zacks portfolio only (orders for the next open, open positions, closed trades)."""
    site = os.environ.get("SITE_URL", "")
    day = fmt_date(result["date"])
    h2 = 'style="font-size:16px;margin:22px 0 6px;border-left:4px solid #1565c0;padding-left:8px"'
    th = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;font-size:12px;color:#555"'
    td = 'style="padding:5px 8px;border-bottom:1px solid #eee;font-size:13px"'
    zp = zp or {}
    P = zp.get("positions", [])
    last = zp.get("last_day")
    r = zp.get("rules", {})
    D = r.get("dollars_per_trade", 10000)
    buys = [p for p in P if p["status"] == "pending" and p.get("signal_date") == last]
    sells = [p for p in P if p["status"] == "exit next open" and p.get("exit_signal") == last]
    opn = sorted([p for p in P if p["status"] in ("open", "exit next open")], key=lambda p: -(p.get("pnl") or 0))
    closed = sorted([p for p in P if p["status"] == "closed"], key=lambda p: p.get("exit_date", ""), reverse=True)
    sold_today = [p for p in closed if p.get("exit_date") == last]
    upl = sum((p.get("pnl") or 0) / 100 * D for p in opn)
    rpl = sum((p.get("pnl") or 0) / 100 * D for p in closed)
    avg = sum(p.get("pnl") or 0 for p in opn) / len(opn) if opn else None
    wins = sum(1 for p in closed if (p.get("pnl") or 0) > 0)

    def pc(x):
        return f'<span style="color:{_color(x)}">{_fmt(x, "%")}</span>'

    def usd(x):
        return f'<span style="color:{_color(x)}">{"-" if x < 0 else ""}${abs(x):,.0f}</span>'

    def table(head, rows):
        if not rows:
            return '<p style="color:#666;margin:4px 0 14px">None.</p>'
        return (f'<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:12px"><tr>'
                + "".join(f"<th {th}>{h}</th>" for h in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td {td}>{c}</td>" for c in row) + "</tr>" for row in rows) + "</table>")

    def fd(x):
        return fmt_date(x) if x else "–"

    def px(x):
        return "–" if x is None else f"{x:,.2f}"

    tk = lambda p: f'<b>{escape(p["ticker"])}</b>'
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:820px;color:#222">',
        f'<h1 style="font-size:20px;margin:0 0 4px">Market Radar portfolio — {day}</h1>',
        f'<p style="margin:0 0 4px;color:#444">{len(opn)} open positions · open P&amp;L {usd(upl)} (average {pc(avg)}) · '
        f'closed P&amp;L {usd(rpl)} from {len(closed)} trades ({wins} winners) · total {usd(upl + rpl)} '
        f'on ${D:,.0f} per position · tracking since {fd(zp.get("start"))}</p>',
        f'<p style="margin:0 0 8px"><a href="{site}#portfolio">Open the portfolio on the website →</a></p>' if site else "",
        f'<h2 {h2}>🟢 Buy at the next open ({len(buys)})</h2>',
        table(["Ticker", "Zacks Rank", "Brokers", "Price target", "Upside", "RS", "Close"],
              [[tk(p), p["rank"], px(p["broker"]), px(p["target"]), pc(p["upside"]), p["rs"], px(p["signal_close"])] for p in buys]),
        f'<h2 {h2}>🔴 Sell at the next open ({len(sells)})</h2>',
        table(["Ticker", "Bought", "Entry", "Last", "P&amp;L", "Why"],
              [[tk(p), fd(p.get("entry_date")), px(p.get("entry")), px(p.get("last")), pc(p.get("pnl")), escape(p.get("reason", ""))] for p in sells]),
    ]
    if sold_today:
        parts += [f'<h2 {h2}>✅ Sold at today\u2019s open ({len(sold_today)})</h2>',
                  table(["Ticker", "Bought", "Entry", "Exit", "P&amp;L", "Why"],
                        [[tk(p), fd(p.get("entry_date")), px(p.get("entry")), px(p.get("exit")), pc(p.get("pnl")), escape(p.get("reason", ""))] for p in sold_today])]

    def rk(a, b):
        return f"{a}" if b is None or b == a else f'<span style="color:{"#0a7d38" if b < a else "#c62828"}">{a}→{b}</span>'
    parts += [
        f'<h2 {h2}>📈 Open positions ({len(opn)})</h2>',
        table(["Ticker", "Bought", "Entry", "Last", "P&amp;L", "Days", "Rank", "Brokers", "Target"],
              [[tk(p) + (" ⚠️ sell next open" if p["status"] == "exit next open" else ""), fd(p.get("entry_date")),
                px(p.get("entry")), px(p.get("last")), pc(p.get("pnl")), p.get("days", 0),
                rk(p.get("rank"), p.get("rank_now")), rk(p.get("broker"), p.get("broker_now")), px(p.get("target"))] for p in opn]),
        f'<h2 {h2}>📕 Recently closed</h2>',
        table(["Ticker", "Bought", "Sold", "P&amp;L", "Days", "Why"],
              [[tk(p), fd(p.get("entry_date")), fd(p.get("exit_date")), pc(p.get("pnl")), p.get("days", "–"),
                escape(p.get("reason", ""))] for p in closed[:15]]),
        f'<p style="font-size:11px;color:#888;margin-top:20px">Rules: buy next open when Zacks Rank ≤ {r.get("max_rank")}, '
        f'average broker rating &lt; {r.get("max_broker")}, Zacks price target ≥ +{r.get("min_upside_pct")}% above the close, '
        f'EMA uptrend set and close above EMA{CONFIG["ema"]["exit"]}, RS rating ≥ {r.get("min_rs")}. Sell next open after a close below '
        f'EMA{CONFIG["ema"]["exit"]} or when the Zacks Rank falls to {r.get("sell_rank")}+. Rank / brokers show entry→now. '
        "Paper portfolio, not financial advice.</p></div>",
    ]
    subject = (f"Market Radar {day}: {len(buys)} buy, {len(sells)} sell · {len(opn)} open "
               f"({_fmt(avg, '%') if avg is not None else '–'})")
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
            + (f' · <a href="{site}#portfolio">details</a>' if site else "") + "</p>")


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
    return (f'<h2 {h2}>📋 Old EMA strategy (backtest only) — signals for the next open</h2>'
            f'<p style="margin:2px 0">Buy (EMA trend just set, strongest RS first): {names(longs)}</p>'
            f'<p style="margin:2px 0">Short (EMA downtrend just set): {names(shorts)}</p>'
            f'<p style="margin:2px 0 8px">Close portfolio positions (closed beyond EMA50 today): {exit_txt}</p>'
            f'<p style="margin:0 0 16px;font-size:12px;color:#555">Backtest portfolio since {since}: '
            f'<b style="color:{_color(st.get("total"))}">{_fmt(st.get("total"), "%")}</b> vs SPY '
            f'{_fmt(sp.get("total"), "%")} · max drawdown {_fmt(st.get("max_dd"), "%")} · '
            f'win rate {m.get("trade_stats", {}).get("win_rate", "–")}%'
            + (f' · <a href="{site}#backtest">details</a>' if site else "") + "</p>")


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
