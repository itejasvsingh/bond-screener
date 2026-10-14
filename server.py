#!/usr/bin/env python3
"""Local server for the bond screener.

Serves index.html and /api/bonds. The browser cannot call NSE directly (CORS and bot
protection), so this fetches NSE's public bond feeds with a session cookie, normalises
them, caches the result on disk and serves it. No third-party packages needed.

    python3 server.py            # then open http://localhost:8765
"""
import html, json, os, re, sys, threading, time
import filings, issuers, liquidity, payouts
from datetime import date, datetime
from http.cookiejar import CookieJar
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlparse, parse_qs
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "bonds.json")
RBI_CACHE = os.path.join(ROOT, "data", "rbi_gsec.json")
RBI_URL = "https://rbi.org.in/Scripts/bs_viewcontent.aspx?Id=1956"  # list of outstanding G-Secs: ISIN, issue and maturity dates
PORT = int(os.environ.get("PORT", 8765))
TTL = 600  # seconds before a background refresh is triggered
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BASE = "https://www.nseindia.com"
FEEDS = {
    "corp": BASE + "/api/liveBonds-traded-on-cm?type=bonds",
    "gsec": BASE + "/api/liveBonds-traded-on-cm?type=gsec",
}
PSU = ("NHAI", "IRFC", "REC", "PFC", "HUDCO", "NHB", "NABARD", "SIDBI", "EXIM", "NTPC", "PGCIL",
       "POWERGRID", "IOC", "IOCL", "ONGC", "NPCIL", "NLC", "SAIL", "BHEL", "CONCOR", "IRCON", "RVNL",
       "NHPC", "SJVN", "HPCL", "BPCL", "GAIL", "AAI", "DMRC", "KRCL", "IIFCL", "NABFID", "MTNL", "NMDC",
       "COAL", "OIL", "BSNL", "HAL", "BEL", "MMTC", "IREDA", "IFCI", "TNEB", "NTPCL")
RATING_RE = re.compile(r"\b(AAA|AA\+|AA-|AA|A\+|A-|A|BBB\+|BBB-|BBB|BB\+|BB-|BB|B\+|B-|B|C|D)\b")
RANK = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "C", "D"]

_lock = threading.Lock()
_state = {"data": None, "fetching": False, "error": None, "obs": {}, "obs_running": False, "obs_error": None, "liq": {}, "liq_sessions": 0, "iss": {}, "last_job": 0.0}


def num(v):
    try:
        f = float(str(v).replace(",", ""))
        return f
    except (TypeError, ValueError):
        return 0.0


def pdate(s):
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date()
    except (AttributeError, ValueError):
        return None


def fetch_json(opener, url):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                "Referer": BASE + "/market-data/bonds-traded-in-capital-market"})
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_rating(text, agency):
    """'AAA/Stable, AA+/Stable' -> ('AA+', 'CRISIL, ICRA'). Takes the lowest rating, to be conservative."""
    found = []
    for part in (text or "").split(","):
        head = part.split("/")[0].strip().upper()
        m = RATING_RE.search(head)
        if m:
            found.append(m.group(1))
    if not found:
        return None, agency or None
    return max(found, key=RANK.index), agency or None


def issuer_of(sym):
    return re.sub(r"^[0-9]+|[0-9]+$", "", sym or "")


def rbi_maturities():
    """ISIN -> exact maturity date for outstanding G-Secs, from RBI's published list. Cached for a week."""
    try:
        if time.time() - os.path.getmtime(RBI_CACHE) < 7 * 86400:
            return json.load(open(RBI_CACHE))
    except (OSError, ValueError):
        pass
    try:
        req = Request(RBI_URL, headers={"User-Agent": UA})
        page = build_opener().open(req, timeout=40).read().decode("utf-8", "ignore")
        out = {}
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
            c = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", x))).strip()
                 for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
            if len(c) >= 5 and re.fullmatch(r"IN\w{10}", c[1]):
                out[c[1]] = datetime.strptime(c[4], "%d-%b-%Y").date().isoformat()
        if len(out) > 50:
            with open(RBI_CACHE, "w") as f:
                json.dump(out, f)
            return out
    except Exception as e:
        print("RBI list unavailable, G-Sec dates stay estimated:", e, file=sys.stderr)
    try:  # fall back to an older copy rather than nothing
        return json.load(open(RBI_CACHE))
    except (OSError, ValueError):
        return {}


