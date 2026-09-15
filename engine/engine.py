"""
Mortgage decision engine.

Everything here is arithmetic on (a) the borrower's own contract terms, (b) an offer
they hold, and (c) published market data. No forecast, no house view, no volatility
surface. Every number can be reproduced from public inputs.

Core method
-----------
A "route" is a sequence of legs (rate, months, fees). The cost of a route over a
horizon H is its Total Cost of Ownership:

    TCO = PV(monthly payments to H) + PV(upfront costs) + PV(balance outstanding at H)

Including the terminal balance is what makes routes with different amortisation
comparable. Two routes are compared by TCO difference; a positive number means the
first route is more expensive.

Forward-implied refinancing
---------------------------
Where a route needs a rate the borrower does not yet hold (e.g. the 2-year fix they
would take in two years' time), it is read off today's market:

    forward mortgage rate = forward swap (from the BoE OIS curve) + assumed lender spread

The forward swap is a market price, not a prediction. The spread is a stated, visible
assumption (default: trailing 12-month average of BoE quoted rate minus swap).

Relock option
-------------
UK borrowers can lock a new deal up to ~6 months before their fix ends and switch to a
better one if rates fall before it starts. Historically that has been worth ~25bp on
the refinanced rate (measured on BoE data 2009-2026). It is credited to forward-implied
legs as a rate reduction, net of a friction haircut. It is NOT credited to a rate the
borrower already holds.

Conventions
-----------
Rates are annual percentages (4.78 means 4.78%). Money is GBP. Time is in months.
UK lenders recalculate the payment at each product switch over the *remaining* term;
we do the same. Interest is monthly compounding of rate/12, which is the standard
retail convention. OIS zero rates from the BoE are continuously compounded; the
forward zero between a and b years is (b*s_b - a*s_a)/(b-a).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

import numpy as np
from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Loan:
    balance: float          # outstanding today, GBP
    term_months: int        # remaining contractual term, months


@dataclass(frozen=True)
class Leg:
    """One product period."""
    rate: float                     # annual %
    months: int
    fee: float = 0.0                # product fee
    fee_added_to_loan: bool = True  # else paid upfront
    upfront_costs: float = 0.0      # valuation, legal, booking, exit fees: cash at leg start
    label: str = ""
    forward_implied: bool = False   # rate came from the forward curve (eligible for relock credit)


@dataclass(frozen=True)
class Market:
    ois: dict                       # {tenor_years: spot zero %} continuously compounded
    quoted: dict                    # {fix_years: BoE quoted mortgage rate %} at borrower's LTV band
    spread_bp: float                # lender spread over swap for forward-implied rates
    svr: float                      # reversion rate if nothing is done
    discount_rate: Optional[float] = None   # annual %; None -> quoted 5y (borrower's marginal cost of money)
    relock_bp: float = 25.0         # empirical value of the 6-month relock option
    relock_friction_bp: float = 5.0 # haircut for booking fees / re-application friction
    apply_relock: bool = True

    @property
    def disc(self) -> float:
        return self.discount_rate if self.discount_rate is not None else self.quoted[5]


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------
def _interp_ois(ois: dict, t: float) -> float:
    ks = sorted(ois)
    if t <= ks[0]:
        return ois[ks[0]]
    if t >= ks[-1]:
        return ois[ks[-1]]
    return float(np.interp(t, ks, [ois[k] for k in ks]))


def forward_zero(ois: dict, start_yr: float, tenor_yr: float) -> float:
    """Forward zero rate from start_yr for tenor_yr, continuously compounded, %."""
    if start_yr <= 0:
        return _interp_ois(ois, tenor_yr)
    a, b = start_yr, start_yr + tenor_yr
    return (b * _interp_ois(ois, b) - a * _interp_ois(ois, a)) / (b - a)


def forward_mortgage_rate(m: Market, start_yr: float, tenor_yr: float, relock: bool = True) -> float:
    """Market-implied mortgage rate for a fix of tenor_yr starting in start_yr."""
    r = forward_zero(m.ois, start_yr, tenor_yr) + m.spread_bp / 100.0
    if relock and m.apply_relock and start_yr > 0:
        r -= max(m.relock_bp - m.relock_friction_bp, 0.0) / 100.0
    return r


# ---------------------------------------------------------------------------
# Cashflow engine
# ---------------------------------------------------------------------------
def annuity_payment(balance: float, annual_rate: float, n_months: int) -> float:
    r = annual_rate / 1200.0
    if n_months <= 0:
        return balance
    if abs(r) < 1e-12:
        return balance / n_months
    return balance * r / (1.0 - (1.0 + r) ** (-n_months))


@dataclass
class RouteResult:
    tco: float
    pv_payments: float
    pv_upfront: float
    pv_terminal: float
    terminal_balance: float
    total_paid: float               # undiscounted cash out (payments + upfront)
    payments: list                  # monthly payment per leg
    legs: list


def simulate(loan: Loan, legs: Sequence[Leg], horizon_months: int, discount_rate: float,
             fill_rate: Optional[float] = None) -> RouteResult:
    """Run legs in sequence, truncate at horizon, fill any gap at fill_rate (the SVR)."""
    d = discount_rate / 1200.0
    B = float(loan.balance)
    term_left = int(loan.term_months)
    t = 0
    pv_pay = pv_up = total = 0.0
    pay_per_leg, legs_run = [], list(legs)

    covered = sum(l.months for l in legs)
    if covered < horizon_months:
        if fill_rate is None:
            raise ValueError("legs do not cover horizon; supply fill_rate (SVR)")
        legs_run.append(Leg(fill_rate, horizon_months - covered, label="reversion (SVR)"))

    for leg in legs_run:
        if t >= horizon_months:
            break
        # costs at leg start
        if leg.fee_added_to_loan:
            B += leg.fee
        else:
            pv_up += leg.fee / (1 + d) ** t
            total += leg.fee
        pv_up += leg.upfront_costs / (1 + d) ** t
        total += leg.upfront_costs

        pmt = annuity_payment(B, leg.rate, term_left)
        pay_per_leg.append(pmt)
        r = leg.rate / 1200.0
        n = min(leg.months, horizon_months - t, term_left)
        for _ in range(n):
            t += 1
            interest = B * r
            principal = min(pmt - interest, B)
            B -= principal
            pv_pay += pmt / (1 + d) ** t
            total += pmt
        term_left -= n
        if term_left <= 0:
            break

    pv_term = B / (1 + d) ** t if t > 0 else B
    return RouteResult(tco=pv_pay + pv_up + pv_term, pv_payments=pv_pay, pv_upfront=pv_up,
                       pv_terminal=pv_term, terminal_balance=B, total_paid=total,
                       payments=pay_per_leg, legs=legs_run)


# ---------------------------------------------------------------------------
# Route builders
# ---------------------------------------------------------------------------
def erc_now(balance: float, erc_schedule: Sequence[float], months_into_fix: int) -> float:
    """ERC payable today, GBP. erc_schedule is % of balance per fix year, e.g. [5,4,3,2,1]."""
    if not erc_schedule:
        return 0.0
    yr = min(months_into_fix // 12, len(erc_schedule) - 1)
    return balance * erc_schedule[yr] / 100.0


def chain(m: Market, first: Leg, tenors_yr: Sequence[int], fee: float, fee_added: bool = True,
          upfront_costs: float = 0.0) -> list:
    """first leg as held, then forward-implied fixes of the given tenors."""
    legs = [first]
    start = first.months / 12.0
    for ty in tenors_yr:
        r = forward_mortgage_rate(m, start, ty)
        legs.append(Leg(r, ty * 12, fee, fee_added, upfront_costs,
                        label=f"{ty}y fix from yr {start:.1f} (fwd-implied)", forward_implied=True))
        start += ty
    return legs


def _shift_forward_legs(legs: Sequence[Leg], delta: float) -> list:
    return [replace(l, rate=l.rate + delta) if l.forward_implied else l for l in legs]


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
@dataclass
class Comparison:
    label_a: str
    label_b: str
    tco_a: float
    tco_b: float
    advantage_b: float              # GBP, positive = B cheaper
    per_month_equiv: float          # advantage annuitised over horizon at discount rate
    breakeven_shift_bp: Optional[float]   # shift to B's forward-implied rates that equalises
    result_a: RouteResult
    result_b: RouteResult
    horizon_months: int
    breakeven_on: Optional[str] = None   # which route's forward-implied legs were shifted


def compare(loan: Loan, legs_a: Sequence[Leg], legs_b: Sequence[Leg], horizon_months: int,
            m: Market, label_a="A", label_b="B", solve_breakeven=True) -> Comparison:
    ra = simulate(loan, legs_a, horizon_months, m.disc, m.svr)
    rb = simulate(loan, legs_b, horizon_months, m.disc, m.svr)
    adv = ra.tco - rb.tco
    ann = adv / _annuity_factor(m.disc, horizon_months)

    be, be_on = None, None
    if solve_breakeven:
        if any(l.forward_implied for l in legs_b):
            f = lambda dlt: ra.tco - simulate(loan, _shift_forward_legs(legs_b, dlt), horizon_months, m.disc, m.svr).tco
            be_on = "B"
        elif any(l.forward_implied for l in legs_a):
            f = lambda dlt: simulate(loan, _shift_forward_legs(legs_a, dlt), horizon_months, m.disc, m.svr).tco - rb.tco
            be_on = "A"
        if be_on:
            try:
                be = brentq(f, -10.0, 10.0, xtol=1e-6) * 100.0
            except ValueError:
                be, be_on = None, None
    return Comparison(label_a, label_b, ra.tco, rb.tco, adv, ann, be, ra, rb, horizon_months, be_on)


def _annuity_factor(annual_rate: float, n: int) -> float:
    r = annual_rate / 1200.0
    return n if abs(r) < 1e-12 else (1 - (1 + r) ** (-n)) / r


def breakeven_switch_rate(loan: Loan, stay: Sequence[Leg], offer: Leg, erc_today: float,
                          horizon_months: int, m: Market) -> float:
    """Offer rate at which switching now (paying ERC) is exactly neutral vs staying."""
    ra = simulate(loan, stay, horizon_months, m.disc, m.svr)

    def f(rate):
        o = replace(offer, rate=rate, upfront_costs=offer.upfront_costs + erc_today)
        return ra.tco - simulate(loan, [o], horizon_months, m.disc, m.svr).tco
    return brentq(f, 0.01, 20.0, xtol=1e-6)


def payment_shock(loan: Loan, current_rate: float, months_left_on_fix: int, m: Market,
                  new_fix_years: int = 2) -> dict:
    """Deterministic: today's payment vs the payment at reversion under (a) forward-implied
    rate for a new fix, (b) SVR. Balance is rolled forward to the reversion date."""
    now = annuity_payment(loan.balance, current_rate, loan.term_months)
    # roll balance to reversion
    B, r = loan.balance, current_rate / 1200.0
    for _ in range(months_left_on_fix):
        B -= (now - B * r)
    term_then = loan.term_months - months_left_on_fix
    fwd = forward_mortgage_rate(m, months_left_on_fix / 12.0, new_fix_years, relock=False)
    fwd_relock = forward_mortgage_rate(m, months_left_on_fix / 12.0, new_fix_years, relock=True)
    return dict(
        payment_now=now,
        balance_at_reversion=B,
        fwd_rate=fwd,
        fwd_rate_with_relock=fwd_relock,
        payment_fwd=annuity_payment(B, fwd, term_then),
        payment_fwd_relock=annuity_payment(B, fwd_relock, term_then),
        payment_svr=annuity_payment(B, m.svr, term_then),
        payment_at_quoted_today=annuity_payment(B, m.quoted[new_fix_years], term_then),
    )


def relock_option_value_gbp(loan: Loan, fix_years: int, m: Market, months_until_start: int = 0) -> dict:
    """£ value of the free relock window on a fix of fix_years, using the empirical bp saving
    applied over the fix, on the balance at the fix start. Undiscounted and discounted."""
    B, _ = loan.balance, None
    net_bp = max(m.relock_bp - m.relock_friction_bp, 0.0)
    # approximate: bp x average balance over the fix x years; use annuity for the discounted figure
    r_hi = m.quoted[fix_years]
    r_lo = r_hi - net_bp / 100.0
    n = fix_years * 12
    term = loan.term_months - months_until_start
    p_hi = annuity_payment(B, r_hi, term)
    p_lo = annuity_payment(B, r_lo, term)
    saving_monthly = p_hi - p_lo
    # terminal balance differs too (lower rate amortises faster); include it
    def roll(rate, pmt):
        b, rr = B, rate / 1200.0
        for _ in range(n):
            b -= (pmt - b * rr)
        return b
    term_diff = roll(r_hi, p_hi) - roll(r_lo, p_lo)  # positive: lower rate leaves smaller balance
    d = m.disc / 1200.0
    pv = sum(saving_monthly / (1 + d) ** t for t in range(1, n + 1)) + term_diff / (1 + d) ** n
    return dict(net_bp=net_bp, saving_per_month=saving_monthly, undiscounted=saving_monthly * n + term_diff, pv=pv)
