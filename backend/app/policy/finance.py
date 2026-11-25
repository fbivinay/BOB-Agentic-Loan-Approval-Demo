"""Loan arithmetic: EMI, repayment schedule, upfront fees and the KFS annual percentage rate. Deterministic Decimal maths, no floats.

Conventions (all stated on the Key Facts Statement):
  * EMI is rounded to the whole rupee, as banks do; the final instalment absorbs the rounding so the loan closes at exactly zero.
  * Monthly interest = opening balance x annual rate / 12, rounded to paise, on a reducing balance. No broken-period interest
    (the first instalment falls one month after the start date). Both are simplifications of real practice.
  * Upfront fees (processing fee plus GST on it) are deducted from the amount disbursed.
  * APR is the internal rate of return of the real cash flows (net amount received against the instalments paid), found by bisection.
    It is shown nominal-annualised (monthly IRR x 12); the effective annual rate is shown alongside it.
All rates, fees and the rounding convention are DEMO_ASSUMPTION values, never Bank of Baroda's.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 60
D = Decimal
PAISE = D("0.01")
RUPEE = D("1")


def money(x) -> Decimal:
    return D(str(x)).quantize(PAISE, ROUND_HALF_UP)


def monthly_rate(rate_bp: int) -> Decimal:
    return D(rate_bp) / D(120000)  # basis points per year -> fraction per month


def _check(principal, rate_bp, months) -> Decimal:
    p = D(str(principal))
    if p <= 0:
        raise ValueError("principal must be positive")
    if int(rate_bp) != rate_bp or not 0 <= rate_bp <= 10000:
        raise ValueError("rate must be whole basis points between 0 and 10000")
    if int(months) != months or not 1 <= months <= 360:
        raise ValueError("tenure must be 1 to 360 whole months")
    return p


def annuity_factor(rate_bp: int, months: int) -> Decimal:
    """Present value of 1 rupee a month for `months` months."""
    r = monthly_rate(rate_bp)
    if r == 0:
        return D(months)
    return (1 - (1 + r) ** -months) / r


def emi(principal, rate_bp: int, months: int) -> Decimal:
    p = _check(principal, rate_bp, months)
    r = monthly_rate(rate_bp)
    raw = p / months if r == 0 else p * r * (1 + r) ** months / ((1 + r) ** months - 1)
    return raw.quantize(RUPEE, ROUND_HALF_UP)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


@dataclass(frozen=True)
class Instalment:
    number: int
    due_date: date
    opening: Decimal
    payment: Decimal
    interest: Decimal
    principal: Decimal
    closing: Decimal


def schedule(principal, rate_bp: int, months: int, first_due: date) -> list[Instalment]:
    """Reducing-balance schedule. `first_due` is the date of instalment 1; later ones follow monthly (day clamped to the month end)."""
    p = money(_check(principal, rate_bp, months))
    e = emi(p, rate_bp, months)
    r = monthly_rate(rate_bp)
    rows: list[Instalment] = []
    opening = p
    for k in range(1, months + 1):
        interest = money(opening * r)
        final = k == months or e >= opening + interest
        pay = opening + interest if final else e
        principal_part = pay - interest
        rows.append(Instalment(k, add_months(first_due, k - 1), opening, pay, interest, principal_part, opening - principal_part))
        opening -= principal_part
        if final:
            break
    return rows


def processing_fee(amount, percent_bp: int, minimum, maximum) -> Decimal:
    """Percentage of the sanctioned amount (basis points), clamped between a minimum and a maximum."""
    raw = money(D(str(amount)) * D(percent_bp) / D(10000))
    return min(max(raw, money(minimum)), money(maximum))


def gst_on(fee: Decimal, gst_percent: int) -> Decimal:
    return money(fee * D(gst_percent) / D(100))


def irr_monthly(net_received, payments: list[Decimal]) -> Decimal:
    """Monthly internal rate of return: the rate at which the instalments' present value equals the net amount received."""
    net = D(str(net_received))
    if net <= 0:
        raise ValueError("net amount received must be positive")
    if sum(payments) <= net:
        return D(0)

    def pv(rate: Decimal) -> Decimal:
        f = 1 + rate
        total, disc = D(0), D(1)
        for p in payments:
            disc /= f
            total += p * disc
        return total

    lo, hi = D(0), D(1)  # pv is decreasing in the rate; a monthly rate of 100% is far above any real loan
    for _ in range(120):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) > net else (lo, mid)
    return (lo + hi) / 2


def kfs_figures(principal, rate_bp: int, months: int, first_due: date, *, fee_percent_bp: int, fee_min, fee_max, gst_percent: int) -> dict:
    """The numbers a Key Facts Statement needs, computed from the same schedule the borrower would follow."""
    p = money(principal)
    sched = schedule(p, rate_bp, months, first_due)
    payments = [row.payment for row in sched]
    fee = processing_fee(p, fee_percent_bp, fee_min, fee_max)
    gst = gst_on(fee, gst_percent)
    net = p - fee - gst
    m = irr_monthly(net, payments)
    total_paid = sum(payments, D(0))
    return {
        "loan_amount": p, "tenure_months": len(sched), "interest_rate_bp": rate_bp, "emi": emi(p, rate_bp, months),
        "final_instalment": sched[-1].payment, "total_interest": total_paid - p, "total_payable_instalments": total_paid,
        "processing_fee": fee, "gst_on_fee": gst, "upfront_charges": fee + gst, "net_disbursed": net,
        "total_cost_of_credit": total_paid - net,
        "apr_percent": (m * 12 * 100).quantize(PAISE, ROUND_HALF_UP),
        "effective_annual_rate_percent": (((1 + m) ** 12 - 1) * 100).quantize(PAISE, ROUND_HALF_UP),
        "apr_convention": "IRR of net amount received against the instalments paid, monthly rate x 12 (nominal annualised)",
        "first_instalment_date": sched[0].due_date, "last_instalment_date": sched[-1].due_date,
        "disclaimer": "Illustrative figures computed from demo parameters. Not a loan offer and not Bank of Baroda's actual terms.",
    }
