"""Download the CSV of a saved Zacks Premium stock screen.

    python -m scanner.zacks_fetch

Needs ZACKS_USERNAME and ZACKS_PASSWORD (GitHub secrets). ZACKS_SCREEN is the name of the
saved screen under "My Screens"; if empty, the first saved screen is used.
Screens to download are listed in config.yaml (zacks: screens:); each is saved as
data/zacks/<file>_DD-MM-YYYY.csv (New York date).
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


FORBIDDEN = ("delete", "remove", "logout", "update", "save")


def human_click(page, loc, wander=True):
    """Scroll to the element, glide the mouse to a random point inside it, hesitate, click.
    Refuses to click anything that could change the account (Delete, Remove, Save, ...)."""
    label = " ".join(filter(None, [loc.inner_text(timeout=3000) if loc.count() else "",
                                   loc.get_attribute("id") or "", loc.get_attribute("class") or "",
                                   loc.get_attribute("onclick") or ""])).lower()
    if any(w in label for w in FORBIDDEN) and "btn_run_" not in label and "buttons-csv" not in label:
        raise RuntimeError(f"safety stop: refused to click '{label[:80]}'")
    loc.scroll_into_view_if_needed(timeout=10000)
    pause(0.3, 0.9)
    box = loc.bounding_box()
    if not box:
        loc.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    if wander:   # approach from below/right, away from the menu bar so no drop-down opens on the way
        page.mouse.move(x + random.uniform(40, 220), y + random.uniform(30, 140), steps=random.randint(5, 12))
    page.mouse.move(x, y, steps=random.randint(10, 25))
    pause(0.15, 0.5)
    if not _on_target(loc, x, y):
        # something is covering the target (a pop-up or an open menu): close pop-ups / step aside, come back
        dismiss_popups(page)
        calm_menus(page)
        park_mouse(page)                       # into the page body, well clear of the menu bar
        pause(0.4, 0.9)
        box = loc.bounding_box() or box
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y, steps=6)
        pause(0.2, 0.5)
        if not _on_target(loc, x, y):
            loc.click(delay=random.randint(50, 140), timeout=10000)   # let the browser aim exactly
            pause()
            return
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.14))
    page.mouse.up()
    pause()


def _on_target(loc, x, y):
    """Is the element under the mouse point the one we mean to click (or inside it)?
    For the screener (an iframe from another Zacks address) the frame's offset is measured from outside."""
    try:
        fx = fy = 0.0
        h = loc.element_handle(timeout=3000)
        fr = h.owner_frame()
        if fr and fr.parent_frame:
            fb = fr.frame_element().bounding_box()
            if fb:
                fx, fy = fb["x"], fb["y"]
        return h.evaluate("""(el, p) => {
            const top = el.ownerDocument.elementFromPoint(p[0], p[1]);
            return !!top && (top === el || el.contains(top));
        }""", [x - fx, y - fy])
    except Exception:
        return True


def human_type(page, loc, text):
    human_click(page, loc)
    loc.fill("")
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.06, 0.19) + (random.uniform(0.2, 0.5) if random.random() < 0.06 else 0))
    pause(0.4, 1.2)


def park_mouse(page):
    """Move the mouse down into the page body (away from the green menu bar) so no drop-down stays
    open - like a person reading the page after a click."""
    vw = (page.viewport_size or {"width": 1440, "height": 1000})
    page.mouse.move(random.uniform(.30, .60) * vw["width"], random.uniform(.62, .85) * vw["height"],
                    steps=random.randint(10, 20))
    pause(0.7, 1.4)


BLANK_JS = """([x, y]) => {
    const el = document.elementFromPoint(x, y);
    if (!el) return false;
    if (el.closest('a,button,input,select,textarea,label,iframe,img,video,[onclick],[role=button],nav,ul,li,.dd,ins'))
        return false;
    return ['BODY','HTML','DIV','SECTION','MAIN','HEADER','P','H1','H2','H3','SPAN','TD','TR','TABLE'].includes(el.tagName)
        && !(el.textContent || '').trim().match(/^(Sign|Log|Delete|Remove|Save|Allow|Accept)/i);
}"""


MENU_CSS = ("#primary ul.menu, #primary .mega-menu, #navbar-collapse li.with-dd > ul, #navbar-collapse .mega-menu "
            "{display:none !important; visibility:hidden !important; pointer-events:none !important;}")


