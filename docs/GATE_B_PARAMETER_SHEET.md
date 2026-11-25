# Gate B: Demo Parameter Sheet (Standard Baroda Personal Loan)

Generated from `backend/app/policy/schemes/baroda_personal_loan.yaml`. Rule set **baroda_personal_loan v1 (839ebd199f78)**. Do not edit by hand: run `scripts/generate_parameter_sheet.py`.

**Every value is a `DEMO_ASSUMPTION`.** Nothing has been verified against an official Bank of Baroda document, and nothing here is Bank of Baroda policy. The last column shows what unverified search excerpts said, for comparison only. The engine never reads that column, and the loader rejects any value labelled `UNVERIFIED_CANDIDATE`.

Status: **Gate B closed on 26 Nov 2026** on the project owner's instruction to finish it, with the resolutions below. Values stay `DEMO_ASSUMPTION`; when an official document confirms one, it moves to `VERIFIED` through a new rule-set version activated by an authorised human.

| Parameter | Demo value | Label | Meaning | Unverified excerpt (not used) |
|---|---|---|---|---|
| `age.min_years` | 21 | DEMO_ASSUMPTION | Youngest applicant age. | 21 |
| `age.max_at_maturity.salaried` | 60 | DEMO_ASSUMPTION | Age (completed years) when the last instalment falls, salaried and government. | 60 (another excerpt said 58) |
| `age.max_at_maturity.non_salaried` | 65 | DEMO_ASSUMPTION | Same limit for self-employed applicants. | 65 |
| `employment.min_current_tenure_months` | 12 | DEMO_ASSUMPTION | Salaried applicants with less time in the current job are referred, not rejected. | 1 year employment / 1 year stable business |
| `loan.min_amount` | 50,000 | DEMO_ASSUMPTION | Smallest loan, rupees. | 1.00 lakh metro and urban, 0.50 lakh rural and semi-urban |
| `loan.max_amount` | 2,000,000 | DEMO_ASSUMPTION | Largest loan, rupees. | 20.00 lakh, linked to occupation and account relationship |
| `loan.amount_step` | 5,000 | DEMO_ASSUMPTION | Eligible amounts are rounded down to this step. |  |
| `loan.max_income_multiple` | 20 | DEMO_ASSUMPTION | Loan may not exceed this many months of verified net income. |  |
| `tenure.min_months` | 12 | DEMO_ASSUMPTION | Shortest tenure. |  |
| `tenure.max_months` | 60 | DEMO_ASSUMPTION | Longest tenure. | 84 / 72 / 48-60 months by category |
| `tenure.step_months` | 12 | DEMO_ASSUMPTION | Tenure options offered in an enquiry. |  |
| `income.min_net_monthly` | 15,000 | DEMO_ASSUMPTION | Lowest verified net monthly income, rupees. |  |
| `foir.max_percent` | 50 | DEMO_ASSUMPTION | Existing obligations plus the new EMI, as a share of verified net income. |  |
| `income.min_evidence_months` | 3 | DEMO_ASSUMPTION | Months of income credits needed to treat income as verified (established history). |  |
| `income.evidence_tolerance_percent` | 15 | DEMO_ASSUMPTION | Observed income may differ from declared income by this much before the case is referred as inconsistent. |  |
| `bureau.min_score` | 650 | DEMO_ASSUMPTION | Applied only when a bureau score exists. A missing score is never a failure. | aggregators only: 650 to 750 (no official figure found) |
| `repayment.fail_dpd_days` | 90 | DEMO_ASSUMPTION | Worst delay in the last 12 months at or above this is potentially ineligible. |  |
| `repayment.review_dpd_days` | 30 | DEMO_ASSUMPTION | Worst delay at or above this (below the fail level) is referred. |  |
| `repayment.review_late_months_24m` | 4 | DEMO_ASSUMPTION | This many late months across 24 months is referred. |  |
| `conduct.review_returned_debits_6m` | 2 | DEMO_ASSUMPTION | Returned (bounced) debits on the Bank of Baroda account in six months at or above this are referred. |  |
| `kyc.min_name_match_percent` | 85 | DEMO_ASSUMPTION | Name match score from the mock KYC service below this is referred. |  |
| `pricing.score_bands` | 750+: 10.50%<br>700+: 11.50%<br>650+: 13.00%<br>0+: 15.00% | DEMO_ASSUMPTION | Rate by bureau score band. | existing customers floating 12.15-15.75%; others 14.15-17.75% (CIBIL linked) |
| `pricing.no_score_rate_bp` | 1,200 | DEMO_ASSUMPTION | Applicants with no usable score are priced in a middle band; this is pricing, not a failed rule. |  |
| `pricing.ntb_premium_bp` | 100 | DEMO_ASSUMPTION | Added for customers new to the bank. | about 200 bp between the two excerpt bands |
| `pricing.min_rate_bp` | 1,050 | DEMO_ASSUMPTION | Floor. Matches the database demo limit. |  |
| `pricing.max_rate_bp` | 1,900 | DEMO_ASSUMPTION | Ceiling. Matches the database demo limit. |  |
| `fees.processing_bp` | 100 | DEMO_ASSUMPTION | Processing fee, basis points of the sanctioned amount (1.00%). | 1.00% to 2.00% plus GST (pages conflict) |
| `fees.processing_min` | 1,000 | DEMO_ASSUMPTION | Minimum processing fee, rupees. | 1,000 plus GST |
| `fees.processing_max` | 10,000 | DEMO_ASSUMPTION | Maximum processing fee, rupees. | 10,000 plus GST |
| `fees.gst_percent` | 18 | DEMO_ASSUMPTION | GST on the processing fee. |  |
| `fees.penal_interest_percent_pa` | 2 | DEMO_ASSUMPTION | Extra interest on overdue amounts, per annum. | 2% |
| `fees.prepayment_charge_percent` | 0 | DEMO_ASSUMPTION | Charge on prepayment or foreclosure. | nil (no pre-closure charges) |
| `kfs.cooling_off_days` | 3 | DEMO_ASSUMPTION | Days in which the borrower may exit after disbursement. |  |
| `documents.required` | Signed loan application form (all)<br>Recent passport-size photograph (all)<br>PAN card (all; waived if etb_kyc_current)<br>Identity and address proof (masked Aadhaar (all; waived if etb_kyc_current)<br>Salary slips for the last 3 months (salaried)<br>Income tax return with computation of income for the last year (self_employed)<br>Proof of business (registration or GST certificate) (self_employed)<br>Pension payment order (pensioner)<br>Bank statement for the last 6 months (all; waived if etb_income_at_bob) | DEMO_ASSUMPTION | Required documents by employment type. An existing customer with current KYC need not resubmit PAN and address proof, and one whose income is credited to their Bank of Baroda account need not bring a bank statement (the bank already holds it). |  |
| `exclusion.staff` | applies | DEMO_ASSUMPTION | Whether bank staff are excluded from this scheme is not verified. Flagged for review. On fail: NEEDS_REVIEW. | staff not eligible |
| `exclusion.nri` | applies | DEMO_ASSUMPTION | Whether non-resident applicants are excluded from this scheme is not verified. Flagged for review. On fail: NEEDS_REVIEW. | NRI/PIO not eligible |
| `scope.pensioner_other_scheme` | applies | DEMO_ASSUMPTION | Pensioners are expected to be served by a separate pensioner scheme, which is not modelled here, so they are referred. On fail: NEEDS_REVIEW. | a separate pensioner loan page exists |

## Segment overrides

| Segment | Parameter | Value | Label | Why |
|---|---|---|---|---|
| ETB_THIN | `income.min_evidence_months` | 6 | DEMO_ASSUMPTION | Thin file |
| ETB_NTC | `income.min_evidence_months` | 6 | DEMO_ASSUMPTION | No history |
| NTB_THIN | `income.min_evidence_months` | 6 | DEMO_ASSUMPTION | Thin file |
| NTB_NTC | `income.min_evidence_months` | 6 | DEMO_ASSUMPTION | No history |

## How the engine uses them

- **Recommendation:** any rule FAIL gives `POTENTIALLY_INELIGIBLE`; otherwise any NEEDS_REVIEW gives `REFER_FOR_FURTHER_REVIEW`; otherwise `RECOMMENDED_FOR_APPROVAL`. A human Assistant Manager or Branch Manager decides.
- **Missing history is never a failure:** the score rule is SKIPPED when there is no score, and thin or no-history applicants are asked for six months of income evidence instead.
- **Exclusions only refer:** staff, NRI and pensioner coverage are flagged for review, because they are unverified. They can fail a case only once VERIFIED.
- **Capacity:** income used is the lower of declared and observed. The EMI room is the FOIR limit times income minus existing obligations, and the eligible amount is capped by the maximum loan and the income multiple and rounded down to the step.
- **Pricing:** by score band, mid band when there is no score, plus the new-to-bank premium, clamped to the floor and ceiling.
- **KFS:** the processing fee plus GST is deducted from the amount disbursed. APR is the IRR of the actual cash flows, shown nominal annualised (monthly x 12) with the effective annual rate beside it.

## Gate B resolutions

1. **Rate band kept at 10.50% to 19.00%.** It is in line with what Indian banks generally charge for unsecured personal loans. The excerpt figures are not copied in, because unverified Bank of Baroda figures must not be implemented.
2. **Maximum tenure kept at 60 months**, the common market maximum for personal loans and the more conservative choice. Up to 84 months stays a candidate until verified.
3. **One minimum loan of 50,000 for every branch**, because branch-tier minimums are unverified and would add a rule the demo cannot justify.
4. **Pensioners are referred**, not assessed under this scheme, so that two schemes are never mixed.
5. **APR shown nominal annualised** (monthly IRR x 12) with the effective annual rate beside it, both labelled on the KFS.
6. **Required documents added** (`documents.required`), by employment type, with the two waivers an existing customer earns: current KYC, and income credited to their Bank of Baroda account.
