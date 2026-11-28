"""Entry point.

    python run.py            daily: new day(s) -> scan -> backtest -> website -> email (only if a new day arrived)
    python run.py --force    manual run: also fills older history back to config history.start, then rebuilds all
    python run.py --no-email skip sending the email
    python run.py --site     after a Zacks download: rebuild the website (no email) even without a new day
"""
import sys

from scanner.backtest import run_backtest
from scanner.config import CONFIG
from scanner.data_store import backfill_history, load_panel, update_prices, update_reference
from scanner.export import build_site
from scanner.indicators import compute
from scanner.massive import Massive
from scanner.report import fmt_date, send_email
from scanner.scan import run_scan
from scanner import zportfolio
from scanner.holdings import update_holdings


def main():
    force = "--force" in sys.argv
    client = Massive(base_url=CONFIG["api"]["base_url"],
                     requests_per_minute=CONFIG["api"]["requests_per_minute"])
    added = update_prices(client)
    if force:
        added += backfill_history(client)
    site_only = not added and not force and "--site" in sys.argv
    if not added and not force and not site_only:
        print("No new trading day - nothing to do.")
        return
    try:
        update_holdings()                           # S&P 500 / Nasdaq 100 / sector ETF members (once a day)
    except Exception as e:
        print(f"holdings: skipped ({e})")
    def mood(w):
        try:
            from scanner.sentiment import update as update_mood
            return update_mood([d.strftime("%Y-%m-%d") for d in w["close"].index])
        except Exception as e:
            print(f"market mood: skipped ({e})")
            return None
    if site_only:                                   # new Zacks file, same prices: refresh the website only
        w = load_panel()
        mood(w)
        ind = compute(w)
        zportfolio.update(w, ind)                   # marks to market; buys only on a new price day
        build_site(w, ind)
        return
    update_reference(client)

    w = load_panel()
    print(f"loaded {len(w['close'])} days x {w['close'].shape[1]} tickers")
    ind = compute(w)
    result = run_scan(w, ind)
    print(f"{fmt_date(result['date'])}: {len(result['trend'])} in trend, {len(result['breakouts'])} breakouts, "
          f"{len(result['rs_days'])} up-while-SPY-down, {len(result['rs_leaders'])} RS leaders")
    bt = run_backtest(w, ind)
    zp = zportfolio.update(w, ind)
    md = mood(w)
    build_site(w, ind)
    if "--no-email" not in sys.argv:
        try:
            from scanner.brief import build as build_brief
            brief = build_brief(w, ind, result, zp)
            brief["mood"] = (md or {}).get("summary")
        except Exception as e:                      # the email still goes out without the extras
            print(f"morning brief: skipped ({e})")
            brief = None
        send_email(result, bt, zp, brief)


if __name__ == "__main__":
    main()
