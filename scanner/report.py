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


def build_email(result, bt=None, zp=None, brief=None):
    """Daily email: the Zacks portfolio only (orders for the next open, open positions, closed trades)."""
    site = os.environ.get("SITE_URL", "")
    day = fmt_date(result["date"])
    h2 = 'style="font-size:16px;margin:22px 0 6px;border-left:4px solid #1565c0;padding-left:8px"'
    th = 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;font-size:12px;color:#555"'
    td = 'style="padding:5px 8px;border-bottom:1px solid #eee;font-size:13px"'
    zp = zp or {}
    brief = brief or {}
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

    def table(head, rows, tint=None):
        if not rows:
            return '<p style="color:#666;margin:4px 0 14px">None.</p>'
        tint = tint or [""] * len(rows)
        return (f'<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:12px"><tr>'
                + "".join(f"<th {th}>{h}</th>" for h in head) + "</tr>"
                + "".join(f'<tr style="background:{bg}">' + "".join(f"<td {td}>{c}</td>" for c in row) + "</tr>"
                          for row, bg in zip(rows, tint)) + "</table>")

    sec = {r["ticker"]: r for r in result.get("sectors") or []}
    nsec = len(sec)

    def srank(p):
        r = sec.get(p.get("sector_etf"))
        return r["rank"] if r else 99

    def tint(p):
        k = srank(p)
        return "#e3f6e8" if k <= 2 else "#fdecea" if nsec and nsec - 1 <= k <= nsec else ""

    def sec_cell(p):
        r = sec.get(p.get("sector_etf"))
        if not r:
            return '<span style="color:#999">–</span>'
        tag = (' <b style="color:#0a7d38">▲ top</b>' if r["rank"] <= 2 else
               ' <b style="color:#c62828">▼ weak</b>' if r["rank"] >= nsec - 1 else "")
        return f'{escape(r["name"])} <span style="color:#888">#{r["rank"]}</span>{tag}'

    def fd(x):
        return f'<span style="white-space:nowrap">{fmt_date(x)}</span>' if x else "–"

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
        _mood_banner(brief.get("mood")),
        _tldr(result, zp, brief, opn, buys, sells, sold_today),
        _mood_section(brief.get("mood"), h2, th, td),
        _portfolio_section(zp, opn, closed, h2, th, td, brief),
        _fills_section(brief, h2, th, td),
        f'<h2 {h2}>🟢 Buy at the next open ({len(buys)})</h2>'
        + '<p style="margin:0 0 6px;font-size:12px;color:#555">Sorted by sector strength: '
          '<span style="background:#e3f6e8;padding:1px 6px">green = top-2 sector</span> first, '
          '<span style="background:#fdecea;padding:1px 6px">red = bottom-2 sector</span> last.</p>',
        table(["Ticker", "Sector", "Zacks Rank", "Brokers", "Price target", "Upside", "RS", "Close"],
              [[tk(p), sec_cell(p), p["rank"], px(p["broker"]), px(p["target"]), pc(p["upside"]), p["rs"], px(p["signal_close"])]
               for p in sorted(buys, key=lambda p: (srank(p), -(p.get("upside") or 0)))], tint=[tint(p) for p in sorted(buys, key=lambda p: (srank(p), -(p.get("upside") or 0)))]),
        f'<h2 {h2}>🔴 Sell at the next open ({len(sells)})</h2>',
        table(["Ticker", "Bought", "Entry", "Last", "P&amp;L", "Why"],
              [[tk(p), fd(p.get("entry_date")), px(p.get("entry")), px(p.get("last")), pc(p.get("pnl")), escape(p.get("reason", ""))] for p in sells]),
        _market_section(result, P, buys, opn, h2, th, td, brief),
        _zchanges_section(brief, h2),
        _near_section(brief, h2, th, td, sec),
        _leaders_section(brief, result, h2, th, td),
    ]
    if sold_today:
        parts += [f'<h2 {h2}>✅ Sold at today\u2019s open ({len(sold_today)})</h2>',
                  table(["Ticker", "Bought", "Entry", "Exit", "P&amp;L", "Why"],
                        [[tk(p), fd(p.get("entry_date")), px(p.get("entry")), px(p.get("exit")), pc(p.get("pnl")), escape(p.get("reason", ""))] for p in sold_today])]

    def rk(a, b):
        return f"{a}" if b is None or b == a else f'<span style="color:{"#0a7d38" if b < a else "#c62828"}">{a}→{b}</span>'
    parts += [
        f'<h2 {h2}>📈 Open positions ({len(opn)})</h2>',
        table(["Ticker", "Sector", "Bought", "Entry", "Last", "Today", "P&amp;L", "Days", "Above EMA50<br>(sell line)", "To target", "Rank", "Brokers"],
              [[tk(p) + (" ⚠️ sell next open" if p["status"] == "exit next open" else ""), sec_cell(p), fd(p.get("entry_date")),
                px(p.get("entry")), px(p.get("last")), pc(p.get("chg")), f'<b>{pc(p.get("pnl"))}</b>', p.get("days", 0),
                pc(p.get("vs_ema")), pc(p.get("to_target")),
                rk(p.get("rank"), p.get("rank_now")), rk(p.get("broker"), p.get("broker_now"))] for p in opn],
              tint=[tint(p) for p in opn]),
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


def _pcs(x, d=1):
    return "–" if x is None else f'<span style="color:{_color(x)}">{x:+.{d}f}%</span>'


def _tbl(th, td, head, rows):
    return (f'<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:8px"><tr>'
            + "".join(f"<th {th}>{h}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td {td}>{c}</td>" for c in row) + "</tr>" for row in rows) + "</table>")