def calm_menus(page):
    """Hide the green top-bar drop-down menus in THIS browser window only, so they can never pop open over
    the screener or catch a click. (Earlier runs reached the screener and then a click landed on the open
    Screening menu, which took the page back to /screening/.) Nothing is sent to Zacks."""
    try:
        page.add_style_tag(content=MENU_CSS)
    except Exception:
        pass


def click_blank(page):
    """Click an empty white spot, as you would to close a stuck drop-down menu.
    Prefers the white space to the right of the page heading (e.g. 'Zacks Stock Screener')."""
    vw = page.viewport_size or {"width": 1440, "height": 1000}
    spots = []
    head = first_visible(page, ["h1:has-text('Stock Screener')", "h2:has-text('Stock Screener')",
                                "text=Zacks Stock Screener", "h1", "h2"], 3000)
    if head:
        b = head.bounding_box()
        if b:
            for dx in (260, 340, 420, 180):
                spots.append((b["x"] + b["width"] + dx + random.uniform(-20, 20), b["y"] + b["height"] / 2))
    spots += [(random.uniform(12, 40), random.uniform(.45, .75) * vw["height"]),            # left page margin
              (vw["width"] - random.uniform(12, 40), random.uniform(.45, .75) * vw["height"])]  # right margin
    for x, y in spots:
        if 0 < x < vw["width"] and 0 < y < vw["height"]:
            try:
                if page.evaluate(BLANK_JS, [x, y]):
                    page.mouse.move(x, y, steps=random.randint(10, 20))
                    pause(0.3, 0.7)
                    page.mouse.click(x, y, delay=random.randint(50, 130))
                    print("clicked an empty spot to close menus")
                    pause(0.6, 1.2)
                    return True
            except Exception:
                pass
    print("no empty spot found - pressing Escape and moving into the page margin")
    page.keyboard.press("Escape")
    page.mouse.move(random.uniform(10, 30), random.uniform(.5, .8) * vw["height"], steps=12)
    pause(0.6, 1.2)
    return False


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


POPUP_CLOSERS = [
    "#web-notification-pop-up button.block_notification",   # Zacks "show you notifications?" -> Cancel
    ".web-notification-modal button:text-is('Cancel')",
    "#deny_cookie", "button:text-is('Deny Optional')",               # Zacks cookie banner -> Deny Optional
    "button:text-is('Reject All')", "button:has-text('Reject')", "button:has-text('Decline')",
    "button:has-text('Necessary only')", "button:has-text('No thanks')", "button:has-text('Not now')",
    "[aria-label='Close']", "button.close",
]


def dismiss_popups(page, wait=0.0):
    """Close pop-ups the way a person would: 'Cancel' on the notifications prompt, 'Reject' on cookie
    banners, the X on anything else. Never 'Allow' or 'Accept'. Returns True if something was closed."""
    closed = False
    end = time.time() + wait
    while True:
        for sel in POPUP_CLOSERS:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    pause(0.6, 1.5)                       # notice it, then react
                    loc.first.click(delay=random.randint(50, 140), timeout=3000)
                    print("closed a pop-up:", sel.split(" ")[0])
                    closed = True
                    pause(0.5, 1.2)
            except Exception:
                pass
        if closed or time.time() >= end:
            return closed
        time.sleep(0.5)


def logged_in(page):
    return page.locator("a[href*='logout']").count() > 0


def _wait_url(page, pattern, ms=30000):
    try:
        page.wait_for_url(pattern, timeout=ms)
    except Exception:
        pass


