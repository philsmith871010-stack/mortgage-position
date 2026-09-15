import math
import numpy as np
import pytest

from engine import (Loan, Leg, Market, annuity_payment, simulate, compare, forward_zero,
                    forward_mortgage_rate, chain, erc_now, breakeven_switch_rate, payment_shock,
                    relock_option_value_gbp)

FLAT = {1: 4.0, 2: 4.0, 3: 4.0, 4: 4.0, 5: 4.0, 7: 4.0, 10: 4.0}
MKT = Market(ois=FLAT, quoted={2: 4.6, 3: 4.6, 5: 4.6}, spread_bp=60, svr=6.5, discount_rate=4.6,
             apply_relock=False)


# --- amortisation -----------------------------------------------------------
def test_annuity_matches_textbook():
    # £100k, 5% , 25y -> £584.59/month (standard reference value)
    assert annuity_payment(100_000, 5.0, 300) == pytest.approx(584.59, abs=0.01)


def test_zero_rate_is_straight_line():
    assert annuity_payment(120_000, 0.0, 120) == pytest.approx(1000.0)


def test_full_term_amortises_to_zero():
    loan = Loan(100_000, 300)
    r = simulate(loan, [Leg(5.0, 300)], 300, 4.0)
    assert abs(r.terminal_balance) < 1e-6


def test_interest_identity():
    # total paid = principal + interest; with terminal balance the identity is
    # sum(payments) = (B0 - B_T) + sum(interest)
    loan = Loan(200_000, 240)
    r = simulate(loan, [Leg(4.0, 24)], 24, 4.0)
    pmt = r.payments[0]
    B, rr, interest = 200_000.0, 4.0 / 1200, 0.0
    for _ in range(24):
        i = B * rr; interest += i; B -= pmt - i
    assert r.total_paid == pytest.approx(24 * pmt)
    assert 24 * pmt == pytest.approx((200_000 - r.terminal_balance) + interest, rel=1e-9)


# --- TCO comparison properties ---------------------------------------------
def test_identical_routes_zero_advantage():
    loan = Loan(250_000, 300)
    legs = [Leg(4.5, 60, 999)]
    c = compare(loan, legs, legs, 60, MKT)
    assert c.advantage_b == pytest.approx(0.0, abs=1e-6)


def test_lower_rate_is_cheaper():
    loan = Loan(250_000, 300)
    c = compare(loan, [Leg(5.0, 60)], [Leg(4.5, 60)], 60, MKT)
    assert c.advantage_b > 0


def test_fee_added_to_loan_raises_terminal_balance():
    loan = Loan(250_000, 300)
    a = simulate(loan, [Leg(4.5, 24, 999, fee_added_to_loan=True)], 24, 4.6)
    b = simulate(loan, [Leg(4.5, 24, 999, fee_added_to_loan=False)], 24, 4.6)
    assert a.terminal_balance > b.terminal_balance
    assert a.pv_upfront == 0 and b.pv_upfront > 0
    # at a discount rate equal to the loan rate, they should be nearly equivalent in PV terms;
    # loan rate 4.5 < disc 4.6, so adding is very slightly cheaper in PV
    assert abs(a.tco - b.tco) < 30


def test_discount_rate_scales_terminal_and_payments():
    loan = Loan(250_000, 300)
    hi = simulate(loan, [Leg(4.5, 24)], 24, 8.0)
    lo = simulate(loan, [Leg(4.5, 24)], 24, 2.0)
    assert hi.tco < lo.tco


def test_horizon_gap_filled_by_svr():
    loan = Loan(250_000, 300)
    r = simulate(loan, [Leg(4.5, 24)], 36, 4.6, fill_rate=6.5)
    assert len(r.legs) == 2 and r.legs[1].rate == 6.5 and r.legs[1].months == 12
    with pytest.raises(ValueError):
        simulate(loan, [Leg(4.5, 24)], 36, 4.6)


def test_route_truncated_at_horizon():
    loan = Loan(250_000, 300)
    r = simulate(loan, [Leg(4.5, 60)], 24, 4.6)
    B, rr, pmt = 250_000.0, 4.5 / 1200, r.payments[0]
    for _ in range(24):
        B -= pmt - B * rr
    assert r.terminal_balance == pytest.approx(B, rel=1e-9)


# --- forwards ---------------------------------------------------------------
def test_flat_curve_forward_equals_spot():
    assert forward_zero(FLAT, 2, 2) == pytest.approx(4.0)
    assert forward_zero(FLAT, 0, 5) == pytest.approx(4.0)