def norm_corp(x, today):
    mat = pdate(x.get("maturity_date"))
    if not mat or mat <= today or mat.year > 2100:
        return None
    ltp, close = num(x.get("ltP")), num(x.get("close"))
    price, src = (ltp, "live") if ltp else ((close, "close") if close else (0, None))
    rating, agency = parse_rating(x.get("credit_rating"), x.get("rating_agency"))
    nxt = pdate(x.get("nxtip_date"))
    cpn, derived = num(x.get("coupr")), False
    if not cpn and str(x.get("coupr") or "").strip() in ("", "-"):
        # NSE leaves the coupon blank for some bonds, but the symbol starts with it: 901HUDCO34 = 9.01%
        m = re.match(r"^(\d{2,4})[A-Z]", x.get("symbol", ""))
        if m:
            cpn, derived = int(m.group(1)) / {2: 10, 3: 100, 4: 1000}[len(m.group(1))], True
    sym = x.get("symbol", "")
    iss = issuer_of(sym)
    return {
        "sym": sym, "isin": x.get("isin"), "series": x.get("series"), "name": iss,
        "kind": "CORP", "cat": "PSU" if any(iss.startswith(p) for p in PSU) else "Corporate",
        "face": num(x.get("face_value")) or 1000, "price": price or None, "src": src,
        "chg": num(x.get("per")), "cpn": cpn, "cpnDerived": derived, "mat": mat.isoformat(), "matApprox": False,
        "rating": rating, "agency": agency, "qty": num(x.get("qty")), "val": num(x.get("trdVal")),
        "hi": num(x.get("high")) or None, "lo": num(x.get("low")) or None, "freq": 1,
        "nxt": nxt.isoformat() if nxt and nxt > today else None,  # next interest date, when NSE has a current one
    }


def norm_gsec(x, today, rbi=None):
    series, sym = x.get("series"), x.get("symbol", "")
    price, src = 0, None
    for key, label in (("lastPrice", "live"), ("closePrice", "close"), ("previousClose", "prev")):
        if num(x.get(key)):
            price, src = num(x.get(key)), label
            break
    base = {
        "sym": sym, "isin": x.get("isinCode"), "series": series, "face": num(x.get("faceValue")) or 100,
        "price": price or None, "src": src, "chg": num(x.get("pChange")), "rating": "SOV", "agency": None,
        "qty": num(x.get("totalTradedVolume")), "val": num(x.get("totalTradedValue")),
        "hi": num(x.get("dayHigh")) or None, "lo": num(x.get("dayLow")) or None,
    }
    if series == "TB":
        m = re.match(r"^(\d+)D(\d{2})(\d{2})(\d{2})$", sym)
        if not m:
            return None
        try:
            mat = date(2000 + int(m.group(4)), int(m.group(3)), int(m.group(2)))
        except ValueError:
            return None
        if mat <= today:
            return None
        return {**base, "name": "T-Bill %sd" % m.group(1), "kind": "TB", "cat": "Sovereign", "cpn": 0.0,
                "mat": mat.isoformat(), "matApprox": False, "freq": 2}
    m = re.match(r"^(\d{2,3})(GS|[A-Z]+)(\d{2}|\d{4})$", sym)
    if not m:
        return None
    digits, tag, yr = m.groups()
    cpn = int(digits) / (100 if len(digits) == 3 else 10)
    year = int(yr) if len(yr) == 4 else 2000 + int(yr)
    listed = pdate(x.get("listingDate")) or today
    # NSE gives only the year in the symbol; borrow day/month from the listing date (approximate).
    try:
        mat = date(year, listed.month, listed.day)
    except ValueError:
        mat = date(year, listed.month, 28)
    exact = (rbi or {}).get(base["isin"]) if series == "GS" else None
    if exact:
        mat = date.fromisoformat(exact)
    if mat <= today:
        return None
    return {**base, "name": x.get("companyName") or tag, "kind": "GS" if series == "GS" else "SDL",
            "cat": "Sovereign" if series == "GS" else "State", "cpn": cpn, "mat": mat.isoformat(),
            "matApprox": not exact, "freq": 2}


