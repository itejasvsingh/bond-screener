"""Payment frequency as stated in issuers' half-yearly ISIN statements filed with NSE.

SEBI requires every issuer of listed debt to file, twice a year, a statement listing each ISIN with its
coupon and payment frequency ("Chapter VIII - Specifications related to ISIN for debt"). Layouts differ
by issuer, so the reader looks at the text after each ISIN, up to the next ISIN, and takes the first
frequency word it finds. Where that window holds no frequency word, the ISIN is left out.
"""
import io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.request import Request, urlopen

try:
    import pypdf
except ImportError:  # optional: without it the filing PDFs are skipped, everything else still works
    pypdf = None

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(ROOT, "data", "filings.json")
ISIN = re.compile(r"\bIN[A-Z0-9]{10}\b")
WORDS = (  # most specific first
    (re.compile(r"semi[\s-]*annual|half[\s-]*year|six[\s-]*month|bi[\s-]*annual", re.I), 2),
    (re.compile(r"quarterly", re.I), 4),
    (re.compile(r"monthly", re.I), 12),
    (re.compile(r"once\s+a\s+year|annual|yearly", re.I), 1),
    (re.compile(r"cumulative|on\s+maturity|at\s+maturity|zero\s*coupon|bullet", re.I), 0),
)
WINDOW = 220


def text_of(pdf_bytes):
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)


def parse(text):
    """ISIN -> payments per year (12, 4, 2, 1) or 0 for maturity-only, from the first frequency word after it."""
    text = re.sub(r"\s+", " ", text)
    hits = list(ISIN.finditer(text))
    out = {}
    for i, m in enumerate(hits):
        end = min(m.end() + WINDOW, hits[i + 1].start() if i + 1 < len(hits) else len(text))
        win = text[m.end():end]
        best = None
        for rx, f in WORDS:
            k = rx.search(win)
            if k and (best is None or k.start() < best[0]):
                best = (k.start(), f)
        if best:
            out[m.group(0)] = best[1]
    return out


def update(opener, days=210):
    """Read every ISIN statement filed in the last `days` days. Later filings overwrite earlier ones."""
    if pypdf is None:
        print("filings: pypdf is not installed for this Python, skipping the ISIN filing PDFs (pip install pypdf)", file=sys.stderr)
        return load()
    hdr = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
           "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}
    today, cur, files = date.today(), date.today() - timedelta(days=days), {}
    while cur <= today:
        end = min(cur + timedelta(days=14), today)
        url = "https://www.nseindia.com/api/corporate-announcements?index=debt&from_date=%s&to_date=%s" % (
            cur.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))
        rows = json.loads(opener.open(Request(url, headers=hdr), timeout=90).read().decode("utf-8"))
        for r in rows if isinstance(rows, list) else []:
            f = r.get("attchmntFile") or ""
            if "Chapter VIII" in (r.get("desc") or "") and f.lower().endswith(".pdf"):
                files[f] = r.get("sort_date") or r.get("an_dt") or ""
        cur = end + timedelta(days=1)
        time.sleep(0.5)

    def one(item):
        url, when = item
        try:
            return when, parse(text_of(urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=90).read()))
        except Exception as e:
            print("filing skipped:", url[-50:], str(e)[:60], file=sys.stderr)
            return when, {}

    with ThreadPoolExecutor(6) as ex:
        results = sorted(ex.map(one, files.items()), key=lambda r: r[0])
    merged = {}
    for _, found in results:
        merged.update(found)
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    json.dump({"asOf": date.today().isoformat(), "files": len(files), "freq": merged}, open(STORE, "w"), separators=(",", ":"))
    return merged


def load():
    try:
        return json.load(open(STORE)).get("freq", {})
    except (OSError, ValueError):
        return {}
