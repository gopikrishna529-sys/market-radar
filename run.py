"""Entry point.

    python run.py            daily: new day(s) -> scan -> backtest -> website -> email (only if a new day arrived)
    python run.py --force    manual run: also fills older history back to config history.start, then rebuilds all
    python run.py --no-email skip sending the email
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


def main():
    force = "--force" in sys.argv
    client = Massive(base_url=CONFIG["api"]["base_url"],
                     requests_per_minute=CONFIG["api"]["requests_per_minute"])
    added = update_prices(client)
    if force:
        added += backfill_history(client)
    if not added and not force:
        print("No new trading day - nothing to do.")
        return
    update_reference(client)

    w = load_panel()
    print(f"loaded {len(w['close'])} days x {w['close'].shape[1]} tickers")
    ind = compute(w)
    result = run_scan(w, ind)
    print(f"{fmt_date(result['date'])}: {len(result['trend'])} in trend, {len(result['breakouts'])} breakouts, "
          f"{len(result['rs_days'])} up-while-SPY-down, {len(result['rs_leaders'])} RS leaders")
    bt = run_backtest(w, ind)
    build_site(w, ind)
    if "--no-email" not in sys.argv:
        send_email(result, bt)


if __name__ == "__main__":
    main()
