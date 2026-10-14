#!/usr/bin/env python3
"""Build a static copy of the screener into dist/ for hosting without a server.

    python3 build_static.py            # uses the data already on this machine
    python3 build_static.py --refresh  # first refreshes payment history, trading history, issuers and filings

Pulls fresh data through the same code the local server uses, writes it to dist/bonds.json, and copies
index.html with three changes: it reads the snapshot instead of /api/bonds, the Refresh link is hidden,
and a not-advice notice is added. dist/ is marked noindex, so it stays out of search engines until you
remove that line from this script.
"""
import json, os, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import filings, issuers, liquidity, payouts, server  # noqa: E402

DIST = os.path.join(ROOT, "dist")
NOTICE = ("Information only, not investment advice. Prices and yields come from NSE’s public bond feed, may be delayed, "
          "and can be wrong, especially for part-repaid bonds. Check the issue document and your broker before you trade.")


def swap(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("build_static: expected exactly one %r in index.html, found %d" % (old[:60], text.count(old)))
    return text.replace(old, new)


MIN_BONDS = 3000  # a normal day lists ~4,700. Fewer means a blocked or partial fetch: never publish that.


def refresh_sources():
    """The slow jobs the local server runs in the background. Each one is incremental, so a cached data/ keeps this short."""
    opener = server.nse_opener()
    payouts.update(opener)
    liquidity.update()
    issuers.fetch()
    if filings.pypdf is not None:
        filings.update(opener)


def main():
    if "--refresh" in sys.argv:
        refresh_sources()
    server._state["obs"] = payouts.frequencies()
    server._state["iss"] = issuers.load()
    server.refresh_liquidity(download=False)
    data = server.current(force=True)
    if len(data.get("bonds", [])) < MIN_BONDS:
        raise SystemExit("build_static: only %d bonds fetched (need %d), not publishing: %s"
                         % (len(data.get("bonds", [])), MIN_BONDS, data.get("error")))
    # the snapshot only needs what the page reads
    data = {k: data[k] for k in ("asOf", "bonds", "observed", "error") if k in data}
    data["payoutsRunning"] = False

    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, "bonds.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    shutil.copy(os.path.join(ROOT, "about.json"), os.path.join(DIST, "about.json"))

    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    html = swap(html, "fetch('/api/bonds' + (force ? '?refresh=1' : ''))", "fetch('bonds.json')")
    html = swap(html, "fetch('/about.json')", "fetch('about.json')")
    html = swap(html, 'id="refreshBtn"', 'id="refreshBtn" hidden')
    html = swap(html, "Nothing here places a real order.</span>", "Nothing here places a real order. " + NOTICE + "</span>")
    html = swap(html, '<meta name="viewport"', '<meta name="robots" content="noindex">\n<meta name="viewport"')
    open(os.path.join(DIST, "index.html"), "w", encoding="utf-8").write(html)
    size = os.path.getsize(os.path.join(DIST, "bonds.json")) / 1e6
    print("dist/ ready: %d bonds, as of %s, bonds.json %.1f MB" % (len(data["bonds"]), data["asOf"], size))


if __name__ == "__main__":
    main()
