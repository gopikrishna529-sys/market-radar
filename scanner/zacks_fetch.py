"""Download the CSV of a saved Zacks Premium stock screen.

    python -m scanner.zacks_fetch

Needs ZACKS_USERNAME and ZACKS_PASSWORD (GitHub secrets). ZACKS_SCREEN is the name of the
saved screen under "My Screens"; if empty, the first saved screen is used.
Saves data/zacks/zacks_rank_DD-MM-YYYY.csv (New York date) and data/zacks/latest.csv.
On any failure it saves screenshots and page HTML in debug/ so the problem can be seen.
"""
import csv
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "zacks"
DEBUG = ROOT / "debug"
SCREENER = "https://www.zacks.com/screening/stock-screener"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
STATE = ROOT / ".zacks_state" / "state.json"      # saved sign-in cookies (encrypted by the workflow)
FAIL_MARK = ROOT / "data" / "zacks" / "LOGIN_FAILED"
MAX_LOGIN_TRIES = 2
step_no = 0


def pause(lo=0.8, hi=2.4):
    """Random human-like wait between actions."""
    time.sleep(random.uniform(lo, hi))


def human_click(page, loc):
    """Scroll to the element, glide the mouse to a random point inside it, hesitate, click."""
    loc.scroll_into_view_if_needed(timeout=10000)
    pause(0.3, 0.9)
    box = loc.bounding_box()
    if not box:
        loc.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    page.mouse.move(x + random.uniform(-200, 200), y + random.uniform(-120, 120), steps=random.randint(5, 12))
    page.mouse.move(x, y, steps=random.randint(10, 25))
    pause(0.15, 0.5)
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.14))
    page.mouse.up()
    pause()


def human_type(page, loc, text):
    human_click(page, loc)
    loc.fill("")
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.06, 0.19) + (random.uniform(0.2, 0.5) if random.random() < 0.06 else 0))
    pause(0.4, 1.2)


def idle_scroll(page):
    for _ in range(random.randint(1, 3)):
        page.mouse.wheel(0, random.randint(120, 420))
        pause(0.4, 1.1)
    page.mouse.wheel(0, -2000)
    pause(0.5, 1.2)


def snap(page, name):
    """Screenshot + HTML of the page (and the screener frame) for debugging."""
    global step_no
    step_no += 1
    DEBUG.mkdir(exist_ok=True)
    tag = f"{step_no:02d}-{name}"
    try:
        page.screenshot(path=str(DEBUG / f"{tag}.png"), full_page=True)
        (DEBUG / f"{tag}.html").write_text(page.content())
        fr = screener_frame(page, wait=False)
        if fr:
            (DEBUG / f"{tag}-screener.html").write_text(fr.content())
    except Exception as e:  # debugging must never hide the real error
        print("  (debug snapshot failed:", e, ")")
    print(f"step {tag}")


def screener_frame(page, wait=True):
    for _ in range(40 if wait else 1):
        for f in page.frames:
            if "screener-api.zacks.com" in f.url:
                return f
        if wait:
            time.sleep(0.5)
    return None


def first_visible(scope, selectors, timeout=8000):
    end = time.time() + timeout / 1000
    while time.time() < end:
        for s in selectors:
            loc = scope.locator(s)
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        time.sleep(0.3)
    return None


def dismiss_popups(page):
    for s in ["button:has-text('Accept')", "button:has-text('I Agree')", "button:has-text('No thanks')",
              "[aria-label='Close']", "button.close"]:
        try:
            loc = page.locator(s)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
        except Exception:
            pass


def logged_in(page):
    return page.locator("a[href*='logout']").count() > 0


def open_screener(page):
    page.goto(SCREENER, wait_until="domcontentloaded", timeout=60000)
    pause(3, 6)
    dismiss_popups(page)


def login_once(page, user, pw):
    """One sign-in attempt, done the way a person would: home page, Sign In, type, click Login."""
    page.goto("https://www.zacks.com/", wait_until="domcontentloaded", timeout=60000)
    pause(3, 6)
    dismiss_popups(page)
    idle_scroll(page)
    if not logged_in(page):
        user_box = first_visible(page, ["input[name='username']", "input#username", "input[type='email']"], 2000)
        if not user_box:
            link = first_visible(page, ["a:has-text('Sign In')", "a:has-text('Log In')", "a:has-text('Sign in')"], 10000)
            if not link:
                raise RuntimeError("could not find the Sign In link")
            human_click(page, link)
            pause(1.5, 3)
            user_box = first_visible(page, ["input[name='username']", "input#username", "input[type='email']"], 10000)
        if not user_box:
            raise RuntimeError("could not find the username box")
        human_type(page, user_box, user)
        pw_box = first_visible(page, ["input[name='password']", "input[type='password']"], 5000)
        human_type(page, pw_box, pw)
        btn = first_visible(page, ["button:has-text('Login')", "button:has-text('Log In')", "button:has-text('Sign In')",
                                   "input[type='submit']", "button[type='submit']"], 4000)
        snap(page, "login-filled")
        if btn:
            human_click(page, btn)
        else:
            pw_box.press("Enter")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=60000)
        except PWTimeout:
            pass
        pause(4, 7)
    open_screener(page)
    return logged_in(page)


