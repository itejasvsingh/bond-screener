# Ledger: Indian bond screener

A personal screener for bonds listed on NSE. It shows each bond's coupon, rating, term, yield to
maturity, payout schedule and how often it actually trades, and what an investment would pay.
It does not place orders. You decide here and trade on Kite.

## Run it

    python3 server.py

Then open http://localhost:8765. The first start fetches the data from NSE and takes a few minutes.
After that it refreshes itself in the background.

Python 3.9 or newer, standard library only. `pypdf` is optional (`pip install pypdf`): without it the
server skips the issuers' ISIN filing PDFs and everything else still works.

## Where the data comes from

- Bond list and prices: NSE's public bond feed (unofficial, can change or block access).
- Government security maturity dates: the RBI's published list of outstanding securities.
- Trading history, payment history, issuer names: NSE's daily files and corporate filings.

The data is fetched on your machine and is not stored in this repository. Check NSE's terms
before you republish any of it.

## Static copy

`python3 build_static.py` writes a server-free copy to `dist/` (a data snapshot plus the page).

## Limits

Information only, not investment advice. Payment frequency and seniority are often not in NSE's
data and show as assumed. Yields for part-repaid bonds can be wrong. Issuer summaries in
`about.json` were written from general knowledge and may be out of date.
