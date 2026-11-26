# Market Radar

Scans **every US stock** after each market close and:
- finds stocks in an **EMA trend** (Close > EMA20 > EMA50 > EMA200, EMAs rising)
- finds **EMA trend + 20-day breakouts**
- lists stocks that went **up while SPY was down**, and **RS leaders** (RS rating ≥ 80 with the RS line at a 50-day high)
- ranks **sector and industry rotation** against SPY
- **emails you** the lists and **updates a website**

It runs on GitHub's servers, so your computer does not need to be on. Cost: $0 (GitHub Free + Massive free plan).

All settings (EMA lengths, price/volume filters, sectors) live in `config.yaml`.

---

## One-time setup (about 20 minutes)

### Step 1 — Get your Massive API key
1. Log in at massive.com and open **Dashboard → API Keys**.
2. Copy the key and keep it for Step 5.

### Step 2 — Create a Gmail app password (for the alert email)
1. Go to **myaccount.google.com → Security** and make sure **2-Step Verification** is ON.
2. Search "App passwords" in the account search bar and create one named `stock scanner`.
3. Copy the 16-character password and keep it for Step 5.

### Step 3 — Create a GitHub account and a repository
1. Sign up at **github.com** (free).
2. Click **+ → New repository**.
   - Name: `market-radar`
   - Choose **Public**. The free website (GitHub Pages) needs a public repository. Your API key and passwords stay hidden as "secrets".
   - Click **Create repository**.

### Step 4 — Upload the files
1. Unzip `stock-scanner.zip` on your computer.
2. On a Mac, open the unzipped folder in Finder and press **Cmd + Shift + .** so the hidden `.github` folder becomes visible.
3. On the new repository page, click **"uploading an existing file"**.
4. Select **everything inside** the `stock-scanner` folder, including `.github`, and drag it into the browser.
5. Click **Commit changes**.
6. Check that the repository now shows `.github/workflows/daily-scan.yml`. If it is missing, use **Add file → Create new file**, type the name `.github/workflows/daily-scan.yml`, and paste the file's contents.

### Step 5 — Add your secrets
In the repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add these four:

| Name | Value |
|---|---|
| `MASSIVE_API_KEY` | your Massive API key |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the 16-character Gmail app password |
| `EMAIL_TO` | where alerts go (can be the same Gmail; separate several with commas) |

### Step 6 — Turn on the website
Go to **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.

### Step 7 — Run it the first time
1. Open the **Actions** tab. If asked, click **"I understand my workflows, go ahead and enable them"**.
2. Click **Daily stock scan → Run workflow → Run workflow**.
3. The first run downloads about 15 months of history for the whole market. The free plan allows 5 requests a minute, so this takes **about 70 minutes**. Later daily runs take about 2 minutes.
4. When it finishes you get the first email, and the website is at
   `https://YOUR-GITHUB-USERNAME.github.io/market-radar/`

After that, it runs **by itself every weekday at 5:30 pm New York time**, with a backup run at 9 pm in case the data wasn't ready. On holidays it skips.

---

## Everyday use
- **Change settings:** open `config.yaml` on GitHub, click the ✏️ pencil, edit, then **Commit**. The next run uses the new settings.
- **Run it now:** Actions → Daily stock scan → Run workflow.
- **If a run fails:** GitHub emails you. Open the run under Actions to see the log.

## What the columns mean
- **RS:** relative strength rating 1–99 compared with all scanned stocks, weighted toward the last 3 months (IBD-style).
- **★:** the stock's RS line (price ÷ SPY) is at a 50-day high.
- **Vol x:** today's volume compared with the 50-day average.
- **Days in trend:** how many days in a row the EMA trend rule has been true.
- **Sector quadrants:** Improving → Leading → Weakening → Lagging. Money tends to rotate clockwise; "Improving" is where strength is starting.

## Where the data is stored
- `data/daily/YYYY/YYYY-MM-DD.csv.gz`: one file per trading day for the whole US market, about 300 KB a day. This becomes your own market database.
- `results/YYYY-MM-DD.json`: each day's scan results, so the website keeps a history you can browse by date.

## Upgrading later
If you move to a paid Massive plan, change `requests_per_minute` in `config.yaml` (for example to 100). Nothing else needs to change.

*Not financial advice.*