def _tldr(result, zp, brief, opn, buys, sells, sold_today):
    """'Yesterday in 30 seconds' box at the very top."""
    daily = zp.get("daily") or []
    D = (zp.get("rules") or {}).get("dollars_per_trade", 10000)
    lines = []
    if len(daily) >= 2 and any(x[2] for x in daily[-2:]):
        d = daily[-1][1] - daily[-2][1]
        lines.append(f'Portfolio <b style="color:{_color(d)}">{"+" if d >= 0 else "-"}${abs(d):,.0f}</b> on the day · '
                     f'total <b style="color:{_color(daily[-1][1])}">{"+" if daily[-1][1] >= 0 else "-"}${abs(daily[-1][1]):,.0f}</b> · {len(opn)} open positions.')
    elif opn:
        lines.append(f"{len(opn)} open positions (first day — no daily change yet).")
    else:
        lines.append("No positions yet — the first buy orders fill at the next open.")
    snap = {x["t"]: x for x in brief.get("snapshot", [])}
    mk = " · ".join(f'{n} {_pcs(snap[t]["chg"], 2)}' for t, n in (("SPY", "S&amp;P 500"), ("QQQ", "Nasdaq"), ("IWM", "Small caps")) if t in snap)
    if mk:
        b = brief.get("breadth") or {}
        trend = ""
        if b.get("now") is not None and b.get("w1") is not None:
            dd = b["now"] - b["w1"]
            trend = f' · breadth {"improving" if dd > 2 else "weakening" if dd < -2 else "flat"} ({b["now"]:.0f}% above EMA200, {dd:+.0f} pts in a week)'
        lines.append(f"Market: {mk}{trend}.")
    bought = [f for f in brief.get("fills", []) if f["side"] == "buy"]
    act = []
    if bought:
        act.append(f"bought {len(bought)} at the open")
    if sold_today:
        act.append(f"sold {len(sold_today)} ({', '.join(escape(p['ticker']) for p in sold_today[:5])})")
    act.append(f"{len(buys)} buy order{'s' if len(buys) != 1 else ''} and {len(sells)} sell order{'s' if len(sells) != 1 else ''} for the next open")
    lines.append("Today: " + ", ".join(act) + ".")
    watch = []
    near = [p for p in opn if p["status"] == "open" and p.get("vs_ema") is not None and 0 <= p["vs_ema"] <= 2]
    if near:
        watch.append(f"{len(near)} near the EMA50 sell line ({', '.join(escape(p['ticker']) for p in near[:4])})")
    hit = [p for p in opn if p.get("to_target") is not None and p["to_target"] <= 0]
    if hit:
        watch.append(f"{len(hit)} at their price target ({', '.join(escape(p['ticker']) for p in hit[:4])})")
    soon = [e for e in brief.get("earnings", []) if e["date"] <= _days_ahead(result["date"], 7)]
    if soon:
        watch.append(f"earnings within a week: {', '.join(escape(e['t']) for e in soon[:5])}")
    dn = [x for x in (brief.get("zchanges") or {}).get("items", []) if x["held"] and x["kind"] == "rank" and (x["new"] or 9) > (x["old"] or 0)]
    if dn:
        watch.append(f"Zacks downgrades overnight: {', '.join(escape(x['t']) for x in dn[:4])}")
    md = brief.get("mood") or {}
    if md.get("mood"):
        lines.append(f'Market mood: <b>{escape(md["mood"])}</b>'
                     + (f' · VIX {md["vix"]:.1f}' if md.get("vix") is not None else "")
                     + (f' · put/call {md["pcr_total"]:.2f}' if md.get("pcr_total") is not None else "") + f' — {escape(md.get("action", ""))}')
    lines.append("<b>Needs attention:</b> " + ("; ".join(watch) + "." if watch else "nothing urgent."))
    return ('<div style="background:#f3f7fd;border:1px solid #d6e4f7;border-radius:8px;padding:10px 14px;margin:8px 0 6px">'
            '<div style="font-weight:700;margin-bottom:4px">⏱ Yesterday in 30 seconds</div>'
            + "".join(f'<div style="margin:2px 0">• {x}</div>' for x in lines) + "</div>")


