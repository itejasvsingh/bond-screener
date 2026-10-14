"""Issuer names and issue descriptions from NSE's "CBM - Security Master" (Negotiated Trade Reporting Platform).

One row per listed bond: full issuer name, issue description (e.g. "Axis 7.88% 2032 Sr 30 Tier 2"), coupon
frequency, rating column, dates. Published daily; we keep the latest copy.
"""
import csv, io, json, os, re, time
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(ROOT, "data", "issuers.json")
URL = "https://nsearchives.nseindia.com/content/debt/Corporate_bond_report_%s.csv"


def load():
    try:
        return json.load(open(STORE))
    except (OSError, ValueError):
        return {}


def fetch():
    raw = None
    for k in range(7):  # weekends and holidays have no file
        d = date.today() - timedelta(days=k)
        try:
            raw = urlopen(Request(URL % d.strftime("%d-%b-%Y"), headers={"User-Agent": "Mozilla/5.0"}), timeout=90).read()
            break
        except HTTPError as e:
            if e.code not in (403, 404):
                raise
    if raw is None:
        raise RuntimeError("no security master file in the last 7 days")
    lines = raw.decode("utf-8-sig", "ignore").splitlines()
    head = next(i for i, l in enumerate(lines) if l.startswith("Sectype"))
    out = {}
    for r in csv.DictReader(io.StringIO("\n".join(lines[head:]))):
        r = {k: (v or "").strip() for k, v in r.items() if k}
        if r.get("ISIN"):
            out[r["ISIN"]] = {"issuer": r.get("Issuer", ""), "desc": r.get("Issue Desc", ""),
                              "cf": r.get("Coupon Frequency", ""), "type": r.get("Sectype", "")}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump(out, open(STORE, "w"), separators=(",", ":"))
    return out
