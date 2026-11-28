"""ETF tab: every listed ETF with category, issuer, leverage, what it tracks (for bundles) and performance / risk.

Bundles put every fund on the same underlying together: NVDA -> NVDL (2x), NVDU (2x), NVD (-2x), NVDS (-1.5x),
NVDY (covered-call income) ...; QQQ -> TQQQ (3x), QLD (2x), SQQQ (-3x), PSQ (-1x), QQQY (income) ...
"Decay" compares a leveraged fund's 3-month return with leverage x the underlying's 3-month return.
"""
import re

import numpy as np
import pandas as pd

from . import jsonsafe
from .data_store import load_all_tickers

FUND_TYPES = ("ETF", "ETV", "ETN", "ETS")

ISSUERS = ["iShares", "Vanguard", "SPDR", "Invesco", "ProShares", "Direxion", "GraniteShares", "YieldMax", "Roundhill",
           "Defiance", "T-Rex", "Tradr", "Leverage Shares", "Global X", "ARK", "VanEck", "First Trust", "Schwab",
           "WisdomTree", "JPMorgan", "Fidelity", "Franklin", "Pacer", "Amplify", "Simplify", "Volatility Shares",
           "REX", "Subversive", "Unusual Whales", "Grayscale", "Bitwise", "Avantis", "Dimensional", "Goldman Sachs",
           "Xtrackers", "Columbia", "Neos", "NEOS", "Kurv", "Tuttle", "AXS", "MicroSectors", "Bank of Montreal", "iPath",
           "Themes", "Kraneshares", "KraneShares", "Sprott", "abrdn", "Aberdeen", "Teucrium", "United States", "Nuveen",
           "Capital Group", "Alpha Architect", "Cambria", "Strive", "Texas Capital", "Matthews", "Hartford", "Putnam",
           "BlackRock", "State Street", "Janus", "PIMCO", "Eaton Vance", "Morgan Stanley", "Western Asset", "Virtus",
           "Innovator", "Calamos", "FT Vest", "AllianceBernstein", "American Century", "Principal", "John Hancock",
           "Main Street", "Adaptive", "Tidal", "Toroso", "Ninepoint", "Horizon", "Grizzle", "Distillate", "Sterling",
           "Vaneck", "Hashdex", "CoinShares", "21Shares", "Franklin Templeton", "Canary", "Valkyrie", "Osprey"]
ISSUER_ALIAS = {"Vaneck": "VanEck", "Kraneshares": "KraneShares", "NEOS": "Neos", "Aberdeen": "abrdn",
                "State Street": "SPDR", "Franklin Templeton": "Franklin", "Bank of Montreal": "MicroSectors (BMO)"}

# name -> underlying for single-stock funds that spell out the company
COMPANY = {r"nvidia": "NVDA", r"tesla": "TSLA", r"apple\b": "AAPL", r"microsoft": "MSFT", r"amazon": "AMZN",
           r"\bmeta\b|facebook": "META", r"alphabet|google": "GOOGL", r"netflix": "NFLX", r"palantir": "PLTR",
           r"coinbase": "COIN", r"microstrategy|\bstrategy inc": "MSTR", r"advanced micro|\bamd\b": "AMD",
           r"broadcom": "AVGO", r"super micro": "SMCI", r"robinhood": "HOOD", r"taiwan semi": "TSM",
           r"eli lilly": "LLY", r"berkshire": "BRK.B", r"uber\b": "UBER", r"snowflake": "SNOW", r"shopify": "SHOP",
           r"rivian": "RIVN", r"intel\b": "INTC", r"oracle": "ORCL", r"salesforce": "CRM", r"boeing": "BA",
           r"alibaba": "BABA", r"micron": "MU", r"arm holdings": "ARM", r"gamestop": "GME", r"amc ent": "AMC"}
INDEXES = [(r"nasdaq[- ]?100|\bqqq\b|\bndx\b|innovation 100", "QQQ"), (r"s&p ?500|\bspy\b|\bspx\b|\b500\b", "SPY"),
           (r"russell 2000|\bsmall[- ]?cap 2000|\biwm\b", "IWM"), (r"\bdow\b|dow jones industrial|\bdia\b", "DIA"),
           (r"semiconductor|\bsoxx?\b|\bchip", "SOXX"), (r"bitcoin|\bbtc\b", "BTC"), (r"\bether(eum)?\b|\beth\b", "ETH"),
           (r"solana", "SOL"), (r"\bxrp\b", "XRP"), (r"gold miners", "GDX"), (r"\bgold\b", "GLD"), (r"\bsilver\b", "SLV"),
           (r"natural gas", "UNG"), (r"crude|\boil\b", "USO"), (r"20\+? ?year treasury|long[- ]term treasury|\btlt\b", "TLT"),
           (r"\bvix\b|volatility", "VIX"), (r"financial", "XLF"), (r"energy", "XLE"), (r"biotech", "XBI"),
           (r"technology|\btech\b", "XLK"), (r"real estate", "XLRE"), (r"regional bank", "KRE"), (r"\bchina\b", "FXI"),
           (r"homebuilder", "XHB"), (r"aerospace|defense", "ITA"), (r"retail", "XRT"), (r"health ?care", "XLV")]
