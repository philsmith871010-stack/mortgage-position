# CLAUDE.md — Mortgage position

Read this before touching anything. It explains what this project is, the decisions already made and why, and the rules every change must respect.

## What this is

A tool that tells a UK mortgage borrower what their deal is worth on today's market, and what would have to be true for a different choice to be better. It is built on arithmetic over three things: the borrower's own contract, an offer they hold, and published Bank of England data. It does not forecast interest rates. Ever.

Owner: Phil — UK local authority treasury and structured-finance specialist, ~20 years. Talk to him as a peer; he will check the maths.

Two audiences, one engine:
- **Consumer face**: BoE market average as the benchmark, the user's own offers as inputs.
- **Intermediary face**: adds lender-level product data (parsed from lenders' public intermediary product guides). Those guides are marked "for intermediary use only", so this layer is a build flag, not something that leaks into the consumer build.

## Decisions already made — do not reopen without asking

1. **No prediction.** Where a future rate is needed, use the forward rate read off the BoE OIS curve plus a stated lender spread. That is the market's price, not our view. The P50 of anything forward-looking must equal the forward.
2. **Break-even, not a verdict.** The headline is "rates would need to move X for the other choice to win", never "you should do Y". This is both the honest output and the one that stays on the right side of the FCA advice boundary.
3. **Three outputs, not one number.** Locked value (deterministic), payment shock at reversion (£/month), break-even (bp). A single "your mortgage is worth £N" figure was considered and rejected: it launders uncertain inputs through confident arithmetic.
4. **TCO with terminal balance.** Routes are compared on present value of payments + upfront fees + balance still owed at the horizon, discounted at the borrower's marginal cost of money (default: BoE quoted 5y). Terminal balance is what makes routes with different amortisation comparable. Never compare payment streams alone.
5. **Relock option is credited, at its measured value.** Borrowers can lock a new deal ~6 months early and re-book if rates fall. Measured on BoE data 2009–2026: ~25bp average saving, positive 80% of months. Credit 25bp minus 5bp friction, on forward-implied legs only, never on a rate already held. Without this the 2y-vs-5y comparison is biased toward the longer fix.
6. **Calibrate on post-2022 only.** Pre-2019 the lender spread was a one-way structural grind (330bp → 80bp) and the swap explained nothing. Post-2022 the spread is stationary (~55bp, half-life <1 month) and the swap explains ~90% of 12–24m variance. Pass-through is asymmetric: rises pass through in a month, falls take ~6 months. Any spread model must be one-sided and jump-aware.
7. **Every number shows its provenance.** Data rows carry `as_of`, `source`, `confidence`. The page states where each figure comes from. This is not decoration.

## Repo layout

```
index.html              built site: web/ui_template.html with engine.js + product JSON inlined. Self-contained.
engine/engine.py        THE maths. One source of truth.
engine/test_engine.py   21 tests. Must pass before any commit touching engine/.
web/engine.js           JS port. Must reproduce engine.py to the pound on the worked example.
web/ui_template.html    page template; /*ENGINE*/ is the inline marker.
data/                   product snapshots (CSV + JSON) and parsed lender sheets.
ingest/                 (to build) BoE fetch + one parser per lender + schema.
```

## Conventions

- Rates are annual %, money GBP, time in months. Monthly compounding of rate/12 (UK retail convention). BoE OIS zeros are continuously compounded; forward zero between a and b years = (b·s_b − a·s_a)/(b−a).
- Payment is recalculated over the *remaining* term at each product switch (UK product-transfer convention).
- Fee added to loan by default; upfront is a flag.
- ERC schedules are % of balance by fix year, e.g. `[5,4,3,2,1]`.
- Product schema: `lender, as_of, segment (remortgage | existing-switch | purchase | benchmark), ltv_max, fix_years, rate, fee, loan_min, loan_max, code, erc, svr, source, confidence (full-sheet | best-buy-point | press-point | official-average)`.
- When you change engine.py, rerun the tests, then re-verify engine.js against `engine/worked_example` numbers (5y cheaper by £2,347; break-even −40bp; relock PV £930/£2,081; inertia £9,104; Case B stay cheaper by £4,217; break-even offer 3.69%). If those move, say so and why.
- Rebuild `index.html` by inlining engine.js and `data/lender_products_*.json` into the template. Never hand-edit index.html.

## Data status

- Market: BoE quoted household rates (IUMBV34 = 2y, IUMBV37 = 3y, IUMBV42 = 5y, IUMBV45 = 10y, all 75% LTV; IUMTLMV = SVR) and BoE OIS spot curve, month-average, to Aug 2026. Hard-coded in the page for now.
- Lenders: Nationwide and TSB in full from their guides dated 15 Sep 2026. Halifax, first direct, Coventry are partial dated points. Not yet: NatWest, Santander, Barclays, HSBC.
- Known public sources: Nationwide (single PDF + historic archive), NatWest (dated PDFs, yearly archive, per-reprice change log), Santander (numbered bulletins + archive), Barclays (stable-URL PDF, snapshot it), HSBC (12-month archive on product page), TSB (dated PDF). Halifax is JS-rendered — needs a headless browser.

## Guardrails

- Never invent or interpolate a lender rate. If a product isn't in a parsed sheet, it isn't in the data. Placeholder rows must carry a low `confidence` and say where they came from.
- A parser that returns materially fewer rows than last run must fail the job, not publish.
- Do not add anything that forecasts, predicts, or "expects" a rate. If a feature needs a future rate, it uses the forward plus the stated spread, and the page says so.
- Do not add persuasive or recommendation language to the UI. The tone is a blunt friend who works in finance, not a compliance leaflet and not a salesperson.
- Keep `index.html` self-contained: no external scripts except Google Fonts, no API calls, no localStorage beyond remembering inputs.
- Do not remove the plain-English guide or the provenance footer.

## Next tasks, in order

1. `ingest/schema.py` + `ingest/lenders/natwest.py` and `santander.py` with tests against sample PDFs committed to `data/samples/`. Both lenders publish per-reprice change logs — use them to verify the diff.
2. `ingest/boe.py`: pull quoted rates + OIS zip, emit the monthly market table. Replace the hard-coded market block in the template with a generated one.
3. Weekly job: fetch → parse → diff vs last snapshot → rebuild → deploy to Pages. Fail loudly on parse-count drops.
4. Historical view: run the engine month-by-month against the BoE series so a user sees how their deal tracked the market since they took it, with attribution (rate move / spread move / time decay / LTV migration).
5. Halifax via headless browser.