def login(page, user, pw):
    """Reuse saved cookies if they still work; otherwise at most MAX_LOGIN_TRIES real sign-ins."""
    open_screener(page)
    if logged_in(page):
        print("still signed in from last time (saved cookies) - no login needed")
        return
    for attempt in range(1, MAX_LOGIN_TRIES + 1):
        print(f"signing in (attempt {attempt} of {MAX_LOGIN_TRIES})")
        try:
            if login_once(page, user, pw):
                print("signed in to Zacks")
                return
        except PWTimeout as e:
            print("  timed out:", str(e).splitlines()[0])
        snap(page, f"login-failed-{attempt}")
        if attempt < MAX_LOGIN_TRIES:
            pause(25, 50)       # a person would wait a bit before trying again
    FAIL_MARK.parent.mkdir(parents=True, exist_ok=True)
    FAIL_MARK.write_text(datetime.now(ZoneInfo("America/New_York")).strftime("%d-%b-%y %H:%M New York\n"))
    raise RuntimeError(f"login failed {MAX_LOGIN_TRIES} times - stopped. Automatic runs are paused until you "
                       "press 'Run workflow' yourself (see debug screenshots)")


def run_saved_screen(page, name):
    fr = screener_frame(page)
    if not fr:
        raise RuntimeError("screener frame not found")
    tab = first_visible(fr, ["text=My Screens", "text=My Screen", "a:has-text('MY SCREEN')"], 20000)
    if not tab:
        raise RuntimeError("'My Screens' tab not found")
    human_click(page, tab)
    pause(2, 4)
    snap(page, "my-screens")
    if name:
        row = first_visible(fr, [f"tr:has-text('{name}')", f"li:has-text('{name}')", f"div:has-text('{name}') >> nth=-1"], 15000)
        if not row:
            raise RuntimeError(f"saved screen '{name}' not found under My Screens")
        run = row.locator("a:has-text('Run'), button:has-text('Run'), input[value*='Run' i]")
        human_click(page, run.first if run.count() else fr.get_by_text(name, exact=True).first)
    else:
        run = first_visible(fr, ["a:has-text('Run')", "button:has-text('Run')", "input[value*='Run' i]"], 15000)
        if not run:
            raise RuntimeError("no saved screen with a Run button found")
        human_click(page, run)
    # results appear in a table in the same frame
    pause(6, 10)
    fr = screener_frame(page)
    fr.wait_for_selector("table tbody tr", timeout=90000)
    snap(page, "results")
    return fr


def download_csv(page, fr, dest):
    btn = first_visible(fr, ["a:has-text('CSV')", "button:has-text('CSV')", "[title*='CSV' i]",
                             "a:has-text('Export')", "button:has-text('Export')", "[class*='csv' i]"], 15000)
    if btn:
        try:
            pause(1.5, 4)          # "look" at the results first
            with page.expect_download(timeout=60000) as dl:
                human_click(page, btn)
            dl.value.save_as(str(dest))
            print("downloaded the CSV export")
            return
        except PWTimeout:
            print("CSV button gave no download - reading the results table instead")
    else:
        print("no CSV button found - reading the results table instead")
    rows = fr.evaluate("""() => {
        const t = [...document.querySelectorAll('table')].sort((a,b)=>b.rows.length-a.rows.length)[0];
        if (!t) return [];
        return [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim()));
    }""")
    if len(rows) < 2:
        raise RuntimeError("results table is empty")
    with open(dest, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"saved {len(rows) - 1} rows from the results table (only the rows shown on the page)")


def main():
    user, pw = os.environ.get("ZACKS_USERNAME"), os.environ.get("ZACKS_PASSWORD")
    if not user or not pw:
        sys.exit("ZACKS_USERNAME / ZACKS_PASSWORD secrets are not set")
    name = (os.environ.get("ZACKS_SCREEN") or "").strip()
    manual = "--manual" in sys.argv
    if FAIL_MARK.exists() and not manual:
        print(f"Zacks login failed on {FAIL_MARK.read_text().strip()}; skipping automatic runs so the account "
              "is not locked. Press 'Run workflow' to try again.")
        return
    if FAIL_MARK.exists():
        FAIL_MARK.unlink()
    if not manual:
        wait = random.randint(0, 9 * 60)       # don't sign in at exactly the same second every day
        print(f"waiting {wait // 60} min {wait % 60} s before starting")
        time.sleep(wait)
    day = datetime.now(ZoneInfo("America/New_York")).strftime("%d-%m-%Y")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"zacks_rank_{day}.csv"      # e.g. zacks_rank_27-11-2026.csv (New York date)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(storage_state=str(STATE) if STATE.exists() else None, user_agent=UA, viewport={"width": 1440, "height": 1000},
                                  locale="en-US", timezone_id="America/New_York", accept_downloads=True)
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            login(page, user, pw)
            fr = run_saved_screen(page, name)
            download_csv(page, fr, dest)
            STATE.parent.mkdir(exist_ok=True)
            ctx.storage_state(path=str(STATE))      # keep cookies so tomorrow needs no login
        except Exception as e:
            snap(page, "error")
            browser.close()
            sys.exit(f"Zacks download failed: {e}")
        browser.close()
    text = dest.read_text(errors="replace")
    if text.lstrip().startswith("<"):
        sys.exit("Zacks returned a web page instead of a CSV (probably signed out)")
    (OUT / "latest.csv").write_text(text)
    print(f"saved {dest.relative_to(ROOT)}: {text.count(chr(10))} lines")


if __name__ == "__main__":
    main()