UNDERLYING_FUND = {"BTC": "IBIT", "ETH": "ETHA", "SOL": "SOLZ", "XRP": "XRPI", "VIX": "VIXY", "SOXX": "SOXX"}

CATS = [("Politicians, insiders & gurus", r"congress|democrat|republican|pelosi|unusual whales|political|insider|guru|"
                                         r"hedge fund|13f|smart money|buyback|activist"),
        ("Income / covered-call", r"option income|covered call|premium income|buy-?write|yieldmax|income strategy|"
                                  r"enhanced income|weekly (?:pay|distribution)|weeklypay|option strategy|target income|\bput ?write|"
                                  r"growth & income|& income|\bincome etf|yield shares|dividend target"),
        ("Crypto", r"bitcoin|ether(?:eum)?\b|crypto|solana|\bxrp\b|blockchain|\bbtc\b|digital asset"),
        ("Volatility", r"\bvix\b|volatility"),
        ("Bonds & cash", r"treasury|\bbond|t-bill|\bbill\b|muni|aggregate|corporate|high yield|fixed income|duration|"
                         r"floating rate|\bclo\b|mortgage|tips\b|income fund|money market|ultra-short|credit"),
        ("Commodities", r"\bgold\b|silver|\boil\b|crude|natural gas|commodit|copper|uranium|agricult|platinum|palladium|"
                        r"\bcorn\b|wheat|soybean|metals"),
        ("International", r"emerging|international|developed|\bchina\b|japan|india|europe|eafe|brazil|korea|taiwan|"
                          r"ex-us|ex us|\bworld\b|global|mexico|canada|germany|\buk\b|asia|latin|africa|frontier|"
                          r"australia|vietnam|israel|saudi|turkey|greece|argentina|poland"),
        ("Sectors & industries", r"technology|semiconductor|financial|health|biotech|pharma|energy|utilit|real estate|"
                                 r"reit|industrial|materials|consumer|communication|software|bank|insurance|"
                                 r"aerospace|defense|homebuilder|retail|transport|infrastructure|medical"),
        ("Dividend", r"dividend|\bdiv\b|yield"),
        ("Thematic", r"innovation|\bai\b|artificial intelligence|robot|cloud|cyber|clean|solar|space|genomic|meme|"
                     r"sentiment|esports|gaming|cannabis|lithium|electric vehicle|\bev\b|nuclear|quantum|drone|internet|"
                     r"fintech|metaverse|disrupt|future|nextgen|water|hydrogen|battery|robotics"),
        ("Broad market & factors", r"s&p|total (?:stock )?market|nasdaq|russell|\bdow\b|large[- ]?cap|mid[- ]?cap|"
                                   r"small[- ]?cap|equal weight|growth|value|momentum|quality|low vol|core|broad|"
                                   r"\b500\b|\b1000\b|\b2000\b|multifactor|factor")]


def _issuer(name):
    for i in ISSUERS:
        if re.search(r"\b" + re.escape(i) + r"\b", name, re.I):
            return ISSUER_ALIAS.get(i, i)
    w0 = name.split(" ")[0] if name else ""
    return "" if re.fullmatch(r"-?\d.*", w0) else w0


def _leverage(name):
    n = name.lower()
    m = re.search(r"(-?\d(?:\.\d+)?)\s?x\b", n)
    lev = abs(float(m.group(1))) if m else (3.0 if "ultrapro" in n else 2.0 if re.search(r"\bultra(short)?\b", n) else 1.0)
    if re.search(r"\bshort\b(?!\s*-?\s*(?:term|duration|maturity|dated|treasury))|\bbear\b|inverse|ultrashort", n):
        lev = -lev
    return lev


def _underlying(name, ticker, stock_set):
    n = name.lower()
    # an explicit stock ticker in the name: "GraniteShares 2x Long NVDA Daily ETF"
    for tok in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b", name):
        if tok in stock_set and tok not in ("ETF", "ETN", "II", "US", "USA", "AI", "DAILY", "ARK", "REX", "NEOS", "MSCI",
                                            "S", "P", "X", "T", "A", "SPDR", "ESG", "QQQ", "SPY", "IWM", "DIA", ticker):
            return tok
    for pat, u in COMPANY.items():
        if re.search(pat, n):
            return u
    for pat, u in INDEXES:
        if re.search(pat, n):
            return u
    return None


def _cat(name, lev, und, stock_set, issuer=""):
    n = name.lower().replace(issuer.lower(), " ") if issuer else name.lower()   # "Global X" is not "global"
    if lev != 1:
        return "Leveraged & inverse"
    for c, pat in CATS:
        if re.search(pat, n):
            if c == "Dividend" and "income" in n and re.search(CATS[1][1], n):
                return CATS[1][0]
            return c
    return "Single stock" if und in stock_set else "Other"


