"""Trading history for NSE-listed bonds, from NSE's daily CM bhavcopy (the order book Kite trades on).

Each trading day NSE publishes one file with every security that traded: volume, turnover and trade
count. Keeping the last ~45 days shows how often a bond actually trades, which one day's snapshot can't.
"""
import csv, io, json, os, re, sys, time, zipfile
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(ROOT, "data", "liquidity.json")
URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_%s_F_0000.csv.zip"
KEEP = 45          # calendar days of history to keep
SESSIONS = 30      # most recent sessions used for the score
BOND_SERIES = re.compile(r"^(GS|TB|SG|N[0-9A-Z])$")


def load():
    try:
        return json.load(open(STORE))
    except (OSError, ValueError):
        return {"days": {}}


def fetch_day(d):
    """{isin: [volume, turnover_rupees, trades]} for one date, or None if NSE has no file (weekend, holiday, not yet out)."""
    try:
        raw = urlopen(Request(URL % d.strftime("%Y%m%d"), headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    except HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    z = zipfile.ZipFile(io.BytesIO(raw))
    out = {}
    for r in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
        if BOND_SERIES.match(r["SctySrs"].strip()):
            try:
                out[r["ISIN"].strip()] = [float(r["TtlTradgVol"] or 0), float(r["TtlTrfVal"] or 0), int(float(r["TtlNbOfTxsExctd"] or 0))]
            except ValueError:
                pass
    return out


def update():
    store, today = load(), date.today()
    days = store["days"]
    for k in range(KEEP + 1):
        d = today - timedelta(days=k)
        key = d.isoformat()
        if d.weekday() >= 5 or key in days:
            continue
        got = fetch_day(d)
        if got is not None:
            days[key] = got
        time.sleep(0.3)
    cutoff = (today - timedelta(days=KEEP)).isoformat()
    store["days"] = {k: v for k, v in days.items() if k >= cutoff}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(store, open(STORE, "w"), separators=(",", ":"))
    return store


def summary(store=None):
    """isin -> {days, sessions, turnover, trades, last}, over the latest SESSIONS sessions."""
    store = store or load()
    keys = sorted(store["days"])[-SESSIONS:]
    out = {}
    for k in keys:
        for isin, (vol, val, n) in store["days"][k].items():
            o = out.setdefault(isin, {"days": 0, "turnover": 0.0, "trades": 0, "last": k})
            if n > 0 or vol > 0:
                o["days"] += 1
                o["turnover"] += val
                o["trades"] += n
                o["last"] = k
    for o in out.values():
        o["sessions"] = len(keys)
        o["turnover"] = round(o["turnover"])
    return out
