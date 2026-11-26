"""Daily entry point.

    python run.py            update data -> scan -> website -> email (only if a new trading day arrived)
    python run.py --force    re-run the scan/website/email on the latest stored day
    python run.py --no-email skip sending the email
"""
import sys

from scanner.config import CONFIG
from scanner.data_store import update_prices, update_reference
from scanner.massive import Massive
from scanner.report import build_site, send_email
from scanner.scan import run_scan


def main():
    force = "--force" in sys.argv
    client = Massive(base_url=CONFIG["api"]["base_url"],
                     requests_per_minute=CONFIG["api"]["requests_per_minute"])
    added = update_prices(client)
    if not added and not force:
        print("No new trading day - nothing to do.")
        return
    update_reference(client)
    result = run_scan()
    print(f"{result['date']}: {len(result['trend'])} in trend, {len(result['breakouts'])} breakouts, "
          f"{len(result['rs_days'])} up-while-SPY-down, {len(result['rs_leaders'])} RS leaders")
    build_site(result)
    if "--no-email" not in sys.argv:
        send_email(result)


if __name__ == "__main__":
    main()