def nse_opener():
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    for url in (BASE + "/", BASE + "/companies-listing/corporate-filings-announcements"):
        try:  # these often answer 403 but still set the cookies the API needs
            opener.open(Request(url, headers={"User-Agent": UA}), timeout=30).read()
        except HTTPError:
            pass
    return opener


CARRY_DAYS = 14  # keep a bond this long after NSE last listed it


def carry_forward(fresh, today):
    """During trading hours NSE lists only the bonds with activity that day, and the complete list returns after
    the close. Keep bonds the feed omits for now, with their last known price marked as a previous close, so the
    list doesn't shrink every morning. Matured bonds, and ones unseen for CARRY_DAYS, are dropped."""
    prev = _state["data"]
    if prev is None:
        try:
            prev = json.load(open(CACHE))
        except (OSError, ValueError):
            return fresh, 0
    have = {b["isin"] for b in fresh}
    kept = []
    for b in prev.get("bonds", []):
        if b["isin"] in have:
            continue
        seen = date.fromisoformat(b.get("seen") or prev["asOf"][:10])
        if date.fromisoformat(b["mat"]) <= today or (today - seen).days > CARRY_DAYS:
            continue
        kept.append({**b, "seen": seen.isoformat(), "src": "prev" if b.get("price") else b.get("src"),
                     "chg": 0.0, "qty": 0.0, "val": 0.0, "carried": True})
    return fresh + kept, len(kept)


def build():
    opener = nse_opener()
    today = date.today()
    out, counts = [], {}
    rbi = rbi_maturities()
    for key, fn in (("corp", norm_corp), ("gsec", lambda r, t: norm_gsec(r, t, rbi))):
        rows = fetch_json(opener, FEEDS[key]).get("data", [])
        items = [b for b in (fn(r, today) for r in rows) if b and b["isin"]]
        counts[key] = len(items)
        out += items
    if not out:
        raise RuntimeError("NSE returned no bonds")
    for b in out:
        b["seen"] = today.isoformat()
    out, carried = carry_forward(out, today)
    counts["carried"] = carried
    return {"asOf": datetime.now().isoformat(timespec="seconds"), "source": "NSE", "counts": counts, "bonds": out}


def refresh(blocking=False):
    with _lock:
        if _state["fetching"]:
            return
        _state["fetching"] = True

    def work():
        try:
            data = build()
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            _state.update(data=data, error=None)
            print("refreshed", data["counts"], file=sys.stderr)
        except Exception as e:  # keep serving the last good data
            _state["error"] = "%s: %s" % (type(e).__name__, e)
            print("refresh failed:", _state["error"], file=sys.stderr)
        finally:
            _state["fetching"] = False

    if blocking:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def sec_hint(desc):
    """Seniority stated in the issue description itself, e.g. 'Axis 7.88% 2032 Sr 30 Tier 2'. Otherwise None."""
    d = " " + (desc or "").lower() + " "
    if re.search(r"perp|\bat ?1\b|tier[\s-]*(i|1)\b(?!i)", d):
        return "Perpetual / AT1"
    if re.search(r"tier[\s-]*(ii|2)\b|\bt2\b|subordinat", d):
        return "Subordinated (Tier II)"
    return None


def refresh_liquidity(download=True):
    store = liquidity.update() if download else liquidity.load()
    _state["liq"] = liquidity.summary(store)
    _state["liq_sessions"] = min(len(store["days"]), liquidity.SESSIONS)


