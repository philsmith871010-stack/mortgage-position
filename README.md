# Mortgage position — mock-up

What a mortgage deal is worth on today's market, with every figure traceable to the borrower's contract, an offer they hold, or published Bank of England data. No forecasting.

**Live page:** `index.html` — a single self-contained file. Open it locally or serve it from GitHub Pages.

## What's in here

| Path | What it is |
|---|---|
| `index.html` | The site. Engine and data are inlined; no build step, no database. |
| `engine/engine.py` | The valuation engine: amortisation, total cost of ownership with terminal balance, forward-implied refinancing off the BoE OIS curve, relock option, break-even solvers. |
| `engine/test_engine.py` | 21 tests. `python -m pytest engine/test_engine.py` |
| `web/engine.js` | JavaScript port of the engine, verified against the Python to the pound. |
| `web/ui_template.html` | Page template. `index.html` = template with `engine.js` and the product JSON inlined. |
| `data/lender_products_2026-09.csv` | Lender product snapshot. Every row carries `as_of`, `source` and `confidence`. |
| `data/nationwide_20260915_products.csv` | Nationwide's full range parsed from their intermediary product guide dated 15 Sep 2026. |

## Data status (mock-up)

- **Market**: BoE quoted household rates (75% LTV) and BoE OIS curve, month-average, August 2026. Hard-coded in the page; editable under "Market inputs".
- **Lenders**: Nationwide and TSB in full from their own guides dated 15 Sep 2026. Halifax, first direct and Coventry are dated best-buy/press points, older and partial. Labelled as such on the page.
- **Not yet**: NatWest, Santander, Barclays, HSBC parsers; automated weekly refresh; historical view.

## Method, in one paragraph

Cost of a route over a horizon = present value of payments + fees paid upfront + the balance still owed at the end, discounted at the borrower's marginal cost of money (default: BoE quoted 5y). Routes are compared by that total. Where a future rate is needed it is the forward swap read off the OIS curve plus a stated lender spread (default: trailing 12-month average of quoted rate minus swap). The free relock option (lock up to 6 months early, re-book if rates fall) is credited at its measured historical value, ~25bp, net of friction, on future fixes only.

## Caveats

Lender product guides are marked "for intermediary use only". The lender panel is appropriate for internal and broker-facing use; a consumer-facing build should strip it or wrap it appropriately. This is a comparison tool, not advice.