def _mood_banner(m):
    """Big red / amber box at the very top when VIX or the put/call ratio flash a warning."""
    if not m or not (m.get("alerts") or m.get("changed")):
        return ""
    red = any(a["level"] == "red" for a in m.get("alerts", []))
    bg, bd, ic = ("#fdecea", "#e57373", "🚨") if red else ("#fff6e0", "#f0b429", "⚠️")
    lines = ([f"<b>{escape(m['changed'])}</b>"] if m.get("changed") else []) + [escape(a["text"]) for a in m.get("alerts", [])]
    return (f'<div style="background:{bg};border:2px solid {bd};border-radius:8px;padding:10px 14px;margin:8px 0">'
            f'<div style="font-size:16px;font-weight:800;margin-bottom:4px">{ic} Market alert — mood: {escape(m.get("mood", ""))}</div>'
            + "".join(f'<div style="margin:3px 0">• {x}</div>' for x in lines)
            + '<div style="margin-top:4px;font-size:12px;color:#555">Details and the reasoning are in “Fear gauges” below.</div>'
            + f'<div style="margin-top:6px;font-weight:700">What to do: {escape(m.get("action", ""))}</div></div>')


def _mood_section(m, h2, th, td):
    """VIX + put/call ratios with plain-English reading."""
    if not m or (m.get("vix") is None and m.get("pcr_total") is None):
        return ""
    col = {"Bullish": "#0a7d38", "Neutral": "#555", "Cautious": "#d17a00", "Bearish": "#c62828"}.get(m.get("mood"), "#555")

    def pcr_c(x, hi=1.0, lo=0.7):
        return "–" if x is None else f'<b style="color:{"#c62828" if x > hi else "#0a7d38" if x < lo else "#222"}">{x:.2f}</b>'
    rows = []
    if m.get("vix") is not None:
        z = m.get("vix_zone", "")
        rows.append(["<b>😨 Fear index (VIX)</b><br><span style=\"color:#777;font-size:11px\">how big a move traders expect in the S&amp;P 500 over the next month</span>",
                     f'<b style="color:{"#0a7d38" if z in ("calm", "normal") else "#c62828"}">{m["vix"]:.1f}</b> '
                     f'<span style="color:{_color(-(m.get("vix_chg") or 0))}">({m.get("vix_chg", 0):+.1f}%)</span>',
                     f'20-day avg {m.get("vix_avg20", 0):.1f} · higher than {m.get("vix_pctl", 0)}% of the last year · {z}',
                     "below 20 calm · 20–30 nervous · above 30 panic" + (f'<br>last 5 days: {" → ".join(f"{x[1]:.1f}" for x in m.get("vix_5d", []))}' if m.get("vix_5d") else "")])
    if m.get("term") is not None:
        rows.append(["<b>⏱ Panic now vs later</b><br><span style=\"color:#777;font-size:11px\">VIX (next 30 days) ÷ VIX3M (next 3 months)</span>",
                     f'<b style="color:{"#c62828" if m["term"] >= 1 else "#0a7d38"}">{m["term"]:.2f}</b>',
                     f'3-month VIX {m.get("vix3m", 0):.1f}', "normally below 1 (people worry more about the distant future) · above 1 = traders scared <i>right now</i>, typical of sell-offs"])
    if m.get("pcr_total") is not None:
        rows.append(["<b>⚖️ Put/call ratio</b><br><span style=\"color:#777;font-size:11px\">puts traded ÷ calls traded, all US options</span>", pcr_c(m["pcr_total"]),
                     f'5-day avg {m.get("pcr_avg5", 0):.2f} · 10-day avg {m.get("pcr_avg10", 0):.2f}'
                     + (f'<br>last 5 days: {" → ".join(f"{x[1]:.2f}" for x in m.get("pcr_5d", []) if x[1])}' if m.get("pcr_5d") else ""),
                     "below 1 = more calls (optimism) · above 1 = more puts (fear / hedging)"])
        if m.get("pcr_equity") is not None:
            rows.append(["<b>Put/call — stock options only</b>", pcr_c(m["pcr_equity"], 0.85, 0.5), "individual-stock traders",
                         "0.5–0.85 normal · above ~1 extreme fear (often near bottoms) · below 0.5 greed"])
    notes = ""
    return (f'<h2 {h2}>😨 Fear gauges — mood: <span style="color:{col}">{escape(m.get("mood", ""))}</span></h2>'
            + _tbl(th, td, ["Gauge", "Now", "Context", "How to read it"], rows)
            + (f'<ul style="margin:4px 0 6px;padding-left:20px;line-height:1.5">{notes}</ul>' if notes else "")
            + (f'<p style="margin:6px 0 2px"><b>Why “{escape(m.get("mood", ""))}”</b> — {escape(m.get("why_text", ""))}:</p>'
               + "".join(f'<div style="margin:1px 0 1px 6px">{"✅" if c["ok"] else "❌"} {escape(c["text"])}</div>' for c in m.get("checks", []))
               if m.get("checks") else "")
            + f'<p style="margin:0 0 6px"><b>What to do:</b> {escape(m.get("action", ""))}</p>'
            '<p style="margin:0 0 10px;font-size:11px;color:#888">Put/call figures are Cboe totals for the previous session'
            + (f' ({fmt_date(m.get("pcr_date"))})' if m.get("pcr_date") else "") + '. Note: “put-call parity” is an option-pricing formula '
            '(how call and put prices must line up), not a sentiment signal — the put/call <i>ratio</i> is what shows fear vs greed.</p>')


