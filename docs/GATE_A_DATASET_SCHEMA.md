# Gate A: Synthetic Dataset Schema

Status: **decisions confirmed by the project owner; six review corrections implemented and validated. Awaiting final review before the rest of Phase 4.**

All data is synthetic. No real customer, PAN, Aadhaar, mobile number or account is represented. The generator is seeded and reproducible (`as_of_date = 2026-11-01`; Bank of Baroda transactions cover 2025-11-01 to 2026-10-31).

The authoritative machine-readable schema is `data/generator/schema.py`. This document explains it.

## 1. Confirmed decisions

1. **Credit-history status is not on `customers`.** It lives in `bureau_profiles` (what the application can see) and in `customer_ground_truth` (test truth only). The application and agent runtime cannot read ground-truth classifications (section 8).
2. **Sample documents approved:** the `documents_truth` table, for 120 customers and 480 documents, kept separate from application-facing data. Rendering the actual PDF and image files is a later Phase 4 step.
3. **Segment distribution fixed:**

| Segment | Established | Thin file | No history | Total |
|---|---:|---:|---:|---:|
| ETB | 440 | 80 | 80 | 600 |
| NTB | 250 | 50 | 100 | 400 |
| Total | 690 | 130 | 180 | 1,000 |

## 2. The six corrections

| # | Correction | What was done |
|---|---|---|
| 1 | One canonical customer ID | `BOB-C0001` to `BOB-C1000` everywhere. Validation fails on any other format, and on any `CUST-` text in any file (section 7) |
| 2 | Deterministic reproducibility | Integer PRNG, integer money, `Decimal` EMI maths, per-customer streams, canonical CSV, manifest with checksums. `generated_at` and similar metadata removed from the dataset and moved to a separate `run_info.json` outside every checksum (section 6) |
| 3 | Exact thin-file scores | **78 of 130 have a score (ETB 48, NTB 30) and 52 are null (ETB 32, NTB 20).** Asserted by the generator, this document and the validation suite |
| 4 | Ground-truth isolation | `lh_truth` is a separate BigQuery dataset readable only by a dedicated evaluation identity. Runtime identities are denied, and 18 automated checks prove it (section 8) |
| 5 | Synthetic PAN safety | PANs are `SYNP` + 7 digits + a letter, 12 characters, so they can never be a valid 10-character PAN (section 5) |
| 6 | Repayment-account consistency | Repayment accounts must be active Bank of Baroda accounts held by the loan's own ETB customer. NTB repayment evidence is kept on non-Bank-of-Baroda accounts (section 4) |

## 3. Where the data lives

All tables load into the BigQuery sandbox through load jobs (no DML, no streaming). BigQuery does not enforce keys, so the validation suite does.

| Dataset | Purpose | Readable by application and agent identities? |
|---|---|---|
| `lh_raw` | Application-facing raw layer | Yes (READER) |
| `lh_truth` | Ground truth for building and testing documents and for evaluation | **No** |
| `lh_curated`, `lh_gold` | Derived layers, built later from `lh_raw` | Yes |

**Search behaviour:** the Loan Officer's lookup searches ETB customers only (by customer ID, mobile, PAN or account number). An NTB person is not found, exactly like a real walk-in, and is registered as a new enquiry. NTB identity data exists only to drive the mock bureau and KYC lookups (by PAN) and to build their documents.

## 4. Tables

Types: `MONEY` is whole rupees written `123456.00` (BigQuery NUMERIC), `BP` is a rate in basis points written `9.45` (NUMERIC(5,2)), `PCT2` is `0.97`. `PK` is unique and non-null, `FK` is validated.

### `lh_raw.branches` (40 rows)
`branch_id` PK (`BRN-001`…), `branch_name`, `city`, `state`, `pincode`, `city_tier` (`METRO`, `URBAN`, `SEMI_URBAN`, `RURAL`), `ifsc_code` (synthetic, `BARB0SYN` + 3 digits).