def open_screener(page):
    """Two plain clicks, like a person, no drop-down menus:
       1. 'Screening' in the green top bar -> the Screening page
       2. 'Stock Screener' (Top Zacks Features box) or 'Custom Stock Screener' -> the stock screener"""
    if "zacks.com" not in page.url:
        page.goto("https://www.zacks.com/", wait_until="domcontentloaded", timeout=60000)
        pause(3, 6)
        dismiss_popups(page)
    if "/screening" not in page.url or "stock-screener" in page.url:
        page.mouse.wheel(0, -3000)                 # top bar is at the top of the page
        dismiss_popups(page, wait=4)               # the notifications prompt appears a few seconds late
        pause(0.8, 1.6)
        top = first_visible(page, ["a:text-is('Screening')"], 8000)
        if top:
            print("clicking Screening (top bar)")
            human_click(page, top)
            _wait_url(page, "**/screening/**")
            park_mouse(page)                       # off the menu bar, or its drop-down pops open
            pause(2.5, 5)
            dismiss_popups(page)
            idle_scroll(page)                      # glance down the page like a reader
    if "stock-screener" not in page.url:
        link = first_visible(page, ["a:text-is('Stock Screener')", "a:has-text('Custom Stock Screener')"], 8000)
        if link:
            print("clicking", link.inner_text().strip())
            human_click(page, link)
            _wait_url(page, "**/stock-screener**")
            park_mouse(page)
    if "stock-screener" not in page.url:
        print("links did not open the screener - opening it directly")
        page.goto(SCREENER, referer="https://www.zacks.com/screening/", wait_until="domcontentloaded", timeout=60000)
    park_mouse(page)
    pause(3, 6)
    dismiss_popups(page)
    calm_menus(page)                               # drop-down menus can't cover the screener now
    print("on page:", page.url.split("?")[0])


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
            page.wait_for_selector("a[href*='logout']", state="attached", timeout=30000)   # signed in
        except Exception:
            pass
        pause(3, 6)
    if not logged_in(page):
        return False
    open_screener(page)
    if not screener_ready(page):
        print("signed in, but the stock screener did not load (on", page.url.split("?")[0] + ")")
        return False
    print("stock screener is loaded")
    return True


def screener_ready(page):
    """True only when the stock screener itself (its My Screen tab) is on the page - not just its address.
    (Zacks can load the address and then redirect to /screening/ a moment later.)"""
    if "stock-screener" not in page.url:
        return False
    try:
        el = page.wait_for_selector("iframe[src*='screener-api.zacks.com']", state="attached", timeout=45000)
        fr = el.content_frame()
        fr.wait_for_selector("#my-screen-tab a", timeout=45000)
        calm_menus(page)
        park_mouse(page)
        pause(2, 4)                                   # still there a moment later = no redirect
        return "stock-screener" in page.url
    except Exception:
        return False


def login(page, user, pw):
    """A real sign-in every run (at most MAX_LOGIN_TRIES attempts). Every run that reused saved cookies
    was bounced off the Premium screener; every run that signed in properly reached it."""
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


def get_screener(page):
    """The stock screener lives in an iframe that can load slowly or lazily: scroll to it, wait for it,
    and reload the page once if it never shows up."""
    for attempt in range(2):
        try:
            el = page.wait_for_selector("iframe[src*='screener-api.zacks.com']", state="attached", timeout=45000)
            el.scroll_into_view_if_needed(timeout=10000)
            pause(1, 2.5)
            fr = el.content_frame()
            if fr:
                fr.wait_for_selector("#my-screen-tab a", timeout=45000)
                return fr
        except PWTimeout:
            pass
        snap(page, f"no-screener-{attempt + 1}")
        if attempt == 0:
            print("screener box not ready - reloading the page once")
            open_screener(page)
            idle_scroll(page)
    raise RuntimeError("screener frame not found (see debug screenshots)")


NEW_CSV = "#screener_table_wrapper .buttons-csv:not([data-mr-old])"   # the CSV button of the latest results


def run_saved_screen(page, name):
    calm_menus(page)                               # no drop-down can cover the screener tabs
    park_mouse(page)
    fr = get_screener(page)
    tab = first_visible(fr, ["#my-screen-tab a", "a:text-is('My Screen')", "a:text-is('My Screens')"], 20000)
    if not tab:
        raise RuntimeError("'My Screen' tab not found")
    human_click(page, tab)
    try:
        fr.wait_for_selector("#screenlist a[id^='btn_run_']", timeout=10000)
    except PWTimeout:
        print("My Screen click did not open the list - using the screener's own My Screen action")
        snap(page, "my-screen-click-missed")
        calm_menus(page)
        fr.evaluate("() => openTab('my-screen')")
        fr.wait_for_selector("#screenlist a[id^='btn_run_']", timeout=30000)
    pause(2, 4)
    snap(page, "my-screens")
    rows = fr.locator("#screenlist tbody tr")
    names = [rows.nth(i).locator("th").inner_text().strip() for i in range(rows.count())]
    print("saved screens:", ", ".join(names))
    if name:
        idx = next((i for i, n in enumerate(names) if n.lower() == name.lower()), None)
        if idx is None:
            raise RuntimeError(f"saved screen '{name}' not found (have: {', '.join(names)})")
    else:
        idx = 0
    run = rows.nth(idx).locator("a[id^='btn_run_']")       # only ever the Run button
    print(f"running saved screen '{names[idx]}'")
    run_id = (run.get_attribute("id") or "").replace("btn_run_", "")
    # mark any results table from the previous screen, so we only accept THIS screen's new results
    fr.evaluate("() => document.querySelectorAll('.buttons-csv').forEach(b => b.setAttribute('data-mr-old', '1'))")
    human_click(page, run)
    # results are ready when the CSV button of the results table shows
    try:
        fr.wait_for_selector(NEW_CSV, state="visible", timeout=45000)
    except PWTimeout:
        if not run_id.isdigit():
            raise
        print(f"Run click did not show results - using the screener's own Run action for screen {run_id}")
        snap(page, "run-click-missed")
        calm_menus(page)
        fr.evaluate("(id) => runsavedscreen(Number(id), 1)", run_id)
        fr.wait_for_selector(NEW_CSV, state="visible", timeout=120000)
    pause(3, 6)
    try:
        print("results:", fr.locator("#runscreen_result").inner_text().split("\n")[0][:120])
    except Exception:
        pass
    snap(page, "results")
    return fr