def _days_ahead(day, n):
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _fills_section(brief, h2, th, td):
    F = brief.get("fills") or []
    if not F:
        return ""
    rows = []
    for f in F:
        if f["side"] == "buy":
            rows.append([f'<b>{escape(f["t"])}</b>', '<span style="color:#0a7d38">BUY</span>', f'{f["price"]:,.2f}' if f["price"] else "–",
                         f'{f["ref"]:,.2f}' if f["ref"] else "–", _pcs(f["gap"]), _pcs(f.get("day")), ""])
        else:
            rows.append([f'<b>{escape(f["t"])}</b>', '<span style="color:#c62828">SELL</span>', f'{f["price"]:,.2f}' if f["price"] else "–",
                         f'{f["ref"]:,.2f}' if f["ref"] else "–", _pcs(f["gap"]), "", f'{_pcs(f.get("pnl"))} · {escape(f.get("reason") or "")}'])
    gaps = [f["gap"] for f in F if f["side"] == "buy" and f["gap"] is not None]
    note = (f'Average gap on buys: {_pcs(sum(gaps) / len(gaps), 2)} versus the signal close '
            f'({"you paid more than the signal price" if sum(gaps) > 0 else "you paid less than the signal price"}).') if gaps else ""
    return (f'<h2 {h2}>🧾 What happened at the open</h2>'
            + _tbl(th, td, ["Ticker", "Side", "Fill (open)", "Previous close", "Gap", "Open → close", "Result"], rows)
            + (f'<p style="margin:0 0 10px;font-size:12px;color:#555">{note}</p>' if note else ""))


def _zchanges_section(brief, h2):
    z = brief.get("zchanges") or {}
    it = z.get("items") or []
    if not it:
        return ""
    held_r = [x for x in it if x["held"] and x["kind"] == "rank"]
    held_b = [x for x in it if x["held"] and x["kind"] == "broker"]
    rank = lambda x: f'<b>{escape(x["t"])}</b> #{x["old"]}→#{x["new"]}' if x["new"] else f'<b>{escape(x["t"])}</b> left the screen'
    lines = []
    if held_r:
        lines.append("<b>Your positions:</b> " + ", ".join(
            f'<span style="color:{"#0a7d38" if (x["new"] or 9) < (x["old"] or 9) else "#c62828"}">{rank(x)}</span>' for x in held_r))
    if held_b:
        lines.append("<b>Broker rating changes on your positions:</b> " + ", ".join(
            f'<span style="color:{"#0a7d38" if x["new"] < x["old"] else "#c62828"}"><b>{escape(x["t"])}</b> {x["old"]:.2f}→{x["new"]:.2f}</span>' for x in held_b))
    n1 = z.get("new_rank1") or []
    if n1:
        lines.append(f"<b>New Zacks Rank #1 (Strong Buy)</b>, liquid stocks, strongest RS first: "
                     + ", ".join(f"<b>{escape(t)}</b>" for t in n1[:15]) + (f" and {len(n1) - 15} more" if len(n1) > 15 else "") + ".")
    up = sum(1 for x in it if x["kind"] == "rank" and x["new"] and x["old"] and x["new"] < x["old"])
    dn = sum(1 for x in it if x["kind"] == "rank" and x["new"] and x["old"] and x["new"] > x["old"])
    lines.append(f"Across your Zacks screens: {up} rank upgrades and {dn} downgrades overnight"
                 + (" — more downgrades than upgrades, analysts are turning cautious." if dn > up * 1.3 else
                    " — more upgrades than downgrades, estimates are rising." if up > dn * 1.3 else "."))
    return (f'<h2 {h2}>🔔 Overnight Zacks changes <span style="font-weight:400;font-size:12px;color:#777">({fmt_date(z.get("date"))} file)</span></h2>'
            '<ul style="margin:4px 0 10px;padding-left:20px;line-height:1.55">' + "".join(f"<li>{x}</li>" for x in lines) + "</ul>")


def _near_section(brief, h2, th, td, sec):
    N = brief.get("near") or []
    if not N:
        return ""
    rows = [[f'<b>{escape(x["t"])}</b>', escape(x["why"]), x["rank"], f'{x["broker"]:.2f}', _pcs(x["upside"]), x["rs"], _pcs(x["m1"])] for x in N]
    return (f'<h2 {h2}>👀 Next in line <span style="font-weight:400;font-size:12px;color:#777">(pass every buy rule but one — '
            f'{brief.get("near_total", len(N))} stocks, strongest {len(N)} shown)</span></h2>'
            + _tbl(th, td, ["Ticker", "Still missing", "Zacks", "Brokers", "To target", "RS", "1 month"], rows))


