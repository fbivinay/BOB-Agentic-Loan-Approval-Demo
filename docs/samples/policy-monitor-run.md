# Policy monitor run (sample)

Run of 26 Nov 2026 07:52 UTC against the live official sources. Short quotes, URLs and hashes only; full documents stay in the private bucket and BigQuery.

## Policy monitor run `1c1a0c0b`: SUCCEEDED

Reports: 4 | New proposals: 2

| Source | Result | Version | Note |
|---|---|---|---|
| bob_personal_loan | baseline | 1 | text unchanged |
| bob_retail_loan_rates | baseline | 1 | text unchanged |
| bob_service_charges | baseline | 1 | text unchanged |
| rbi_cb_responsible_business_conduct | baseline | 1 | text unchanged |
| rbi_cb_kyc | unchanged | 2 | text unchanged |
| rbi_cb_credit_risk | unchanged | 1 | text unchanged |

### RULE_IMPACT: Baroda Personal Loan (product page)
- Source: https://bankofbaroda.bank.in/loans/personal-loan/baroda-personal-loan (v1, text sha256 `98021503363954f3`)
- Publication date: unknown
- VERIFICATION_CANDIDATE: age_min_years "21" vs `age.min_years` = 21 (DEMO_ASSUMPTION). Quote: "Applicants need to be at least 21 years of age to be eligible for a personal loan, while co-applicants are not allowed."
- VERIFICATION_CANDIDATE: age_max_at_maturity_years "60" vs `age.max_at_maturity.salaried` = 60 (DEMO_ASSUMPTION). Quote: "The maximum age to apply for personal loans is 60 years for salaried and 65 for non-salaried persons at the end of the repayment period."
- VERIFICATION_CANDIDATE: age_max_at_maturity_years "65" vs `age.max_at_maturity.non_salaried` = 65 (DEMO_ASSUMPTION). Quote: "The maximum age to apply for personal loans is 60 years for salaried and 65 for non-salaried persons at the end of the repayment period."
- VERIFICATION_CANDIDATE: loan_amount_max "Rs.20.00 lakhs" vs `loan.max_amount` = 2000000 (DEMO_ASSUMPTION). Quote: "Maximum Rs.20.00 lakhs (it is linked with borrower’s occupation and account relationship with Bank)."
- RULE_IMPACT: loan_amount_min "Rs.1.00 lakh" vs `loan.min_amount` = 50000 (DEMO_ASSUMPTION). Quote: "Minimum Rs.1.00 lakh for Metro & Urban Branch Rs.0.50 lakh for Rural & Semi-Urban Branch"
- VERIFICATION_CANDIDATE: loan_amount_min "Rs.0.50 lakh" vs `loan.min_amount` = 50000 (DEMO_ASSUMPTION). Quote: "Minimum Rs.1.00 lakh for Metro & Urban Branch Rs.0.50 lakh for Rural & Semi-Urban Branch"
- RULE_IMPACT: tenure_max_months "84 Months" vs `tenure.max_months` = 60 (DEMO_ASSUMPTION). Quote: "84 Months"

### FACT_CHANGED_NO_RULE_IMPACT: Retail Loans Interest Rates (MCLR, repo-linked rate, personal loan rates)
- Source: https://bankofbaroda.bank.in/interest-rate-and-service-charges/retail-loans-interest-rates (v1, text sha256 `0f813e3ade0adca4`)
- Publication date: unknown

### RULE_IMPACT: Service Charges (personal loan processing and other charges)
- Source: https://bankofbaroda.bank.in/interest-rate-and-service-charges/service-charges (v1, text sha256 `ee42342f0e0f982a`)
- Publication date: unknown
- RULE_IMPACT: prepayment_charge_percent "3% + GST" vs `fees.prepayment_charge_percent` = 0 (DEMO_ASSUMPTION). Quote: "Personal Loan: Pre-payment charges under fixed rate loan is as under: - @ 3% + GST on amount pre-paid during the first 3 years of loan perio"

### COMPLIANCE_ITEM: Reserve Bank of India (Commercial Banks - Responsible Business Conduct) Directions, 2025
- Source: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13140 (v1, text sha256 `d6bf8a745c282fec`)
- Publication date: unknown
- COMPLIANCE_ITEM: kfs_validity_working_days "at least three working days". Quote: "The KFS shall be provided with a unique proposal number and shall have a validity period of at least three working days for loans having ten"
- COMPLIANCE_ITEM: kfs_validity_working_days "one working day". Quote: "The KFS shall be provided with a unique proposal number and shall have a validity period of at least three working days for loans having ten"

Nothing is activated automatically: a person reviews proposals, drafts a rule set, the golden cases run, and they activate it.