JOB_EVERY = 6 * 3600  # NSE publishes each day's files after the close, so check a few times a day


def daily_due():
    """True when the slow jobs (payment history, trading history, issuer names, filings) should run again."""
    return not _state["iss"] or time.time() - _state["last_job"] > JOB_EVERY


def scheduler():
    """Keeps a long-running server fresh: every 15 minutes, re-run the slow jobs if due and warm the bond list."""
    while True:
        time.sleep(900)
        try:
            if daily_due():
                refresh_payouts()
            current()  # refreshes the bond list itself when it is older than TTL
        except Exception as e:
            print("scheduler:", type(e).__name__, e, file=sys.stderr)


def refresh_payouts():
    """Build the observed-payment-frequency table in the background (first run takes a few minutes)."""
    if _state["obs_running"]:
        return
    _state["obs_running"] = True
    _state["last_job"] = time.time()

    def work():
        try:
            payouts.update(nse_opener())
            _state["obs"] = payouts.frequencies()
            if time.time() - _mtime(filings.STORE) > 3 * 86400:
                filings.update(nse_opener())
            refresh_liquidity()
            _state["iss"] = issuers.fetch()
            _state["obs_error"] = None
            print("payout frequencies observed for", len(_state["obs"]), "bonds", file=sys.stderr)
        except Exception as e:
            _state["obs_error"] = "%s: %s" % (type(e).__name__, e)
            print("payout refresh failed:", _state["obs_error"], file=sys.stderr)
        finally:
            _state["obs_running"] = False

    threading.Thread(target=work, daemon=True).start()


def with_observed(d):
    obs, filed = _state["obs"], filings.load()
    bonds = []
    for b in d["bonds"]:
        extra = {}
        if b["isin"] in obs:
            extra.update(fObs=obs[b["isin"]]["freq"], fObsDates=obs[b["isin"]]["dates"])
        i = _state["iss"].get(b["isin"])
        if i:
            extra.update(issuer=i["issuer"], issueDesc=i["desc"])
            if sec_hint(i["desc"]):
                extra["secHint"] = sec_hint(i["desc"])
        if _state["liq_sessions"]:
            extra["liq"] = _state["liq"].get(b["isin"]) or {"days": 0, "turnover": 0, "trades": 0, "last": None,
                                                           "sessions": _state["liq_sessions"]}
        if filed.get(b["isin"]) in (1, 2, 4, 12):  # 0 (maturity-only wording) was unreliable against real payments
            extra["fFile"] = filed[b["isin"]]
        bonds.append({**b, **extra} if extra else b)
    return {**d, "bonds": bonds, "observed": len(obs), "payoutsRunning": _state["obs_running"],
            "payoutsError": _state["obs_error"]}


def current(force=False):
    if _state["data"] is None and os.path.exists(CACHE):
        try:
            _state["data"] = json.load(open(CACHE))
        except (OSError, ValueError):
            pass
    d = _state["data"]
    stale = d is None or force or (time.time() - datetime.fromisoformat(d["asOf"]).timestamp()) > TTL
    if d is None or force:
        refresh(blocking=True)
    elif stale:
        refresh()
    d = _state["data"]
    if d is None:
        return {"error": _state["error"] or "no data yet", "bonds": []}
    return with_observed({**d, "error": _state["error"]})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/bonds":
            body = json.dumps(current(force="refresh" in parse_qs(u.query))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif u.path in ("/", "/index.html", "/about.json"):
            self.path = "/index.html" if u.path != "/about.json" else "/about.json"
            super().do_GET()
        else:  # nothing else is public: no server.py, no data/, no legacy/
            self.send_error(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    st = payouts.load()
    _state["obs"] = payouts.frequencies(st)
    refresh_liquidity(download=False)
    _state["iss"] = issuers.load()
    _state["last_job"] = _mtime(payouts.STORE)
    if daily_due():
        refresh_payouts()
    threading.Thread(target=scheduler, daemon=True).start()
    print("Bond screener on http://localhost:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