def _r(x, n=1):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, n) if np.isfinite(x) else None


def build(w, ind, out_path, rs_all=None):
    tick = load_all_tickers().drop_duplicates("ticker")
    stock_set = set(tick.loc[tick["type"].isin(["CS", "ADRC"]), "ticker"])
    funds = tick[tick["type"].isin(FUND_TYPES)].set_index("ticker")
    c, v = w["close"], w["volume"]
    funds = funds[funds.index.isin(c.columns)]
    cols = list(funds.index)
    C = c[cols]
    last_i = C.apply(lambda s: s.last_valid_index())
    end = c.index[-1]
    alive = [t for t in cols if last_i.get(t) is not None and (end - last_i[t]).days <= 7]
    C = C[alive].ffill(limit=3)
    V = v[alive]
    n = len(C)
    lastp = C.iloc[-1]

    def ret(k):
        return (lastp / C.iloc[-1 - k] - 1) * 100 if n > k else pd.Series(np.nan, index=C.columns)
    yr0 = C[C.index.year == end.year]
    ytd_base = C[C.index.year < end.year].iloc[-1] if (C.index.year < end.year).any() else yr0.iloc[0]
    hi = C.iloc[-252:].max()
    dret = C.pct_change(fill_method=None)
    vol = dret.iloc[-63:].std() * np.sqrt(252) * 100
    w1y = C.iloc[-252:]
    mdd = ((w1y / w1y.cummax()) - 1).min() * 100
    first = C.apply(lambda s: s.first_valid_index())
    jump = dret.iloc[-252:].abs().max()                    # a >150% one-day move is almost always an unadjusted split
    adv = (C.iloc[-20:] * V.iloc[-20:]).mean() / 1e6
    lt, stt = ind["long_trend"].iloc[-1], ind["short_trend"].iloc[-1]
    R = {k: ret(k) for k in (1, 5, 21, 63, 126, 252)}
    ytd = (lastp / ytd_base - 1) * 100
    rows = []
    for t in alive:
        name = str(funds.at[t, "name"] or "")
        lev = _leverage(name)
        und = _underlying(name, t, stock_set)
        iss = _issuer(name)
        cat = _cat(name, lev, und, stock_set, iss)
        income = bool(re.search(CATS[1][1], name.lower()))
        short = re.sub(r"(?i)^.*?\b(?:ETF Trust(?: II| III)?|Series Trust(?: II)?|Trust II|ETF Series Solutions|ETF Opportunities Trust)\s+(?=\S)", "", name)
        rows.append({"t": t, "n": re.sub(r"\s+", " ", short if len(short) > 8 else name).strip()[:90],
                     "is": iss, "cat": cat, "lev": lev, "u": und, "inc": income,
                     "px": _r(lastp[t], 2), "d1": _r(R[1][t], 2), "w1": _r(R[5][t]), "m1": _r(R[21][t]), "m3": _r(R[63][t]),
                     "m6": _r(R[126][t]), "y1": _r(R[252][t]) if first[t] is not None and (end - first[t]).days >= 360 else None,
                     "ytd": _r(ytd[t]), "hi": _r((lastp[t] / hi[t] - 1) * 100), "vol": _r(vol[t], 0), "dd": _r(mdd[t], 0),
                     "adv": _r(adv[t], 2), "tr": "L" if bool(lt.get(t, False)) else "S" if bool(stt.get(t, False)) else "",
                     "age": int((end - first[t]).days) if first[t] is not None else None,
                     "x": 1 if jump.get(t, 0) > 1.5 else 0})
    df = pd.DataFrame(rows).set_index("t")
    # leverage decay: leveraged fund 3M return vs leverage x underlying 3M return
    base = {}
    for u in df["u"].dropna().unique():
        b = UNDERLYING_FUND.get(u, u)
        if b in c.columns and np.isfinite(c[b].iloc[-1]):
            base[u] = b
    for t, r in df.iterrows():
        u = r["u"]
        if u in base and r["lev"] != 1 and r["m3"] is not None:
            bs = c[base[u]]
            if n > 63 and np.isfinite(bs.iloc[-64]):
                exp = (bs.iloc[-1] / bs.iloc[-64] - 1) * 100 * r["lev"]
                df.at[t, "dec"] = _r(r["m3"] - exp)
    df["base"] = df["u"].map(lambda u: base.get(u))
    out = df.reset_index().replace({np.nan: None}).to_dict("records")
    out_path.write_text(jsonsafe.dumps({"date": end.strftime("%Y-%m-%d"), "funds": out}, separators=(",", ":")))
    print(f"ETF tab: {len(out)} funds, {df['u'].notna().sum()} with an identified underlying")
    return out