def _leaders_section(brief, result, h2, th, td):
    L = brief.get("leaders") or []
    if not L:
        return ""
    names = {r["ticker"]: r["name"] for r in result.get("sectors") or []}
    rows = [[f'<b>{escape(x["t"])}</b>', escape(names.get(x["sector"], x["sector"])), x["rs"], _pcs(x["m1"]),
             x["rank"] if x["rank"] else "–", f'{x["broker"]:.2f}' if x["broker"] else "–"] for x in L]
    return (f'<h2 {h2}>⭐ Leaders in the leading sectors <span style="font-weight:400;font-size:12px;color:#777">(in an EMA uptrend, not owned — '
            'the Zacks columns show why they are not a buy yet)</span></h2>'
            + _tbl(th, td, ["Ticker", "Sector", "RS", "1 month", "Zacks Rank", "Brokers"], rows))


def _portfolio_section(zp, opn, closed, h2, th, td, brief=None):
    """How the portfolio is doing: totals, vs SPY, daily P&L history and a check of every open position."""
    r = zp.get("rules", {})
    D = r.get("dollars_per_trade", 10000)
    daily = zp.get("daily") or []
    filled = [p for p in zp.get("positions", []) if p.get("entry")]

    def pc(x, d=1):
        return "–" if x is None else f'<span style="color:{_color(x)}">{x:+.{d}f}%</span>'

    def usd(x):
        return "–" if x is None else f'<span style="color:{_color(x)}">{"-" if x < 0 else "+"}${abs(x):,.0f}</span>'
    bt = lambda L: ", ".join(f'<b>{escape(p["ticker"])}</b> {pc(p.get("pnl"))}' for p in L)
    if not filled:
        pend = [p for p in zp.get("positions", []) if p["status"] == "pending"]
        return (f'<h2 {h2}>📊 How the portfolio is doing</h2><p style="margin:0 0 8px;color:#444">Nothing bought yet — '
                f'{len(pend)} buy orders fill at the next open, and the first results appear in tomorrow\'s email.</p>')
    upl = sum((p.get("pnl") or 0) / 100 * D for p in opn)
    rpl = sum((p.get("pnl") or 0) / 100 * D for p in closed)
    tot = upl + rpl
    ret = tot / (D * len(filled)) * 100
    today = daily[-1][1] - daily[-2][1] if len(daily) >= 2 else None
    first = next((x for x in daily if len(x) > 3 and x[2]), None)
    spy_ret = (daily[-1][3] / first[3] - 1) * 100 if first and len(daily[-1]) > 3 and daily[-1][3] and first[3] else None
    wins = [p for p in closed if (p.get("pnl") or 0) > 0]
    loss = [p for p in closed if (p.get("pnl") or 0) <= 0]
    avg = lambda L: sum(p["pnl"] for p in L) / len(L) if L else None
    cell = lambda l, v, sub="": (f'<td style="padding:8px 10px;border:1px solid #e6e6e6;vertical-align:top"><div style="font-size:11px;color:#777">{l}</div>'
                                  f'<div style="font-size:17px;font-weight:700;margin-top:2px">{v}</div><div style="font-size:11px;color:#777">{sub}</div></td>')
    kpi = ('<table cellspacing="0" style="border-collapse:collapse;width:100%;margin:4px 0 10px"><tr>'
           + cell("Total P&amp;L", usd(tot), f"{pc(ret)} on ${D * len(filled):,.0f} used")
           + cell("Today", usd(today), "change in total P&amp;L")
           + cell("Open positions", f"{len(opn)}", f"{usd(upl)} · average {pc(avg([p for p in opn if p.get('pnl') is not None]))}")
           + cell("Closed trades", f"{len(closed)}", f"{usd(rpl)} · {len(wins)} won" + (f" ({len(wins) / len(closed):.0%})" if closed else ""))
           + cell("vs SPY", pc(ret - spy_ret) if spy_ret is not None else "–",
                  f"portfolio {pc(ret)} · SPY {pc(spy_ret)} since {fmt_date(first[0]) if first else '–'}")
           + "</tr></table>")
    # daily P&L bars (last 30 days)
    bars = ""
    hist = daily[-30:]
    if len(hist) >= 2:
        m = max(abs(x[1]) for x in hist) or 1
        bars = ('<table cellspacing="0" style="border-collapse:collapse;margin:2px 0 4px"><tr style="vertical-align:bottom">'
                + "".join(f'<td title="{fmt_date(x[0])}: ${x[1]:,.0f}" style="padding:0 1px;height:46px;vertical-align:{"bottom" if x[1] >= 0 else "top"}">'
                          f'<div style="width:14px;height:{max(2, int(44 * abs(x[1]) / m))}px;background:{"#26a69a" if x[1] >= 0 else "#ef5350"}"></div></td>' for x in hist)
                + f'</tr></table><div style="font-size:11px;color:#888;margin-bottom:8px">Total P&amp;L each day, {fmt_date(hist[0][0])} → {fmt_date(hist[-1][0])}</div>')
    # checks on open positions
    ins = []
    wp = [p for p in opn if p.get("pnl") is not None]
    if wp:
        up = [p for p in wp if p["pnl"] > 0]
        ins.append(f"<b>{len(up)} of {len(wp)}</b> open positions are in profit · average {pc(avg(wp))} after "
                   f"{sum(p.get('days', 0) for p in wp) / len(wp):.0f} days on average.")
        b3 = sorted(wp, key=lambda p: -p["pnl"])[:3]
        w3 = sorted(wp, key=lambda p: p["pnl"])[:3]
        ins.append(f"<b>Leaders:</b> {bt(b3)}.")
        if w3 and w3[0]["pnl"] < 0:
            ins.append(f"<b>Laggards:</b> {bt([p for p in w3 if p['pnl'] < 0])}.")
    sell = [p for p in opn if p["status"] == "exit next open"]
    if sell:
        ins.append(f"<b>Selling at the next open:</b> " + ", ".join(f'<b>{escape(p["ticker"])}</b> ({escape(p.get("reason", ""))})' for p in sell) + ".")
    near = [p for p in opn if p["status"] == "open" and p.get("vs_ema") is not None and 0 <= p["vs_ema"] <= 2]
    if near:
        ins.append("<b>Close to the sell line</b> (within 2% of EMA50 — a close below sells it): "
                   + ", ".join(f'<b>{escape(p["ticker"])}</b> {pc(p["vs_ema"])} above' for p in near) + ".")
    hit = [p for p in opn if p.get("to_target") is not None and p["to_target"] <= 0]
    if hit:
        ins.append("<b>At or above the Zacks price target</b> (upside used up — consider taking some profit): " + bt(hit) + ".")
    close_t = [p for p in opn if p.get("to_target") is not None and 0 < p["to_target"] <= 3]
    if close_t:
        ins.append("<b>Nearly at target</b> (less than 3% to go): " + ", ".join(f'<b>{escape(p["ticker"])}</b> {pc(p["to_target"])} left' for p in close_t) + ".")
    big_w = [p for p in wp if p["pnl"] >= 10]
    if big_w:
        ins.append(f"<b>Big winners (+10% or more):</b> {bt(big_w)} — let them run while above EMA50.")
    big_l = [p for p in wp if p["pnl"] <= -7]
    if big_l:
        ins.append(f"<b>Down 7% or more:</b> {bt(big_l)} — the EMA50 rule decides, but check the news.")
    mv = [p for p in opn if p.get("chg") is not None]
    if mv:
        bm, wm = max(mv, key=lambda p: p["chg"]), min(mv, key=lambda p: p["chg"])
        ins.append(f"<b>Today:</b> best {escape(bm['ticker'])} {pc(bm['chg'], 2)} · worst {escape(wm['ticker'])} {pc(wm['chg'], 2)}.")
    dn = [p for p in opn if isinstance(p.get("rank_now"), int) and isinstance(p.get("rank"), int) and p["rank_now"] > p["rank"]]
    upg = [p for p in opn if isinstance(p.get("rank_now"), int) and isinstance(p.get("rank"), int) and p["rank_now"] < p["rank"]]
    if dn:
        ins.append("<b>Zacks downgrades since buying:</b> " + ", ".join(f'<b>{escape(p["ticker"])}</b> #{p["rank"]}→#{p["rank_now"]}' for p in dn)
                   + f" (sold automatically at Rank {r.get('sell_rank', 4)}+).")
    if upg:
        ins.append("<b>Zacks upgrades since buying:</b> " + ", ".join(f'<b>{escape(p["ticker"])}</b> #{p["rank"]}→#{p["rank_now"]}' for p in upg) + ".")
    rk_ = (brief or {}).get("risk")
    if rk_ and rk_["max_dd"] < 0:
        ins.append(f"<b>Risk:</b> largest drop from a peak so far {usd(rk_['max_dd'])}; now {usd(rk_['cur_dd'])} below the best day.")
    E = (brief or {}).get("earnings") or []
    if E:
        ins.append("<b>Earnings coming up:</b> " + ", ".join(f'<b>{escape(e["t"])}</b> {fmt_date(e["date"])}' for e in E[:10])
                   + " — results can move a stock 10–20% overnight.")
    elif brief is not None and not brief.get("earnings_available"):
        ins.append('<span style="color:#888">Earnings dates: add the “Next EPS Report Date” column to your Zacks '
                   'market-radar screen and they will appear here.</span>')
    if closed:
        ins.append(f"<b>Closed trades:</b> average winner {pc(avg(wins))}, average loser {pc(avg(loss))}, held "
                   f"{sum(p.get('days') or 0 for p in closed) / len(closed):.0f} days on average.")
    return (f'<h2 {h2}>📊 How the portfolio is doing</h2>' + kpi + bars
            + '<ul style="margin:4px 0 10px;padding-left:20px;line-height:1.55">' + "".join(f"<li>{x}</li>" for x in ins) + "</ul>")