### `lh_raw.customers` (1,000 rows)
`customer_id` PK (`BOB-C0001`…), `bank_relationship` (`ETB`/`NTB`), `full_name`, `date_of_birth`, `gender` (`M`/`F`/`O`), `mobile`, `email` (`@example.invalid`), `pan` (synthetic, section 5), `aadhaar_last4` (last four digits only), `address_line`, `city`, `state`, `pincode`, `residency_status` (`RESIDENT`/`NRI`), `is_staff`, `home_branch_id` FK (ETB only), `customer_since` (ETB only), `kyc_status` (`VERIFIED`, `EXPIRED`, `PENDING`, `NOT_APPLICABLE` for NTB), `kyc_last_verified_on`.
There is **no credit-history column**.

### `lh_raw.employment` (1,214 rows)
`employment_id` PK, `customer_id` FK, `employment_type` (`SALARIED_PRIVATE`, `SALARIED_GOVT`, `SALARIED_PSU`, `SELF_EMPLOYED_PROFESSIONAL`, `SELF_EMPLOYED_BUSINESS`, `PENSIONER`), `employer_name`, `employer_category`, `designation`, `start_date`, `end_date`, `is_current` (exactly one true per customer), `declared_monthly_gross`, `declared_monthly_net` (MONEY).

### `lh_raw.accounts` (1,330 rows)
`account_id` PK (`ACC-000001`…), `institution_name`, `is_bob`, `branch_id` FK (Bank of Baroda only), `account_type` (`SAVINGS`, `SALARY`, `CURRENT`, `PENSION`), `opened_on`, `status` (`ACTIVE`, `DORMANT`, `CLOSED`), `is_salary_account`, `current_balance`, `avg_balance_6m` (MONEY), `currency`.

### `lh_raw.account_holders` (1,397 rows)
`account_id` FK, `customer_id` FK (composite PK), `holder_role` (`PRIMARY`/`JOINT`), `relationship_since`. About 8% of active Bank of Baroda accounts have a joint holder.

