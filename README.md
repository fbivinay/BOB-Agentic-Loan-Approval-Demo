# Baroda Lending Desk: Agentic AI Loan Approval Demo

A presales demonstration of how AI agents can speed up Personal Loan application and assessment while a human keeps the final decision. It is a demo only: all data is synthetic, interest rates and limits are illustrative `DEMO_ASSUMPTION` values, and nothing here is a loan offer or Bank of Baroda's actual policy.

**Product:** Standard Baroda Personal Loan. **Roles:** Loan Officer, Assistant Manager, Branch Manager (demo permissions). **AI recommends; only an Assistant Manager or Branch Manager decides.**

**Cost:** the project runs entirely on free tiers (no billing enabled anywhere).

## Layout

```
frontend/   Next.js, TypeScript, Tailwind (desktop and laptop only)
backend/    FastAPI
scripts/    connectivity and billing checks
docs/       plan, dataset schema, notes
supabase/   migrations
data/       synthetic data generator (later phase)
```

## Local setup

```bash
cp .env.example .env            # then fill in your own keys; .env is gitignored

# backend
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Windows; use .venv/bin/python elsewhere
.venv/Scripts/python -m pytest
.venv/Scripts/python -m uvicorn app.main:app --reload       # http://localhost:8000/health

# frontend
cd ../frontend
npm install
npm run dev                     # http://localhost:3000
```

Connectivity and billing checks (nothing is purchased or enabled):

```bash
backend/.venv/Scripts/python scripts/check_connectivity.py
backend/.venv/Scripts/python scripts/check_connectivity.py --billing-canary   # needs `gcloud auth login`
```

## Synthetic dataset

```bash
backend/.venv/Scripts/python data/generate.py          # writes data/out (gitignored): canonical CSVs + manifest.json
backend/.venv/Scripts/python data/validate.py          # 105 validation checks
backend/.venv/Scripts/python -m pytest data/tests -q   # reproducibility, validation and negative tests
```

All identifiers are synthetic (customer IDs `BOB-C0001`…, PANs `SYNP#######X`). Ground-truth tables are kept in a separate BigQuery dataset that the application identities cannot read (`scripts/setup_bq_access.py`, `scripts/test_truth_isolation.py`).

Load into BigQuery (free sandbox, load jobs) and verify:

```bash
backend/.venv/Scripts/python scripts/load_bigquery.py
backend/.venv/Scripts/python scripts/validate_bigquery.py
```

Build and verify the curated and gold layers:

```bash
backend/.venv/Scripts/python scripts/build_layers.py
backend/.venv/Scripts/python scripts/validate_layers.py
```

Operational database (Supabase Postgres): migrations, tests and seeding

```bash
backend/.venv/Scripts/python -m pytest supabase/tests backend/tests -q    # needs Docker running; starts a throwaway Postgres
backend/.venv/Scripts/python scripts/apply_migrations.py                   # needs SUPABASE_DB_URL in .env
DEMO_STAFF_PASSWORD='...' backend/.venv/Scripts/python scripts/seed_staff.py
```

Policy engine (standard Baroda Personal Loan, demo parameters) and the Gate B review sheet:

```bash
cd backend && .venv/Scripts/python -m pytest tests -q     # includes the engine run over all 1,000 synthetic customers
backend/.venv/Scripts/python scripts/generate_parameter_sheet.py
```

Run the API locally and check the enquiry flow against the live services (needs an active rule set):

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload     # http://localhost:8000/docs
backend/.venv/Scripts/python scripts/activate_rule_set.py --approver branch.manager@demo.example.com --reason "..."   # a human approval
backend/.venv/Scripts/python scripts/live_check_enquiry.py
backend/.venv/Scripts/python scripts/setup_storage.py        # private bucket for uploaded documents (once)
backend/.venv/Scripts/python scripts/live_check_intake.py
backend/.venv/Scripts/python scripts/setup_ocr_tables.py       # lh_raw tables for page text and AI output (once)
backend/.venv/Scripts/python scripts/load_prepared_demo_text.py   # PREPARED_DEMO_TEXT for degraded demo documents (owner)
backend/.venv/Scripts/python scripts/import_ocr_results.py results.jsonl   # a Kaggle Unlimited-OCR batch, if one was run
backend/.venv/Scripts/python scripts/live_check_documents.py
```

## Secrets

Keys live only in a local `.env` or in the host's secret settings. Never commit credentials, API keys, service-account files, customer data or generated datasets.

Planning documents are in `docs/`.
