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

## Bond research assistant

Open **Ask Ledger** in the screener to ask questions about bonds, issuers, ratings, coupons, maturities
and yields. Retrieval runs locally against the current NSE bond snapshot and issuer notes, and shows
the records it used. With no model configured, answers remain in local retrieval mode and do not call
an AI service.

To enable generated answers on Vercel, add an `OPENAI_API_KEY` environment variable in the Vercel
project settings, then redeploy. The default endpoint is OpenAI chat completions using `gpt-4o-mini`.
You can optionally override it with `RAG_API_URL`, `RAG_MODEL`, and `RAG_API_KEY`. For another
OpenAI-compatible provider, configure all three. The server sends only the question and retrieved
excerpts to the model endpoint; credentials stay on the server and are never sent to the browser. Locally:

    RAG_API_URL=http://localhost:11434/v1/chat/completions RAG_MODEL=llama3.2 python3 server.py

Generated responses are grounded in retrieved excerpts but can still be wrong. The assistant is for
information only, not investment advice.

## Where the data comes from

- Bond list and prices: NSE's public bond feed (unofficial, can change or block access).
- Government security maturity dates: the RBI's published list of outstanding securities.
- Trading history, payment history, issuer names: NSE's daily files and corporate filings.

The data is fetched on your machine and is not stored in this repository. Check NSE's terms
before you republish any of it.

## Static copy

`python3 build_static.py` writes a server-free copy to `dist/` (a data snapshot plus the page).
The Vercel snapshot also bundles the chat function and its read-only data snapshot; a plain static
copy outside Vercel has no chat API.

## Automatic daily snapshot

A GitHub Actions workflow (`.github/workflows/refresh.yml`) runs every weekday at 20:00 IST. It runs
`python build_static.py --refresh` and publishes the result to the `vercel-snapshot` branch. Vercel builds
that branch into a preview that only the account owner can open. `main` is never deployed
(`vercel.json` skips its builds). To run it by hand: Actions tab, "Refresh snapshot", "Run workflow".

## Limits

Information only, not investment advice. Payment frequency and seniority are often not in NSE's
data and show as assumed. Yields for part-repaid bonds can be wrong. Issuer summaries in
`about.json` were written from general knowledge and may be out of date.
