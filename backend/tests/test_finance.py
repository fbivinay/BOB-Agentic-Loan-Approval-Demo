"""EMI, schedule, fees and APR, cross-checked against an independent float implementation."""

import itertools
from datetime import date
from decimal import Decimal as D

import pytest

from app.policy import finance as f

START = date(2026, 12, 5)
FEES = dict(fee_percent_bp=100, fee_min=1000, fee_max=10000, gst_percent=18)


def emi_float(p, rate_bp, n):
    r = rate_bp / 120000
    return p / n if r == 0 else p * r * (1 + r) ** n / ((1 + r) ** n - 1)


def irr_float(net, payments):
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        pv = sum(p / (1 + mid) ** (k + 1) for k, p in enumerate(payments))
        lo, hi = (mid, hi) if pv > net else (lo, mid)
    return (lo + hi) / 2


def test_known_emi_values():
    assert f.emi(100000, 1200, 12) == D("8885")        # the textbook example: 8,884.88 rounded to the rupee
    assert f.emi(500000, 1150, 36) == D("16488")
    assert f.emi(1000000, 1050, 60) == D("21494")


@pytest.mark.parametrize("p,bp,n", list(itertools.product([50000, 123456, 500000, 2000000], [1050, 1250, 1900], [12, 24, 36, 48, 60])))
def test_emi_agrees_with_an_independent_calculation(p, bp, n):
    assert abs(float(f.emi(p, bp, n)) - emi_float(p, bp, n)) <= 0.5


@pytest.mark.parametrize("p,bp,n", list(itertools.product([50000, 99999, 750000, 2000000], [0, 1050, 1900], [1, 12, 37, 60])))
def test_schedule_closes_exactly_and_adds_up(p, bp, n):
    s = f.schedule(p, bp, n, START)
    assert s[-1].closing == 0 and len(s) == n
    assert sum(r.principal for r in s) == D(p)
    assert all(r.opening > 0 and r.interest >= 0 and r.principal > 0 and r.closing >= 0 for r in s)
    assert all(r.payment == r.interest + r.principal and r.closing == r.opening - r.principal for r in s)
    assert all(a.closing == b.opening for a, b in zip(s, s[1:]))
    # the last instalment absorbs rounding only: it stays within a few rupees of the regular EMI
    assert abs(s[-1].payment - s[0].payment) < n + 2
    assert [r.number for r in s] == list(range(1, n + 1))


def test_schedule_dates_are_monthly_and_clamped_to_month_end():
    s = f.schedule(100000, 1200, 6, date(2026, 1, 31))
    assert [r.due_date for r in s] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30)]
    assert f.add_months(date(2027, 12, 31), 2) == date(2028, 2, 29)  # leap year


def test_interest_falls_and_principal_rises_over_time():
    s = f.schedule(500000, 1250, 36, START)
    assert all(a.interest > b.interest for a, b in zip(s, s[1:]))
    assert all(a.principal < b.principal for a, b in zip(s[:-1], s[1:-1]))


def test_first_month_interest_is_exact():
    assert f.schedule(100000, 1200, 12, START)[0].interest == D("1000.00")  # 1% a month on 100,000


@pytest.mark.parametrize("bad", [dict(principal=0), dict(principal=-5), dict(rate_bp=-1), dict(rate_bp=10001), dict(months=0), dict(months=361), dict(months=12.5)])
def test_bad_inputs_are_rejected(bad):
    args = dict(principal=100000, rate_bp=1200, months=12) | bad
    with pytest.raises(ValueError):
        f.emi(args["principal"], args["rate_bp"], args["months"])


def test_processing_fee_follows_percentage_minimum_and_maximum():
    assert f.processing_fee(300000, 100, 1000, 10000) == D("3000.00")
    assert f.processing_fee(50000, 100, 1000, 10000) == D("1000.00")      # below the minimum
    assert f.processing_fee(2000000, 100, 1000, 10000) == D("10000.00")   # capped
    assert f.gst_on(D("3000.00"), 18) == D("540.00")


@pytest.mark.parametrize("p,bp,n", [(500000, 1150, 36), (100000, 1200, 12), (2000000, 1900, 60), (50000, 1050, 12)])
def test_apr_without_fees_equals_the_nominal_rate(p, bp, n):
    s = f.schedule(p, bp, n, START)
    m = f.irr_monthly(D(p), [r.payment for r in s])
    assert abs(float(m * 12 * 100) - bp / 100) < 0.02  # rupee-rounded EMI moves it by a hair at most


@pytest.mark.parametrize("p,bp,n", [(500000, 1150, 36), (100000, 1200, 12), (2000000, 1900, 60), (50000, 1050, 12), (250000, 1300, 24)])
def test_apr_matches_an_independent_irr_and_is_above_the_rate_when_fees_exist(p, bp, n):
    k = f.kfs_figures(p, bp, n, START, **FEES)
    s = f.schedule(p, bp, n, START)
    m = irr_float(float(k["net_disbursed"]), [float(r.payment) for r in s])
    assert abs(float(k["apr_percent"]) - m * 1200) < 0.01
    assert float(k["apr_percent"]) > bp / 100
    assert float(k["effective_annual_rate_percent"]) > float(k["apr_percent"])  # compounding monthly makes the effective rate higher
    # present value of the instalments at the solved rate equals the net amount received
    r = D(str(m))
    pv = sum(float(x.payment) / (1 + m) ** x.number for x in s)
    assert abs(pv - float(k["net_disbursed"])) < 0.01


def test_a_known_apr_example():
    k = f.kfs_figures(100000, 1200, 12, START, **FEES)
    assert k["emi"] == D("8885") and k["processing_fee"] == D("1000.00") and k["gst_on_fee"] == D("180.00")
    assert k["net_disbursed"] == D("98820.00") and k["upfront_charges"] == D("1180.00")
    assert k["total_cost_of_credit"] == k["total_payable_instalments"] - k["net_disbursed"]
    assert D("14.0") < k["apr_percent"] < D("14.8")  # 12% rate plus the fee: about 14.4%


def test_apr_rises_with_the_fee_and_falls_with_a_longer_tenure():
    low = f.kfs_figures(300000, 1200, 24, START, fee_percent_bp=50, fee_min=0, fee_max=10**6, gst_percent=18)["apr_percent"]
    high = f.kfs_figures(300000, 1200, 24, START, fee_percent_bp=200, fee_min=0, fee_max=10**6, gst_percent=18)["apr_percent"]
    assert high > low
    short = f.kfs_figures(300000, 1200, 12, START, **FEES)["apr_percent"]
    long = f.kfs_figures(300000, 1200, 60, START, **FEES)["apr_percent"]
    assert short > long  # a fixed upfront fee weighs more on a short loan


def test_kfs_figures_are_consistent_and_labelled():
    k = f.kfs_figures(450000, 1250, 36, START, **FEES)
    assert k["loan_amount"] - k["upfront_charges"] == k["net_disbursed"]
    assert k["total_interest"] == k["total_payable_instalments"] - k["loan_amount"]
    assert k["first_instalment_date"] == START and k["last_instalment_date"] == date(2029, 11, 5)
    assert "Not a loan offer" in k["disclaimer"] or "not a loan offer" in k["disclaimer"].lower()
    assert "IRR" in k["apr_convention"]


def test_results_are_deterministic():
    a = f.kfs_figures(450000, 1250, 36, START, **FEES)
    b = f.kfs_figures(450000, 1250, 36, START, **FEES)
    assert a == b
