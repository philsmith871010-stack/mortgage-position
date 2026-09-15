// Mortgage decision engine — JS port of engine.py. Rates in %, money GBP, time in months.
const Engine = (() => {
  const interp = (ois, t) => {
    const ks = Object.keys(ois).map(Number).sort((a, b) => a - b);
    if (t <= ks[0]) return ois[ks[0]];
    if (t >= ks[ks.length - 1]) return ois[ks[ks.length - 1]];
    let i = 0; while (ks[i + 1] < t) i++;
    const a = ks[i], b = ks[i + 1];
    return ois[a] + (ois[b] - ois[a]) * (t - a) / (b - a);
  };
  const forwardZero = (ois, start, tenor) => {
    if (start <= 0) return interp(ois, tenor);
    const a = start, b = start + tenor;
    return (b * interp(ois, b) - a * interp(ois, a)) / (b - a);
  };
  const forwardMortgageRate = (m, start, tenor, relock = true) => {
    let r = forwardZero(m.ois, start, tenor) + m.spreadBp / 100;
    if (relock && m.applyRelock && start > 0) r -= Math.max(m.relockBp - m.relockFrictionBp, 0) / 100;
    return r;
  };
  const annuity = (B, rate, n) => {
    const r = rate / 1200;
    if (n <= 0) return B;
    if (Math.abs(r) < 1e-12) return B / n;
    return B * r / (1 - Math.pow(1 + r, -n));
  };
  const annuityFactor = (rate, n) => { const r = rate / 1200; return Math.abs(r) < 1e-12 ? n : (1 - Math.pow(1 + r, -n)) / r; };
  const disc = m => m.discountRate ?? m.quoted[5];

  // leg: {rate, months, fee=0, feeAdded=true, upfront=0, label, fwd=false}
  const simulate = (loan, legs, H, d, fill) => {
    const dm = d / 1200;
    let B = loan.balance, termLeft = loan.termMonths, t = 0, pvPay = 0, pvUp = 0, total = 0;
    const pays = [], run = legs.slice(), sched = [];
    const covered = legs.reduce((s, l) => s + l.months, 0);
    if (covered < H) {
      if (fill == null) throw new Error("legs do not cover horizon");
      run.push({ rate: fill, months: H - covered, fee: 0, feeAdded: true, upfront: 0, label: "Reversion rate", fwd: false });
    }
    for (const leg of run) {
      if (t >= H) break;
      const fee = leg.fee || 0, up = leg.upfront || 0;
      if (leg.feeAdded !== false) B += fee; else { pvUp += fee / Math.pow(1 + dm, t); total += fee; }
      pvUp += up / Math.pow(1 + dm, t); total += up;
      const pmt = annuity(B, leg.rate, termLeft); pays.push(pmt);
      const r = leg.rate / 1200, n = Math.min(leg.months, H - t, termLeft);
      for (let k = 0; k < n; k++) {
        t++; const i = B * r; const p = Math.min(pmt - i, B); B -= p;
        pvPay += pmt / Math.pow(1 + dm, t); total += pmt; sched.push({ t, pmt, B, rate: leg.rate });
      }
      termLeft -= n; if (termLeft <= 0) break;
    }
    const pvTerm = t > 0 ? B / Math.pow(1 + dm, t) : B;
    return { tco: pvPay + pvUp + pvTerm, pvPayments: pvPay, pvUpfront: pvUp, pvTerminal: pvTerm, terminal: B, total, pays, legs: run, sched };
  };
  const shiftFwd = (legs, dl) => legs.map(l => l.fwd ? { ...l, rate: l.rate + dl } : l);
  const bisect = (f, lo, hi, tol = 1e-7) => {
    let flo = f(lo), fhi = f(hi);
    if (flo * fhi > 0) return null;
    for (let i = 0; i < 100; i++) { const mid = (lo + hi) / 2, fm = f(mid); if (Math.abs(fm) < tol || (hi - lo) < tol) return mid; if (flo * fm < 0) { hi = mid; fhi = fm; } else { lo = mid; flo = fm; } }
    return (lo + hi) / 2;
  };
  const compare = (loan, A, B, H, m) => {
    const d = disc(m), ra = simulate(loan, A, H, d, m.svr), rb = simulate(loan, B, H, d, m.svr);
    const adv = ra.tco - rb.tco;
    let be = null, beOn = null;
    if (B.some(l => l.fwd)) { beOn = "B"; be = bisect(dl => ra.tco - simulate(loan, shiftFwd(B, dl), H, d, m.svr).tco, -10, 10); }
    else if (A.some(l => l.fwd)) { beOn = "A"; be = bisect(dl => simulate(loan, shiftFwd(A, dl), H, d, m.svr).tco - rb.tco, -10, 10); }
    return { ra, rb, advantageB: adv, perMonth: adv / annuityFactor(d, H), breakevenBp: be == null ? null : be * 100, beOn, H };
  };
  const chain = (m, first, tenors, fee, feeAdded = true, upfront = 0) => {
    const legs = [first]; let start = first.months / 12;
    for (const ty of tenors) {
      legs.push({ rate: forwardMortgageRate(m, start, ty), months: ty * 12, fee, feeAdded, upfront, label: `${ty}y fix from year ${start.toFixed(1)}`, fwd: true });
      start += ty;
    }
    return legs;
  };
  const ercNow = (B, sched, monthsIn) => { if (!sched || !sched.length) return 0; const y = Math.min(Math.floor(monthsIn / 12), sched.length - 1); return B * sched[y] / 100; };
  const breakevenSwitchRate = (loan, stay, offer, erc, H, m) => {
    const d = disc(m), ra = simulate(loan, stay, H, d, m.svr);
    return bisect(rate => ra.tco - simulate(loan, [{ ...offer, rate, upfront: (offer.upfront || 0) + erc }], H, d, m.svr).tco, 0.01, 20);
  };
  const paymentShock = (loan, curRate, monthsLeft, m, newFixYears = 2) => {
    const now = annuity(loan.balance, curRate, loan.termMonths);
    let B = loan.balance; const r = curRate / 1200;
    for (let k = 0; k < monthsLeft; k++) B -= (now - B * r);
    const term = loan.termMonths - monthsLeft;
    const fwd = forwardMortgageRate(m, monthsLeft / 12, newFixYears, false), fwdR = forwardMortgageRate(m, monthsLeft / 12, newFixYears, true);
    return { now, balance: B, fwd, fwdRelock: fwdR, payFwd: annuity(B, fwd, term), payFwdRelock: annuity(B, fwdR, term), paySvr: annuity(B, m.svr, term), payQuoted: annuity(B, m.quoted[newFixYears], term) };
  };
  const relockValue = (loan, fixYears, m, monthsUntil = 0) => {
    const net = Math.max(m.relockBp - m.relockFrictionBp, 0), rHi = m.quoted[fixYears], rLo = rHi - net / 100, n = fixYears * 12, term = loan.termMonths - monthsUntil;
    const pHi = annuity(loan.balance, rHi, term), pLo = annuity(loan.balance, rLo, term), sav = pHi - pLo;
    const roll = (rate, pmt) => { let b = loan.balance, rr = rate / 1200; for (let k = 0; k < n; k++) b -= (pmt - b * rr); return b; };
    const termDiff = roll(rHi, pHi) - roll(rLo, pLo), dm = disc(m) / 1200;
    let pv = 0; for (let t = 1; t <= n; t++) pv += sav / Math.pow(1 + dm, t); pv += termDiff / Math.pow(1 + dm, n);
    return { netBp: net, perMonth: sav, undiscounted: sav * n + termDiff, pv };
  };
  return { forwardZero, forwardMortgageRate, annuity, simulate, compare, chain, ercNow, breakevenSwitchRate, paymentShock, relockValue, disc };
})();
if (typeof module !== "undefined") module.exports = Engine;