def test_forward_from_two_spots():
    ois = {2: 4.0, 4: 4.5}
    # (4*4.5 - 2*4.0)/2 = 5.0
    assert forward_zero(ois, 2, 2) == pytest.approx(5.0)


def test_forward_mortgage_rate_adds_spread_and_relock():
    m = Market(ois=FLAT, quoted={2: 4.6, 5: 4.6}, spread_bp=60, svr=6.5, relock_bp=25, relock_friction_bp=5)
    assert forward_mortgage_rate(m, 0, 2) == pytest.approx(4.6)          # no relock at t=0
    assert forward_mortgage_rate(m, 2, 2) == pytest.approx(4.6 - 0.20)   # relock net 20bp
    assert forward_mortgage_rate(m, 2, 2, relock=False) == pytest.approx(4.6)


# --- chain, ERC, break-evens -------------------------------------------------
def test_chain_builds_forward_legs():
    legs = chain(MKT, Leg(4.6, 24, 999), [2, 1], 999)
    assert len(legs) == 3 and legs[1].forward_implied and legs[2].months == 12


def test_chain_vs_single_fix_equal_on_flat_curve_without_fees():
    # flat curve, same spread, no relock, no fees: 2+2+1 chain must equal a 5y fix at same rate
    loan = Loan(250_000, 300)
    m = MKT
    five = [Leg(4.6, 60)]
    two_chain = chain(m, Leg(4.6, 24), [2, 1], 0.0)
    c = compare(loan, five, two_chain, 60, m)
    assert c.advantage_b == pytest.approx(0.0, abs=1e-6)


def test_fees_make_chain_worse_on_flat_curve():
    loan = Loan(250_000, 300)
    five = [Leg(4.6, 60, 999)]
    two_chain = chain(MKT, Leg(4.6, 24, 999), [2, 1], 999)
    c = compare(loan, five, two_chain, 60, MKT)
    assert c.advantage_b < 0
    # two extra fees, roughly
    assert -c.advantage_b == pytest.approx(2 * 999, rel=0.15)


def test_breakeven_shift_equalises():
    loan = Loan(250_000, 300)
    five = [Leg(4.6, 60, 999)]
    two_chain = chain(MKT, Leg(4.6, 24, 999), [2, 1], 999)
    c = compare(loan, five, two_chain, 60, MKT)
    assert c.breakeven_shift_bp is not None
    from engine import _shift_forward_legs
    shifted = _shift_forward_legs(two_chain, c.breakeven_shift_bp / 100)
    assert compare(loan, five, shifted, 60, MKT, solve_breakeven=False).advantage_b == pytest.approx(0, abs=0.05)


def test_erc_schedule_lookup():
    assert erc_now(200_000, [5, 4, 3, 2, 1], 0) == 10_000
    assert erc_now(200_000, [5, 4, 3, 2, 1], 13) == 8_000
    assert erc_now(200_000, [5, 4, 3, 2, 1], 70) == 2_000
    assert erc_now(200_000, [], 5) == 0


def test_breakeven_switch_rate_is_neutral():
    loan = Loan(200_000, 240)
    stay = [Leg(5.5, 26)]
    offer = Leg(4.6, 60, 999)
    erc = erc_now(200_000, [3, 2], 10)
    be = breakeven_switch_rate(loan, stay, offer, erc, 26, MKT)
    from dataclasses import replace
    o = replace(offer, rate=be, upfront_costs=erc)
    a = simulate(loan, stay, 26, MKT.disc, MKT.svr).tco
    b = simulate(loan, [o], 26, MKT.disc, MKT.svr).tco
    assert a == pytest.approx(b, abs=0.05)
    assert be < 5.5  # you'd need a lower rate than you're on to justify paying ERC


# --- payment shock & relock --------------------------------------------------
def test_payment_shock_orders():
    loan = Loan(250_000, 276)
    ps = payment_shock(loan, 1.99, 4, MKT, 2)
    assert ps["payment_now"] < ps["payment_fwd"] < ps["payment_svr"]
    assert ps["balance_at_reversion"] < loan.balance


def test_relock_value_scales_with_tenor_and_balance():
    m = Market(ois=FLAT, quoted={2: 4.6, 5: 4.6}, spread_bp=60, svr=6.5, discount_rate=4.6)
    a = relock_option_value_gbp(Loan(250_000, 300), 2, m)
    b = relock_option_value_gbp(Loan(250_000, 300), 5, m)
    c = relock_option_value_gbp(Loan(125_000, 300), 5, m)
    assert b["pv"] > a["pv"] > 0
    assert b["pv"] == pytest.approx(2 * c["pv"], rel=1e-6)
    assert a["net_bp"] == 20
