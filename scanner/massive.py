"""Small Massive (formerly Polygon.io) REST client with free-tier rate limiting."""
import os
import time

import requests


class MassiveError(RuntimeError):
    pass


class Massive:
    def __init__(self, api_key=None, base_url="https://api.massive.com", requests_per_minute=5):
        self.api_key = api_key or os.environ.get("MASSIVE_API_KEY")
        if not self.api_key:
            raise MassiveError("MASSIVE_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        self.min_interval = 60.0 / max(requests_per_minute, 1) + 0.5
        self._last_call = 0.0
        self.session = requests.Session()

    def _wait_turn(self):
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get(self, url_or_path, params=None):
        url = url_or_path if url_or_path.startswith("http") else self.base_url + url_or_path
        params = dict(params or {})
        params["apiKey"] = self.api_key
        for attempt in range(6):
            self._wait_turn()
            try:
                r = self.session.get(url, params=params, timeout=60)
            except requests.RequestException as e:
                print(f"  network error ({e}); retrying")
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code == 429:
                print("  rate limited; waiting 65s")
                time.sleep(65)
                continue
            if r.status_code >= 500:
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise MassiveError(f"{r.status_code} not authorized for {url}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        raise MassiveError(f"giving up on {url}")

    def get_all(self, path, params=None, max_pages=50):
        """Follow next_url pagination and return all results."""
        out, data, pages = [], self.get(path, params), 1
        out.extend(data.get("results") or [])
        while data.get("next_url") and pages < max_pages:
            data = self.get(data["next_url"])
            out.extend(data.get("results") or [])
            pages += 1
        return out

    # --- endpoints -------------------------------------------------------
    def grouped_daily(self, date_str):
        """All US stocks' daily bar for one date. Unadjusted (we apply splits ourselves)."""
        data = self.get(f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
                        {"adjusted": "false", "include_otc": "false"})
        return data.get("results") or []

    def tickers(self, ticker_type):
        return self.get_all("/v3/reference/tickers",
                            {"market": "stocks", "type": ticker_type, "active": "true", "limit": 1000})

    def splits(self, since_date):
        return self.get_all("/v3/reference/splits",
                            {"execution_date.gte": since_date, "limit": 1000,
                             "order": "asc", "sort": "execution_date"})
