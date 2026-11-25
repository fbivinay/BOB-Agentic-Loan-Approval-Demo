"""Run the engine over all 1,000 synthetic customers and check that the results look like a real lending book.

Offline: reads the local canonical dataset (data/out). Skipped when it has not been generated. This is a test of the demo, so for new-to-bank
customers it uses the generated external statements as the stand-in for what the document pipeline would extract.
"""

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "out"
pytestmark = pytest.mark.skipif(not (OUT / "manifest.json").exists(), reason="dataset not generated (run data/generate.py)")

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "data"))

from app.policy import engine as e  # noqa: E402
from app.policy import ruleset  # noqa: E402
from app.policy.cases import case_from_customer_360  # noqa: E402

AS_OF = date(2026, 11, 1)
RS = ruleset.load()


def rows(ds, name):
    with open(OUT / ds / f"{name}.csv", encoding="utf-8", newline="") as f:
        return {(r.get("customer_id") or r[next(iter(r))]): r for r in csv.DictReader(f)}


@pytest.fixture(scope="module")
def results():
    import validate_layers as vl

    feats = vl.expected()
    cust, emp, bur, kyc = rows("lh_raw", "customers"), rows("lh_raw", "employment"), rows("lh_raw", "bureau_profiles"), rows("lh_raw", "ext_kyc_mock")
    cur = {r["customer_id"]: r for r in csv.DictReader(open(OUT / "lh_raw" / "employment.csv", encoding="utf-8")) if r["is_current"] == "true"}
    lines = defaultdict(lambda: defaultdict(D))
    for r in csv.DictReader(open(OUT / "lh_truth" / "external_statement_lines.csv", encoding="utf-8")):
        if r["direction"] == "CREDIT" and r["category"] in ("SALARY", "PENSION", "BUSINESS_RECEIPT"):
            lines[r["customer_id"]][r["txn_date"][:7]] += D(r["amount"])
    out = {}
    for i, cid in enumerate(sorted(cust)):
        c, b, f = cust[cid], bur[cid], feats[cid]
        row = {**f, "date_of_birth": c["date_of_birth"], "employment_type": cur[cid]["employment_type"], "bank_relationship": c["bank_relationship"],
               "bureau_history_status": b["history_status"], "has_written_off": b["has_written_off"], "has_settled": b["has_settled"], "kyc_status": c["kyc_status"],
               "residency_status": c["residency_status"], "is_staff": c["is_staff"], "pan_status": kyc[cid]["pan_status"],
               "name_match_score": kyc[cid]["name_match_score"], "aadhaar_check": kyc[cid]["aadhaar_check"]}
        net = D(cur[cid]["declared_monthly_net"])
        requested = min(D(2000000), max(D(50000), (net * (3 + i % 9) / 5000).to_integral_value() * 5000))
        evidence = None
        if c["bank_relationship"] == "NTB":
            m = lines[cid]
            evidence = (sum(m.values(), D(0)) / 6, len(m))
        case = case_from_customer_360(row, requested_amount=requested, requested_tenure_months=[24, 36, 48, 60][i % 4], as_of=AS_OF, income_evidence=evidence)
        out[cid] = (case, e.evaluate(RS, case))
    return out


def test_every_customer_gets_exactly_one_valid_recommendation(results):
    assert len(results) == 1000
    assert {a.recommendation for _, a in results.values()} <= {e.REC_OK, e.REC_REFER, e.REC_NO}
    for _, a in results.values():
        json.dumps(a.to_dict())
        assert len(a.rules) == 15 and a.reasons


def test_the_book_looks_realistic_not_all_approved_and_not_all_declined(results):
    c = Counter(a.recommendation for _, a in results.values())
    print("\noverall:", dict(c))
    assert all(c[k] >= 100 for k in (e.REC_OK, e.REC_REFER, e.REC_NO)), c
    assert 0.20 <= c[e.REC_OK] / 1000 <= 0.75, c


def test_every_segment_has_approvable_applicants_and_none_is_all_one_outcome(results):
    per = defaultdict(Counter)
    for case, a in results.values():
        per[case.segment][a.recommendation] += 1
    for seg, c in sorted(per.items()):
        print(f"{seg:9}", {k.split('_')[0][:4]: v for k, v in c.items()}, "n =", sum(c.values()))
        assert c[e.REC_OK] > 0, seg
        assert max(c.values()) < sum(c.values()), seg


def test_no_one_is_declined_for_lacking_a_credit_history(results):
    for case, a in results.values():
        s = {x.id: x.status for x in a.rules}
        if case.history_status == "NO_HISTORY":
            assert s["R07"] == e.SKIPPED and s["R08"] == e.SKIPPED
        if case.bureau_score is None:
            assert s["R07"] == e.SKIPPED
        # a score rule failure only ever comes from a real, low score
        assert (s["R07"] == e.FAIL) == (case.bureau_score is not None and case.bureau_score < 650)


def test_thin_files_are_not_pushed_to_one_outcome_just_for_being_thin(results):
    thin = [a for c, a in results.values() if c.history_status == "THIN"]
    assert len(thin) == 130
    kinds = Counter(a.recommendation for a in thin)
    print("thin files:", dict(kinds))
    assert kinds[e.REC_OK] > 0
    assert not all(a.recommendation == e.REC_REFER for a in thin)
    no_score = [a for c, a in results.values() if c.history_status == "THIN" and c.bureau_score is None]
    assert any(a.recommendation == e.REC_OK for a in no_score)  # thin and score-less can still be recommended on the evidence


def test_staff_nri_and_pensioners_are_referred_never_declined_for_that_reason(results):
    flagged = [(c, a) for c, a in results.values() if c.is_staff or c.is_nri or c.employment_type == "PENSIONER"]
    assert len(flagged) > 50
    for c, a in flagged:
        s = {x.id: x.status for x in a.rules}
        assert s["R13" if c.is_staff else "R14" if c.is_nri else "R15"] == e.REVIEW
        assert all(s[k] != e.FAIL for k in ("R13", "R14", "R15"))
        assert a.recommendation != e.REC_OK


def test_every_decline_recommendation_is_explained_by_a_demo_limit_and_names_it(results):
    for _, a in results.values():
        if a.recommendation == e.REC_NO:
            fails = [x for x in a.rules if x.status == e.FAIL]
            assert fails and all("demo assumption" in x.message for x in fails)
            assert a.reasons[0].split(":")[0] in {x.title for x in fails}


def test_recommended_amounts_are_affordable_priced_inside_the_band_and_within_limits(results):
    n = 0
    for case, a in results.values():
        f = a.figures
        if a.recommendation == e.REC_OK:
            n += 1
            assert 50000 <= f["recommended_amount"] <= min(case.requested_amount, 2000000) and f["recommended_amount"] % 5000 == 0
            assert f["foir_after_percent"] <= D(50)
            assert 1050 <= f["indicative_rate_bp"] <= 1900 and f["recommended_tenure_months"] <= f["max_tenure_months"]
    assert n >= 200


def test_report_the_distribution_by_segment_for_the_record(results, capsys):
    per = defaultdict(Counter)
    for case, a in results.values():
        per[case.segment][a.recommendation] += 1
    reasons = Counter(x.title for _, a in results.values() for x in a.rules if x.status in (e.FAIL, e.REVIEW))
    print("\nmost common reasons for a referral or decline:", reasons.most_common(6))
    assert per