### `lh_raw.transactions` (193,795 rows, Bank of Baroda accounts only)
`txn_id` PK, `account_id` FK, `txn_date`, `amount`, `direction`, `category` (`SALARY`, `PENSION`, `BUSINESS_RECEIPT`, `TRANSFER_IN`, `EMI`, `UPI`, `ATM`, `RENT`, `UTILITY`, `CARD`, `INVESTMENT`, `INTEREST`, `CHARGES`), `channel`, `narration`, `counterparty`, `txn_status` (`POSTED`/`RETURNED`), `balance_after`, `loan_id` FK, `tradeline_id` FK.
Exactly one of `loan_id` (a Bank of Baroda loan) or `tradeline_id` (another lender's loan paid by NACH from the account) is set on an EMI row.

### `lh_raw.existing_loans` (341 rows, Bank of Baroda loans only)
`loan_id` PK, `customer_id` FK (ETB only), `product_type`, `sanctioned_amount`, `outstanding_amount`, `emi_amount`, `interest_rate` (BP, synthetic), `tenure_months`, `start_date`, `maturity_date`, `status` (`ACTIVE`, `CLOSED`, `OVERDUE`), `repayment_account_id` FK.

### `lh_raw.loan_repayments` (6,087 rows)
`repayment_id` PK, `loan_id` FK, `due_date`, `due_amount`, `paid_date`, `paid_amount`, `days_past_due`, `status` (`PAID_ON_TIME`, `PAID_LATE`, `MISSED`; `PARTIAL` is allowed but not generated).

### `lh_raw.bureau_profiles` (1,000 rows, mock bureau)
`customer_id` PK/FK, `bureau_name` (`MOCK_CIBIL`), `history_status` (`ESTABLISHED`, `THIN`, `NO_HISTORY`), `score` (300–900; null for all NO_HISTORY and for 52 thin files), `score_date`, `tradeline_count`, `active_tradeline_count`, `oldest_tradeline_date`, `total_outstanding`, `total_monthly_emi`, `enquiries_last_6m`, `max_dpd_last_12m` (null for NO_HISTORY), `has_written_off`, `has_settled`, `report_date`. The aggregates are validated against the tradelines.

### `lh_raw.bureau_tradelines` (3,888 rows)
`tradeline_id` PK, `customer_id` FK, `lender_name`, `lender_type`, `is_bob`, `existing_loan_id` FK (Bank of Baroda loans only), `account_type` (`PERSONAL_LOAN`, `HOME_LOAN`, `AUTO_LOAN`, `GOLD_LOAN`, `CREDIT_CARD`, `SECURED_CREDIT_CARD`, `CONSUMER_LOAN`, `EDUCATION_LOAN`, `OVERDRAFT`), `ownership`, `opened_date`, `closed_date`, `sanctioned_amount`, `credit_limit`, `current_balance`, `emi_amount`, `status` (`ACTIVE`, `CLOSED`, `OVERDUE`, `WRITTEN_OFF`, `SETTLED`), `max_dpd`, `monthly_dpd_24m` (24 integers, oldest first). Bank of Baroda tradelines match their loan exactly (amount, balance, EMI) and derive their DPD from the repayment schedule.

### `lh_raw.historical_applications` (2,000 rows)
Past Personal Loan applications: `application_id` PK, `customer_id` FK (about 55%, ETB; otherwise null with an anonymous `applicant_ref`), `scheme`, `application_date`, `channel`, `relationship_at_application`, `history_status_at_application`, requested and approved amount, tenure and rate (synthetic, not Bank of Baroda's), `outcome` (`APPROVED`, `DECLINED`, `WITHDRAWN`), `decline_reason_code`, `decision_date`, `processing_days`, `decided_by_role`.

### `lh_raw.ext_kyc_mock` (1,000 rows)
`customer_id` PK/FK, `pan`, `pan_status` (`VALID` 970, `INVALID` 5, `NOT_FOUND` 15, `DEACTIVATED` 10), `pan_name_on_record`, `name_match_score` (PCT2), `aadhaar_check`, `address_match`, `checked_at` (deterministic, derived from the as-of date).

### `lh_truth.customer_ground_truth` (1,000 rows)
`customer_id` PK/FK, `segment_code` (`ETB_EST`, `ETB_THIN`, `ETB_NTC`, `NTB_EST`, `NTB_THIN`, `NTB_NTC`), `edge_case_tags` (JSON), `persona_notes`.

### `lh_truth.external_statement_lines` (65,300 rows)
Other-bank statement lines for NTB customers (six months), used only to build statement documents and to check extraction accuracy: `line_id` PK, `customer_id` FK, `account_id` FK (a **non-Bank-of-Baroda** account held by the customer), `tradeline_id` FK (set on EMI lines, a non-Bank-of-Baroda tradeline of the same customer), `statement_month`, `txn_date`, `amount`, `direction`, `category`, `narration`, `balance_after`.

### `lh_truth.documents_truth` (480 rows, 120 customers, 20 per segment)
`doc_id` PK, `customer_id` FK, `doc_type` (`PAN_CARD`, `AADHAAR_MASKED`, `SALARY_SLIP` or `ITR`, `BANK_STATEMENT`), `period`, `page_count`, `quality` (`CLEAN` 240, `SKEWED` 96, `LOW_RES` 72, `SCANNED_NOISE` 72), `expected_fields` (JSON, including a "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT" notice), `injected_discrepancy` (`NONE` 408, `NAME_VARIANT` 24, `INCOME_MISMATCH` 24, `DOB_MISMATCH` 12, `PAN_TYPO` 12), `file_name`. Statement fields are computed from the actual transactions or external lines, so they reconcile.

### Repayment-account and external-bank rules (correction 6)
- Every `existing_loans.repayment_account_id` is an `ACTIVE`, `is_bob` account whose **PRIMARY** holder is the loan's own customer, and which existed before the loan started.
- EMI debits with a `loan_id` post only to that loan's repayment account, and posted debits equal paid repayments exactly. Every returned EMI is followed by a late repayment.
- Another lender's EMI from a Bank of Baroda account uses `tradeline_id` of a non-Bank-of-Baroda tradeline of the same customer.
- NTB repayment evidence exists only as `external_statement_lines` on non-Bank-of-Baroda accounts, referencing non-Bank-of-Baroda tradelines. NTB customers have no Bank of Baroda loans, tradelines or accounts.

## 5. Synthetic identity safety (correction 5)

- **PAN:** `SYNP0000123K` form (12 characters). A real PAN is exactly 10 characters (`AAAAA9999A`), so these cannot be submitted as real PANs. The check rejects any value matching the real format, including the deliberate typo documents. The application's own validation will accept only the synthetic form.
- **Never sent externally:** synthetic PANs are never sent to any government, banking, KYC or external verification service. The mock KYC is a local table.
- **Other identifiers:** accounts `ACC-000001`, IFSC `BARB0SYN###`, emails `@example.invalid`, Aadhaar last four digits only.
- **Documents and data:** every sample document carries a synthetic notice, the manifest states the data is synthetic, and BigQuery dataset descriptions say "SYNTHETIC".
- **Known limits:** names are random and could coincide with real ones. Mobile numbers are realistic 10-digit Indian-style numbers with a leading digit 6-9 (decided). They are random and could coincide with real numbers, so nothing is ever sent to them.

## 6. Determinism (correction 2)

- **Deterministic content:** the canonical CSV files and `manifest.json`. Same seed gives byte-identical files. The manifest holds a SHA-256 per file and a canonical hash, and no runtime metadata.
- **Runtime metadata:** `run_info.json` (when it ran, Python version, platform) is volatile, sits outside every checksum, and is never loaded as data. Load-time metadata will go to a separate `load_runs` record.
- **Mechanics:** SplitMix64 integer PRNG with a per-customer stream, whole-rupee integers, basis-point rates, `Decimal` EMI maths, rows sorted by primary key, no clock or environment reads. All dataset timestamps derive from the as-of date.
- **Tests:** identical canonical hash in one process, and across separate processes with different `PYTHONHASHSEED` values. A different seed changes the data. Current canonical hash for seed `20261125`: `3e6e65bd2a5d282da9e84692c583d1b9fc16b46db767e821e29d42aa03317fe4`.

## 7. Validation (105 checks, all passing)

`backend/.venv/Scripts/python data/validate.py` runs these against the canonical files. A crash inside the suite is reported as a failure.

- **A. Integrity:** row counts and SHA-256 match the manifest; manifest has no runtime metadata.
- **B. Segments:** exact counts 440/80/80/250/50/100; ground-truth segment agrees with relationship and bureau status for all 1,000 customers; no history column on `customers`.
- **C. Keys:** every primary key unique and non-null (15 tables); 21 foreign keys resolve.
- **D. IDs:** every customer ID is `BOB-C0001` form, IDs run `BOB-C0001`…`BOB-C1000`, and no `CUST-` anywhere.
- **E. Relationships:** ETB has a Bank of Baroda primary account, branch and since-date; NTB has none; one current job each; transactions only on Bank of Baroda accounts; external lines only on non-Bank-of-Baroda accounts held by the customer.
- **F. Credit history:** NTC has zero tradelines and null score, DPD and oldest date; thin has 1–2 tradelines, oldest under 12 months, **exactly 78 scores present (ETB 48, NTB 30) and 52 null (ETB 32, NTB 20)**; established has 3–8 tradelines and a score; all three states appear under ETB and NTB; bureau aggregates equal the tradelines.
- **G. Loans and repayment accounts:** Bank of Baroda loans and tradelines map one to one with equal amounts; repayment accounts are active Bank of Baroda accounts whose primary holder is the loan customer and which predate the loan; EMI references are consistent; external EMI evidence references non-Bank-of-Baroda tradelines of the same NTB customer; posted EMI debits equal paid repayments; every returned EMI has a late repayment.
- **H. Money:** balance chains consistent and never negative, matching account balances; salary credits equal declared net for ordinary salaried customers and differ by more than 15% for the income-mismatch tag.
- **I. Realism and safety:** ages 18–62 with exactly 8 under 21; income floors; every PAN synthetic, unique, consistent with the KYC table, never matching the real format; four Aadhaar digits only; reserved email domain; synthetic IFSC codes; deterministic KYC timestamps; no future dates.
- **J. Edge cases:** every tag has exactly its specified count (table below), plus behavioural checks (FOIR, staff and NRI split, duplicate mobiles, returned EMIs, KYC states, dormant accounts, government salary accounts, new jobs).
- **K. Documents:** 480 documents for 120 customers, 4 each, 20 per segment; quality and discrepancy counts exact; every document marked synthetic; statements describe an account the customer holds.

Plus 13 pytest tests (`data/tests`), including **negative tests** that tamper with the data (real-format PAN, a `CUST-` ID, a changed thin-file score, a swapped repayment account, an external EMI moved to a Bank of Baroda account, a changed file) and require the suite to fail.

## 8. Ground-truth access isolation (correction 4)

BigQuery enforces it, not naming or instructions:

| Identity | Project role | `lh_raw` | `lh_truth` |
|---|---|---|---|
| `sa-app-runtime` (backend) | `bigquery.jobUser` only | READER | **denied** |
| `sa-agent-runtime` (agents) | `bigquery.jobUser` only | READER | **denied** |
| `sa-truth-eval` (controlled generation and evaluation) | `bigquery.jobUser` | none | READER |
| project owner (the human operator) | owner | yes | yes |

- `scripts/setup_bq_access.py` creates this (free, idempotent, no keys). `scripts/test_truth_isolation.py` acts as each identity by impersonation and proves it: **18 of 18 checks pass.** For each runtime identity: reading `lh_raw` works (positive control), and querying, listing (`INFORMATION_SCHEMA` and the API), reading rows, reading metadata, and joining `lh_truth` with `lh_raw` are all denied (HTTP 403). The evaluation identity can read `lh_truth`. The runtime identities hold no project role beyond `bigquery.jobUser`, and `lh_truth`'s access list names neither.
- A code test fails if any application code references `lh_truth`, `customer_ground_truth`, `documents_truth`, `external_statement_lines` or `segment_code`.
- Ground-truth CSV files exist only on the local machine (gitignored) and are never deployed to the hosts.

## 9. Distribution and edge-case counts (exact, from the generated manifest)

| Tag | Count | Tag | Count |
|---|---:|---|---:|
| `THIN_ONE_8M` | 30 | `RETURNED_EMI` | 25 |
| `THIN_CARD_ONLY` | 25 | `LATE_DPD30_EST` | 80 |
| `THIN_CLOSED_LOAN` | 15 | `WRITTEN_OFF_OR_SETTLED` | 15 |
| `THIN_SECURED_CARD` | 15 | `BORDERLINE_FOIR` | 60 |
| `THIN_BOB_ONLY` | 10 | `SALARY_IRREGULAR` | 70 |
| `THIN_DPD` | 15 | `INCOME_MISMATCH_15` | 80 |
| `NTC_YOUNG` | 60 | `JOB_UNDER_1Y` | 90 |
| `NTC_SE_IRREG` | 40 | `NAME_VARIANT` | 50 |
| `NTC_PENSIONER` | 30 | `DUP_MOBILE` (6 pairs) | 12 |
| `AGE_UNDER_21` | 8 | `KYC_EXPIRED_PENDING` | 30 |
| `AGE_57_62` | 25 | `DORMANT_ACCOUNT` | 30 |
| `STAFF` (ETB only) | 20 | `GOVT_SALARY_BOB` | 50 |
| `NRI` (12 ETB, 18 NTB) | 30 | | |

The other 35 thin files carry the default pattern (a loan and a card). Other tags overlap freely except where they conflict (for example an age-under-21 customer is never also an NTC pensioner).

## 9a. Rendered sample documents (480 files, local and gitignored)

- 240 clean PDFs (text layer), 96 skewed PNGs, 72 low-resolution JPGs, 72 scanned image-only PDFs. Every page carries a diagonal watermark, a footer and file metadata reading "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT".
- Documents hash: `dfdddfe73a217ecd85bc6991fd9c324743b157b184f541724d743ab52e2c8218` (byte-identical only with the same reportlab and Pillow versions, recorded in `documents_run_info.json`).
- 10 document checks pass (`data/validate_docs.py`): files and checksums, page counts, synthetic metadata, text layers, exact field values, scanned PDFs without a text layer, non-blank images, visible injected discrepancies, no real-format PAN.
- Review package (local): `data/out/review/contact_sheet_1.png`, `contact_sheet_2.png` and 12 sample files.

## 9b. BigQuery load (done)

- `scripts/load_bigquery.py` runs the 105-check validation first, then loads all 15 tables with `bq load --replace` (CSV, explicit schema, no billing, no GCS). Reloads are idempotent and leave dataset access untouched. Load metadata goes to `data/out/load_run.json`, outside the dataset.
- Type mapping: money, rates and percentages are `NUMERIC`, counts are `INTEGER`, flags are `BOOLEAN`, `edge_case_tags` and `expected_fields` are `JSON`. `monthly_dpd_24m` is loaded as `STRING` holding a JSON array, because CSV cannot carry repeated fields. The curated layer will parse it.
- `scripts/validate_bigquery.py` checks the loaded data: 62 of 62 pass. These include row counts against the manifest, the 440/80/80/250/50/100 split, 78/52 thin-file scores, primary and foreign keys, NULL loading, ID and PAN formats, NUMERIC precision, repayment-account ownership, balance reconciliation across all 825 active Bank of Baroda accounts, and that the runtime identities can read `lh_raw` but not any `lh_truth` table. The 18 isolation checks also pass on the loaded tables.
- The evaluation identity cannot read `lh_raw`, by design, so cross-dataset comparisons run in Python.
- Sandbox: tables expire after 60 days and the 10 GiB storage quota is lifetime. The load is about 32 MB, and the loader counts toward that quota on each run, so reloads stay deliberate.

## 9c. Curated and gold layers (built)

Built by `scripts/build_layers.py` from SQL in `bigquery/` (`CREATE OR REPLACE TABLE ... AS SELECT`, free in the sandbox, 500 MB bytes-billed cap per statement). The SQL reads `lh_raw` and `lh_curated` only; the build refuses to run if any file mentions `lh_truth`. All dates are relative to the fixed as-of date 2026-11-01, never the current date. Months and years are completed months and years.

| Table | Rows | Purpose |
|---|---:|---|
| `lh_curated.tradeline_dpd_24m` | 93,312 (3,888 x 24) | Unnests the JSON-text DPD array: one row per tradeline per month |
| `lh_curated.monthly_income_credits` | per customer-month | Posted salary/pension and business-receipt credits, Bank of Baroda primary accounts, May to Oct 2026 |
| `lh_curated.credit_features` | 1,000 | One row per customer: age, job tenure, income and obligation features |
| `lh_gold.customer_360` | 1,000 | The one table the lookup and the assessment agent read: identity, KYC, employment, bureau status and features, bank behaviour |

- **Bank-held columns are ETB only** (salary-account flag, account age, average balance, salary credits and regularity, business receipts, income-credit versus declared income, Bank of Baroda EMI and loan count, returned debits). They are NULL for all 400 NTB customers, because NTB external statements exist only as documents at application time and the layers never see ground truth.
- `salary_regularity` is the population standard deviation of the six monthly salary or pension credits divided by their mean, so 0 means perfectly steady. `income_credit_vs_declared_pct` compares observed credits with declared net income, so income mismatches stand out.
- `total_monthly_obligations` is the bureau EMI total, which already includes Bank of Baroda loans, so nothing is double counted. `existing_obligation_pct` is that figure over declared net income. It is existing obligations only, not the FOIR for a new loan, which is a policy-engine calculation.
- **Validation:** `scripts/validate_layers.py` recomputes 28 features independently in Python from the local CSVs and compares all 1,000 customers with BigQuery, then checks structure and business rules and runtime access. 47 of 47 pass. The two percentage columns allow one rounding step, because BigQuery rounds half up and Python half to even (one customer each landed exactly on a halfway point).
- The runtime identities can read all four tables and still cannot read `lh_truth` (18 of 18 isolation checks pass).
- Not built yet: policy, OCR and decision tables, and portfolio KPIs, which belong to later phases.

## 10. Limitations

- **Phase 4 is incomplete by design:** the BigQuery load, curated and gold layers, and the PDF and image rendering of the sample documents are not built, because you asked me to stop after these corrections.
- **Owner access (documented, by design):** the project owner retains administrative access to `lh_truth` through project ownership. The backend and agent runtime identities must remain unable to access it, and the 18 isolation checks (unchanged) prove they are. The controlled identity `sa-truth-eval` exists for pipelines, but no key has been created for it.
- **Sandbox:** DDL such as `CREATE TABLE AS SELECT` worked for the isolation probe tables. Whether it covers the curated and gold build at full size is checked in the load step.
- **Transaction volume:** 193,795 transactions (earlier estimates of 300,000–350,000 are superseded). Coverage checks L1-L5 confirm this is enough for the demo scenarios: every ETB customer has 12 months of activity (at least 150 transactions, mean about 320), every NTB customer has 6 months of external statement lines (mean about 162), and 2,000 historical applications cover 34 months for the analytics. Portfolio KPIs come from historical applications and decision events, not from transactions.
