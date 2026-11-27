# Baroda Lending Desk: Agentic AI Loan Approval Demo

A presales demonstration of how AI agents can speed up Personal Loan application and assessment while a human keeps the final decision. It is a demo only: all data is synthetic, interest rates and limits are illustrative `DEMO_ASSUMPTION` values, and nothing here is a loan offer or Bank of Baroda's actual policy.

**Product:** Standard Baroda Personal Loan. **Roles:** Loan Officer, Assistant Manager, Branch Manager (demo permissions). All three can take a customer from enquiry to a forwarded application; only an Assistant Manager or Branch Manager decides or releases money, and never on a file they processed (four-eyes). An Assistant Manager can refer a file to the Branch Manager for a change of terms. **AI recommends; a person decides.**

**KYC and credit:** existing customers' verified KYC (with SAMPLE / DEMO account-opening documents) is reused, not asked for again; an out-of-date KYC is refreshed from the verified loan documents, and a new-to-bank customer's KYC record is created from them, with every version kept. Credit data is a DEMO / SIMULATED profile per PAN, fixed for the calendar month. Real documents can be uploaded for testing (real PAN format accepted, Aadhaar only masked).

**Cost:** the project runs entirely on free tiers (no billing enabled anywhere).

**Live demo:** https://baroda-lending-desk.vercel.app (one-click demo roles on the sign-in page; the free backend can take about a minute to wake).

## Layout

```
frontend/   Vite, React, TypeScript, Tailwind (desktop and laptop only)
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
cp .env.example .env.local      # VITE_API_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_DEMO_PASSWORD; never committed
npm install
npm test                        # unit tests (node --test)
npm run dev                     # http://localhost:3000 (the backend's default FRONTEND_ORIGIN)
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

The 1,000 synthetic customers are coastal Karnataka people (mostly Tulu surnames, some Konkani) at Mangaluru, Udupi, coastal and Bengaluru branches; `data/customers_1000.csv` lists them all. All identifiers are synthetic (customer IDs `BOB-C0001`…, PANs `SYNP#######X`, Aadhaar last 4 digits only, emails `@example.invalid`). Ground-truth tables are kept in a separate BigQuery dataset that the application identities cannot read (`scripts/setup_bq_access.py`, `scripts/test_truth_isolation.py`).

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
backend/.venv/Scripts/python scripts/live_check_documents.py
backend/.venv/Scripts/python scripts/ingest_policy_sources.py   # policy knowledge base: official sources into BigQuery (owner)
backend/.venv/Scripts/python scripts/live_check_assessment.py    # dry run of the assessment graph, one customer per segment (writes nothing)
backend/.venv/Scripts/python scripts/live_check_review.py        # review and decision endpoints and guards (changes nothing)
backend/.venv/Scripts/python scripts/live_check_sanction.py      # sanction pack, private storage and one Resend test email (changes no application)
backend/.venv/Scripts/python scripts/live_check_disbursement.py  # simulated disbursement guards (rolled back) and one confirmation email
backend/.venv/Scripts/python scripts/setup_policy_monitor.py     # policy facts table, knowledge-base views, app-identity grants (once)
backend/.venv/Scripts/python scripts/run_policy_monitor.py       # one policy monitor run, as the deployed backend runs it for GitHub Actions
backend/.venv/Scripts/python scripts/setup_analytics.py          # lh_raw.events, KPI views, app-identity grant (once)
backend/.venv/Scripts/python scripts/live_check_analytics.py     # flush the analytics outbox to BigQuery and read the KPIs
backend/.venv/Scripts/python scripts/reset_demo.py --dry-run     # what a demo-data reset would clear (then --confirm <project-ref>)
backend/.venv/Scripts/python scripts/make_samples.py --monitor   # regenerate docs/samples
```

Demo data (owner tools, synthetic only):

```bash
backend/.venv/Scripts/python scripts/seed_kyc.py                  # account-opening KYC records and SAMPLE / DEMO PDFs for the existing customers
backend/.venv/Scripts/python scripts/seed_demo.py --plan          # ~100 enquiries / 60 applications driven through the real app (then: --workers 5)
backend/.venv/Scripts/python scripts/backdate_demo.py --days 24 --dry-run   # spread the seeded history over past days (then --confirm <project-ref>)
backend/.venv/Scripts/python scripts/seed_demo.py --upload-source # once: documents for the daily job (.github/workflows/demo-daily.yml)
```

## Secrets

Keys live only in a local `.env` or in the host's secret settings. Never commit credentials, API keys, service-account files, customer data or generated datasets.

## Documentation

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System context, lifecycle swimlane, document and assessment graphs, the policy monitor |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | User stories, permissions matrix, status model, non-functional requirements |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Operational ERD and database guards, lakehouse layer map |
| [docs/RESPONSIBLE_AI.md](docs/RESPONSIBLE_AI.md) | What the AI may and may not do, human oversight, data handling, known limits |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | One walkthrough per role and the 5-minute recording plan |
| [docs/RESEARCH_NOTE.md](docs/RESEARCH_NOTE.md) | Research note: problem, RBI rules, Bank of Baroda's figures against the demo's, approach, recommendations |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Free-tier deployment (Render, GitHub Actions, Resend webhook), demo reset, before going public |
| [docs/GATE_B_PARAMETER_SHEET.md](docs/GATE_B_PARAMETER_SHEET.md) | Every demo parameter with its confidence label |
| [docs/samples/](docs/samples/) | Sample sanction letter, KFS, EMI schedule, both emails, and a policy monitor run (`scripts/make_samples.py`) |
| [docs/PLAN.md](docs/PLAN.md) | The full plan and what each phase delivered |