def download_csv(page, fr, dest):
    btn = first_visible(fr, [NEW_CSV], 15000)
    if btn:
        try:
            pause(1.5, 4)          # "look" at the results first
            with page.expect_download(timeout=90000) as dl:
                human_click(page, btn)
            dl.value.save_as(str(dest))
            print("downloaded the CSV export")
            return
        except PWTimeout:
            print("CSV click gave no download - asking Zacks' export link directly")
    # same request the CSV button makes, from inside the signed-in screener page
    text = fr.evaluate("""async () => {
        const r = await fetch('https://screener-api.zacks.com/export.php', {credentials: 'include'});
        return await r.text();
    }""")
    if not text or text.lstrip().startswith("<"):
        raise RuntimeError("Zacks export returned no CSV")
    dest.write_text(text)
    print("saved the CSV from Zacks' export link")


def screens_to_fetch():
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
        lst = [x for x in (cfg.get("zacks") or {}).get("screens") or [] if x and x.get("name")]
    except Exception as e:
        print("could not read config.yaml:", e)
        lst = []
    if not lst:
        lst = [{"name": (os.environ.get("ZACKS_SCREEN") or "").strip(), "file": "zacks_rank"}]
    return [(x["name"], x.get("file") or "zacks_rank") for x in lst]


def main():
    user, pw = os.environ.get("ZACKS_USERNAME"), os.environ.get("ZACKS_PASSWORD")
    if not user or not pw:
        sys.exit("ZACKS_USERNAME / ZACKS_PASSWORD secrets are not set")
    screens = screens_to_fetch()
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
    (OUT / "latest.csv").unlink(missing_ok=True)      # no longer used: the newest dated file is read
    saved, failed = [], []
    with sync_playwright() as p:
        headed = os.environ.get("ZACKS_HEADED") == "1"     # a normal (visible) browser on a virtual screen
        print("browser:", "normal window (virtual screen)" if headed else "headless")
        browser = p.chromium.launch(headless=not headed, args=["--disable-blink-features=AutomationControlled",
                                                                "--window-size=1440,1000"])
        ua = None if headed else (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                  f"Chrome/{browser.version} Safari/537.36")     # honest: matches the real machine
        ctx = browser.new_context(user_agent=ua,      # fresh browser: saved cookies get bounced off the screener viewport={"width": 1440, "height": 1000},
                                  locale="en-US", timezone_id="America/New_York", accept_downloads=True)
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            login(page, user, pw)
            for k, (name, prefix) in enumerate(screens):
                dest = OUT / f"{prefix}_{day}.csv"       # e.g. zacks_rank_27-11-2026.csv (New York date)
                try:
                    if k:
                        pause(4, 9)                       # a person would take a moment between screens
                    fr = run_saved_screen(page, name)
                    download_csv(page, fr, dest)
                    text = dest.read_text(errors="replace")
                    if text.lstrip().startswith("<"):
                        dest.unlink()
                        raise RuntimeError("got a web page instead of a CSV (signed out?)")
                    saved.append(f"{dest.name}: {text.count(chr(10))} lines")
                except Exception as e:
                    snap(page, f"error-{prefix}")
                    failed.append(f"{name}: {e}")
        except Exception as e:
            snap(page, "error")
            browser.close()
            sys.exit(f"Zacks download failed: {e}")
        browser.close()
    for x in saved:
        print("saved data/zacks/" + x)
    if failed:
        sys.exit("some screens failed:\n  " + "\n  ".join(failed))


if __name__ == "__main__":
    main()