def _market_section(result, P, buys, opn, h2, th, td, brief=None):
    """Market tone + sector leaderboard + plain-English insights for the portfolio."""
    S = result.get("sectors") or []
    if not S:
        return ""
    n = len(S)
    b = result.get("benchmark") or {}
    br = result.get("breadth") or {}

    def pc(x, d=1):
        if x is None:
            return "–"
        return f'<span style="color:{_color(x)}">{x:+.{d}f}%</span>'
    pit = br.get("pct_in_trend") or 0
    p200 = br.get("pct_above_ema200") or 0
    tone = ("🟢 <b>healthy</b> — most stocks are in uptrends" if p200 >= 60 else
            "🟡 <b>mixed</b> — be selective, favour the strongest sectors" if p200 >= 40 else
            "🔴 <b>weak</b> — most stocks are below their EMA200; keep new buys small")
    top, bot = S[:2], S[-2:][::-1]
    by = {}
    for p in P:
        by.setdefault(p.get("sector_etf"), []).append(p)
    bt = lambda L: ", ".join(f'<b>{escape(p["ticker"])}</b>' for p in L)
    ins = []
    ins.append(f'<b>Strongest sectors:</b> {" and ".join(f"{escape(r["name"])} ({r["ticker"]}) {pc(r["rel_1m"])}" for r in top)} vs SPY over the last month.')
    ins.append(f'<b>Weakest sectors:</b> {" and ".join(f"{escape(r["name"])} ({r["ticker"]}) {pc(r["rel_1m"])}" for r in bot)} — money is leaving these.')
    imp = [r for r in S if r["quadrant"] == "Improving"]
    if imp:
        ins.append("<b>Watch for opportunities:</b> " + ", ".join(escape(r["name"]) for r in imp)
                   + " — still behind SPY but momentum is turning up (often the next leaders).")
    wk = [r for r in S if r["quadrant"] == "Weakening"]
    if wk:
        ins.append("<b>Losing steam:</b> " + ", ".join(escape(r["name"]) for r in wk)
                   + " — still ahead of SPY but slowing; tighten stops on positions there.")
    mv = sorted([r for r in S if abs(r.get("rank_change") or 0) >= 3], key=lambda r: -abs(r["rank_change"]))
    if mv:
        ins.append("<b>Big moves in the ranking this week:</b> " + ", ".join(
            f'{escape(r["name"])} {"▲" if r["rank_change"] > 0 else "▼"}{abs(r["rank_change"])} places' for r in mv[:3]) + ".")
    tk_top = [p for p in buys if p.get("sector_etf") in {r["ticker"] for r in top}]
    tk_bot = [p for p in buys if p.get("sector_etf") in {r["ticker"] for r in bot}]
    if buys:
        if tk_top:
            ins.append(f'<b>Best-aligned buys</b> (strong stock + strong sector): {bt(tk_top)}.')
        if tk_bot:
            ins.append(f'<b>Against the tide</b> (buy signal but weak sector — consider a smaller size): {bt(tk_bot)}.')
        cb = {}
        for p in buys:
            cb[p.get("sector") or "Other"] = cb.get(p.get("sector") or "Other", 0) + 1
        bigb = max(cb.items(), key=lambda kv: kv[1])
        if len(buys) >= 5 and bigb[1] / len(buys) >= 0.35:
            ins.append(f'<b>Today\'s buys are bunched:</b> {bigb[1]} of {len(buys)} are in {escape(bigb[0])} — '
                       'one bad day for that sector would hit many positions at once.')
    if opn:
        cnt = {}
        for p in opn:
            cnt[p.get("sector") or "Other"] = cnt.get(p.get("sector") or "Other", 0) + 1
        big = max(cnt.items(), key=lambda kv: kv[1])
        if big[1] / len(opn) >= 0.3 and len(opn) >= 4:
            ins.append(f'<b>Concentration:</b> {big[1]} of {len(opn)} open positions ({big[1] / len(opn):.0%}) are in {escape(big[0])}.')
    rows = []
    for r in S:
        k = r["rank"]
        bg = "#e3f6e8" if k <= 2 else "#fdecea" if k >= n - 1 else ""
        ch = r.get("rank_change") or 0
        chs = f'<span style="color:{"#0a7d38" if ch > 0 else "#c62828"}">{"▲" if ch > 0 else "▼"}{abs(ch)}</span>' if ch else ""
        held = len([p for p in by.get(r["ticker"], []) if p["status"] in ("open", "exit next open", "pending")])
        rows.append(f'<tr style="background:{bg}"><td {td}><b>#{k}</b> {chs}</td><td {td}>{escape(r["name"])} <span style="color:#888">{r["ticker"]}</span></td>'
                    f'<td {td}>{pc(r["rel_1w"])}</td><td {td}><b>{pc(r["rel_1m"])}</b></td><td {td}>{pc(r["rel_3m"])}</td>'
                    f'<td {td}>{escape(r["quadrant"])}</td><td {td}>{held or ""}</td></tr>')
    snap = (brief or {}).get("snapshot") or []
    st = ""
    if snap:
        st = _tbl(th, td, ["", "Day", "1 week", "1 month", "Trend"],
                  [[f'<b>{escape(x["name"])}</b> <span style="color:#888">{x["t"]}</span>', f'<b>{_pcs(x["chg"], 2)}</b>', _pcs(x["w1"]), _pcs(x["m1"]),
                    ('<span style="color:#0a7d38">▲ uptrend</span>' if x["trend"] == "up" else '<span style="color:#c62828">▼ downtrend</span>'
                     if x["trend"] == "down" else ("above EMA50" if x["above50"] else "below EMA50"))] for x in snap])
        risk_off = [x["t"] for x in snap if x["t"] in ("TLT", "GLD", "UUP") and (x["chg"] or 0) > 0.5]
        if (next((x["chg"] for x in snap if x["t"] == "SPY"), 0) or 0) < -0.5 and len(risk_off) >= 2:
            st += '<p style="margin:0 0 8px;font-size:12px">⚠️ Stocks down while bonds / gold / dollar are up — a <b>risk-off</b> day.</p>'
    bb = (brief or {}).get("breadth") or {}
    bl = ""
    if bb.get("now") is not None:
        def dlt(a):
            return "–" if a is None else f'{a:.0f}%'
        bl = (f'<p style="margin:0 0 6px"><b>Breadth</b> (stocks above EMA200): {dlt(bb.get("now"))} today · {dlt(bb.get("w1"))} a week ago · '
              f'{dlt(bb.get("m1"))} a month ago · {dlt(bb.get("m3"))} 3 months ago. Full EMA uptrend: {dlt(bb.get("trend_now"))} '
              f'(was {dlt(bb.get("trend_w1"))} a week ago). New 52-week highs <b style="color:#0a7d38">{bb.get("new_highs")}</b> vs '
              f'lows <b style="color:#c62828">{bb.get("new_lows")}</b>'
              + (" — more stocks breaking down than out." if bb.get("new_lows", 0) > bb.get("new_highs", 0) * 1.5 else
                 " — more breakouts than breakdowns." if bb.get("new_highs", 0) > bb.get("new_lows", 0) * 1.5 else ".") + "</p>")
    return (f'<h2 {h2}>🧭 Market &amp; sectors</h2>' + st + bl +
            f'<p style="margin:0 0 6px"><b>Market tone:</b> {tone}.</p>'
            '<ul style="margin:6px 0 10px;padding-left:20px;line-height:1.55">' + "".join(f"<li>{x}</li>" for x in ins) + "</ul>"
            f'<table cellspacing="0" style="border-collapse:collapse;width:100%;margin-bottom:6px"><tr>'
            + "".join(f"<th {th}>{h}</th>" for h in ("Rank", "Sector", "1W vs SPY", "1M vs SPY", "3M vs SPY", "Rotation", "Ours (open + orders)"))
            + "</tr>" + "".join(rows) + "</table>"
            '<p style="margin:0 0 10px;font-size:11px;color:#888">Ranked by 1-month performance against SPY. Rotation: Leading = strong and '
            'getting stronger · Weakening = strong but slowing · Improving = weak but picking up · Lagging = weak and getting weaker. '
            '▲▼ = places moved since a week ago.</p>')


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


def send_alert(m):
    """Short alert e-mail when the market mood turns red outside the daily e-mail."""
    user, pwd, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    site = os.environ.get("SITE_URL", "")
    html = ('<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:700px;color:#222">' + _mood_banner(m)
            + _mood_section(m, 'style="font-size:16px;margin:18px 0 6px"', 'style="text-align:left;padding:6px 8px;border-bottom:2px solid #ddd;font-size:12px;color:#555"',
                            'style="padding:5px 8px;border-bottom:1px solid #eee;font-size:13px"')
            + (f'<p><a href="{site}">Open Market Radar →</a></p>' if site else "") + "</div>")
    subject = f"🚨 Market alert: mood {m.get('mood')} · VIX {m.get('vix')} · put/call {m.get('pcr_total')}"
    (ROOT / "results" / "last_alert.html").write_text(html)
    if not (user and pwd and to):
        print("alert e-mail not sent: SMTP settings missing")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465")),
                          context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    print(f"alert e-mail sent to {to}")


def send_email(result, bt=None, zp=None, brief=None):
    user, pwd, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    subject, html = build_email(result, bt, zp, brief)
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
