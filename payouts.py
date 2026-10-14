"""Observed interest-payment frequency, from NSE's debt announcements.

Issuers must file a "Confirmation of Redemption/Payment of Interest" for every payment. Each one
names the ISIN and the due date. Two or more due dates for the same ISIN show how often it
actually pays: roughly 30 days apart is monthly, 91 quarterly, 182 semi-annual, 365 annual.
Only gaps that were actually observed are used. Nothing is guessed.
"""
import json, os, re, statistics, sys, time
from datetime import date, datetime, timedelta
from urllib.request import Request

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(ROOT, "data", "payouts.json")
LOOKBACK_DAYS = 240   # long enough to see >=2 payments of anything paying at least semi-annually
CHUNK_DAYS = 15
URL = ("https://www.nseindia.com/api/corporate-announcements?index=debt&from_date=%s&to_date=%s")
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
LINE = re.compile(r"Nature of Payment\s*-\s*([^,]+?)\s*,\s*Due date\s*-\s*(\d{2}-[A-Za-z]{3}-\d{4}).*?ISIN\s*-\s*(IN[A-Z0-9]{10})", re.S)


def load():
    try:
        return json.load(open(STORE))
    except (OSError, ValueError):
        return {"through": None, "dues": {}}


def classify(dates):
    """Median gap between distinct due dates -> payments per year, or None when unclear."""
    ds = sorted({date.fromisoformat(d) for d in dates})
    if len(ds) < 2:
        return None
    gap = statistics.median((b - a).days for a, b in zip(ds, ds[1:]))
    for lo, hi, f in ((25, 36, 12), (80, 100, 4), (165, 200, 2), (340, 390, 1)):
        if lo <= gap <= hi:
            return f
    return None


def frequencies(store=None):
    store = store or load()
    out = {}
    for isin, dates in store["dues"].items():
        f = classify(dates)
        if f:
            out[isin] = {"freq": f, "dates": sorted(set(dates))[-4:]}
    return out


def update(opener):
    """Fetch announcements since the last run (or LOOKBACK_DAYS on the first run) and extend the store."""
    store = load()
    today = date.today()
    start = date.fromisoformat(store["through"]) - timedelta(days=1) if store["through"] else today - timedelta(days=LOOKBACK_DAYS)
    dues = {k: set(v) for k, v in store["dues"].items()}
    cur = start
    while cur <= today:
        end = min(cur + timedelta(days=CHUNK_DAYS - 1), today)
        req = Request(URL % (cur.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y")),
                      headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": REFERER})
        rows = json.loads(opener.open(req, timeout=90).read().decode("utf-8"))
        for r in rows if isinstance(rows, list) else []:
            for nature, due, isin in LINE.findall(r.get("attchmntText") or ""):
                if "interest" in nature.lower():
                    dues.setdefault(isin, set()).add(datetime.strptime(due, "%d-%b-%Y").date().isoformat())
        print("payouts: %s..%s %d filings" % (cur, end, len(rows)), file=sys.stderr)
        cur = end + timedelta(days=1)
        time.sleep(1)
    store = {"through": today.isoformat(), "dues": {k: sorted(v) for k, v in dues.items()}}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(store, open(STORE, "w"), separators=(",", ":"))
    return store
