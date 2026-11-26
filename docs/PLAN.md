# Agentic AI Loan Approval Demo: Consolidated Implementation Plan

Institution: Bank of Baroda (demo). Product: Standard Baroda Personal Loan.
Status: **v10. Phase 0 complete. Gate A decisions confirmed and the six corrections implemented and validated; Phases 0, 1 (built locally) and 4 are complete; Phase 3 is next. No billing enabled, nothing purchased. Nothing pushed to GitHub.**

## 0. Change log

v5 records these decisions:

1. **Billing:** no billing account is created, enabled or linked, and no project with billing is used. A "billing canary" check at Phase 0 proves it (section 1).
2. **No Hugging Face.** No Spaces, no ZeroGPU. Kaggle's free GPU is an **optional** route for running the official OCR in batches and is never a dependency of the application.
3. **Embedded-text extraction is allowed** for born-digital RBI and Bank of Baroda PDFs when live OCR is unavailable. It is always labelled `EMBEDDED_TEXT`, never presented as OCR, and keeps source document and page references (section 9).
4. **Official `baidu/Unlimited-OCR` only.** No community forks, quantized builds or alternative OCR engines.
5. **Gate A:** the full dataset schema is in `docs/GATE_A_DATASET_SCHEMA.md`. Phase 4 waits for your approval.

v4 applied these corrections to the previously agreed plan. Scope, features and phase order are otherwise unchanged.

1. **Budget is strictly ₹0** (section 1). Every service is checked against its free tier. Features that cannot be delivered free get a free alternative, never silent removal.
2. **BigQuery is the lakehouse** with raw, curated and gold layers (section 3 and 12). Supabase handles auth, workflow state, roles and audit.
3. **Thin-file and NTC assessment is evidence-based** (section 7). Thin history alone never forces `REFER_FOR_FURTHER_REVIEW` or `POTENTIALLY_INELIGIBLE`, and missing history is never a failed score rule.
4. **Configuration:** public GitHub repo, Gemini Developer API (`GEMINI_API_KEY`, no Vertex AI), Resend (`RESEND_API_KEY`, no Gmail SMTP), secrets only in local `.env` files or deployment secrets.
5. **OCR stays Baidu Unlimited-OCR**, with a zero-cost feasibility gate and a manual-review / prepared-demo-data fallback. No other OCR engine is substituted.
6. **GitHub Actions monitoring stays**, as scheduler and orchestrator only.
7. **Earlier agreed items kept:** Branch Manager policy activation as a configurable demo permission, three roles, AI never decides, three bureau states, `DEMO_ASSUMPTION` labelling, standard Personal Loan only (Digital Personal Loan parked).

## 1. Zero-cost constraint

Hard rules:

- Only free services, free tiers and open-source software. No credits, subscriptions, API plans, GPU rental, hosting upgrades or domains.
- **No billing account is created or linked, and none is enabled on any project. No project that has billing is used.** No free trial or promotional credit is assumed.
- **Billing canary (Phase 0):** the BigQuery project must be an unbilled sandbox. A harmless unsupported statement (a DML statement) is run once. If it succeeds, the project has billing, so I stop and do not proceed. The expected result is a `billingNotEnabled` error. The Gemini key's project cannot be probed from code, so you confirm in AI Studio that it shows the free tier.
- No call or resource that could incur a charge. Each service's free tier was checked against its official documentation (section 18). Where a limit could not be confirmed, it is marked and verified at Phase 0 by observation only, without spending.
- **Safety nets in code:**
  - Per-service daily budget counters set below the free limits (Gemini calls, Resend sends, Supabase storage).
  - BigQuery queries carry `maximum_bytes_billed`.
  - A hard stop (not a retry) when a quota response is received.
  - A startup check that refuses to run paid-capable calls unless the free tier is confirmed in config.
- No custom domain. Provider subdomains are used (`*.vercel.app`, `*.onrender.com`).
- The whole system also runs **locally at zero cost**. Hosting a live link is the only part that depends on third-party free tiers.

## 2. Scope and principles

- **Repository:** `https://github.com/fbivinay/BOB-Agentic-Loan-Approval-Demo` (public, empty, default branch `main`). The local project folder will be connected to it at Phase 0, with the first push only after you say so.
- **Product:** Standard Baroda Personal Loan. Digital Personal Loan is parked and the engine is multi-scheme-ready.
- **Users:** internal staff app, with no applicant portal.
- **Platform support (decided): laptop and desktop only.** Phones and tablets are blocked, including when they request the desktop site, for the staff app and the public pages alike. There is no mobile or tablet layout.
  - **Gate:** a small inline script in the root layout (same approach as the reference app's `index.html`) sets a `data-desktop-only` flag that hides the app and shows "Please open this website on a laptop or desktop". It checks Client Hints mobile, phone and tablet user agents, iPadOS in desktop mode (Macintosh user agent with touch points), touch-only pointers, and Android or phone-sized touch screens even with a stylus or mouse. Windows laptops with a touch screen and a mouse or trackpad still pass.
  - **Server side:** Next.js middleware also checks the `Sec-CH-UA-Mobile` header and the user agent, so a blocked device receives the message page instead of the app.
  - **Window size:** a minimum width of about 1024 CSS px. Narrower desktop windows see a "widen this window" message.
  - **Limit:** this is a courtesy gate, not a security control, and a determined user can spoof the signals.
- **Stack:**
  - Next.js, TypeScript and Tailwind (Vercel Hobby).
  - FastAPI (Render free web service).
  - Supabase Free (Auth, Postgres, Storage).
  - BigQuery sandbox (lakehouse).
  - Gemini Developer API.
  - LangGraph.
  - Baidu Unlimited-OCR.
  - Resend.
  - GitHub Actions.
- **Data:** synthetic only, no real PII. All Bank of Baroda figures that are not verified from an official document are labelled `DEMO_ASSUMPTION`.
- **Jobs:** one generic `jobs` table, drained by a job runner inside the backend. No Pub/Sub, no Kubernetes, no microservices.

## 3. Architecture and data flow

```
                         GitHub Actions (public repo, free)
   cron: monitoring, job-drain trigger, keepalive pings. Never runs OCR.
        │ fetch RBI / BoB sources, hash, upload ──► POST /internal/policy/ingest
        ▼
Next.js staff app (Vercel Hobby) ──JWT──► FastAPI (Render free)
                                              │ verifies JWT, loads role, checks permission on every call
                 ┌────────────────────────────┼──────────────────────────────┐
                 ▼                            ▼                              ▼
        Supabase (free)               BigQuery sandbox               Job runner (in-process)
   Auth, staff roles, workflow      LAKEHOUSE                        ├─ OCR client ─► Unlimited-OCR host
   state, audit (append-only),      raw ► curated ► gold             │   (free options in section 9)
   decisions, rule-set state,       structured, OCR-derived,         ├─ Gemini Developer API
   email outbox, jobs               policy and external mock data    ├─ Policy engine + comparator (Python)
   Storage: uploaded documents,                                      └─ Resend (email)
   generated PDFs, policy sources
```

**Data flow, per application:**
1. The Loan Officer looks up a customer. The backend reads `gold_customer_360` from BigQuery (accounts, salary credits, existing loans), so nothing is re-asked.
2. Documents upload to private Supabase Storage. An OCR job is queued, and OCR text lands in BigQuery `raw.ocr_pages`, then `curated`.
3. Gemini extracts structured fields from OCR text. They are stored in BigQuery and shown to the Loan Officer for review. Confirmed values drive everything downstream.
4. The assessment reads BigQuery gold tables (customer 360, bureau features, mock KYC) and the active rule set, retrieves policy text, and writes a recommendation and trace to Supabase.
5. A human decision is committed to Supabase together with an `analytics_outbox` row. A batch load job then appends the event to BigQuery `raw.events`, and gold KPIs are rebuilt.
6. Sanction documents are generated, stored in Supabase Storage, and emailed through Resend.

**Rules:**
- The frontend never queries tables. It uses Supabase only to log in. RLS denies direct table access as a second layer.
- Roles come from `staff_profiles`. The backend verifies JWTs and enforces roles and permissions on every endpoint.
- BigQuery and Gemini credentials live only on the backend. The backend and agents use separate BigQuery identities that cannot read the ground-truth dataset (section 5).

## 4. Roles and permissions

Three roles: Loan Officer, Assistant Manager, Branch Manager.

| Action | Loan Officer | Assistant Manager | Branch Manager |
|---|---|---|---|
| Enquiries, intake, documents, discrepancies, forward | ✔ | – | – |
| Approve or decline | ✘ | ✔ | ✔ |
| Take over, refer for reconsideration, adjust terms (demo limits + reason) | ✘ | ✘ | ✔ |
| Disbursement | track | release | release |
| Activate policy rule set | ✘ | ✘ | ✔ (configurable) |

- **Fixed invariants, enforced by DB triggers:** a decision can be created only by a human Assistant Manager or Branch Manager. AI cannot create one.
- **Configurable demo permissions:** policy activation, disbursement release and term-adjustment limits come from a seeded `role_permissions` table. They are labelled **"demo permission, not Bank of Baroda's actual authorization structure"** on every screen and document that shows them.
- Actions and the OCR service act as a `system` service identity. It is not a fourth human role.

## 5. Customer model and synthetic dataset

**Two independent axes:**
- **Relationship:** ETB or NTB.
- **Credit history:** `ESTABLISHED`, `THIN`, `NO_HISTORY` (NTC).

NTB and NTC are not the same category, and thin is not NTC.

**Distribution (1,000 customers):**

| | Established | Thin | No history (NTC) | Total |
|---|---|---|---|---|
| ETB | 440 | 80 | 80 | 600 |
| NTB | 250 | 50 | 100 | 400 |
| Total | 690 | 130 | 180 | 1,000 |

**Tables.** The authoritative field-level definition is `docs/GATE_A_DATASET_SCHEMA.md` (implemented in `data/generator/schema.py`):
- **`lh_raw`, application-facing:** `branches`, `customers` (`bank_relationship`, `is_staff`, residency; **no credit-history column**), `employment`, `accounts`, `account_holders`, `transactions`, `existing_loans`, `loan_repayments`, `bureau_profiles` (history status plus nullable score), `bureau_tradelines`, `historical_applications`, `ext_kyc_mock`.
- **`lh_truth`, ground truth, inaccessible to application and agent identities:** `customer_ground_truth`, `external_statement_lines`, `documents_truth`.
- The `dataset_versions` table is replaced by a deterministic `manifest.json` (content) and a separate `run_info.json` (volatile execution metadata).

**Edge cases:**
- Thin: one 8-month tradeline, credit card only, closed loan only, a DPD event within a thin file, a recent secured card, an ETB whose only history is a Bank of Baroda loan.
- Other: irregular salary, bounced EMIs, name variants, income mismatch, age near limit, borderline FOIR, job under 1 year, government salary accounts, duplicate mobiles, dormant accounts, metro vs rural branches, `is_staff` and `is_nri` flags.

**Canonical customer ID (decided):** `BOB-C0001` to `BOB-C1000` is the only customer ID format, in every schema, file, loader and validation. It is a demo identifier, not a real Bank of Baroda customer number, and an NTB person's ID does not imply a banking relationship (`bank_relationship` does). Validation fails on any other format, including the old `CUST-` form.

**Reproducibility:**
- One generator with a fixed seed. It uses an integer-only PRNG (SplitMix64, per-customer streams), money in whole rupees, rates in basis points, EMI maths in `Decimal`, and no clock, environment or set-order dependence.
- Output is canonical CSV (sorted by primary key, fixed column order, UTF-8, LF) plus `manifest.json` with per-file SHA-256, row counts and one canonical hash. All timestamps inside the dataset (for example the mock KYC `checked_at`) are derived from the as-of date and the seed.
- **Deterministic content versus runtime metadata:** `run_info.json` (when it ran, Python and platform) is volatile, sits outside every checksum, and is never loaded as data. The manifest holds no runtime metadata, and a check enforces it.
- Tests prove identical checksums for the same seed in one process and across separate processes with different hash seeds, and a different seed changes the data.
- One loader writes to BigQuery using **load jobs** (the sandbox has no streaming or DML). Reloads replace tables. Any load-time metadata (when, by whom) goes to a separate `load_runs` record, not into the dataset.
- Generated data files are gitignored and never committed. Only the generator, schema and tests are.
- Size budget: about 32 MB on disk. The sandbox's 10 GiB storage quota is lifetime and is not refunded on deletion (section 18), so reloads are kept deliberate and counted.

**Thin-file score availability (exact):** of the 130 thin-file customers, **78 have a bureau score (ETB 48, NTB 30) and 52 have a null score (ETB 32, NTB 20)**. The generator, the schema document and the validation suite all assert these exact counts.

**Synthetic identifiers:**
- PAN-like values use the form `SYNP` + 7 digits + 1 letter (12 characters). A real PAN has exactly 10 characters, so these can never be mistaken for or submitted as real PANs. The app's own PAN validation accepts only this form and rejects the real format. Synthetic PANs are never sent to any government, banking, KYC or external verification service. The mock KYC is a local table.
- Accounts are `ACC-000001`, IFSC codes are `BARB0SYN###`, emails use the reserved `@example.invalid` domain, and only the last four Aadhaar digits exist. Every sample document carries a "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT" notice, and the manifest and BigQuery table descriptions state that all data is synthetic.
- **Known limit:** names are random and could coincide with real ones. Mobile numbers are realistic 10-digit Indian-style numbers with a leading digit 6-9 (decided). They are random and could coincide with real numbers, so nothing is ever sent to them.

**Repayment and external-bank consistency:**
- Every Bank of Baroda loan's `repayment_account_id` is an active Bank of Baroda account whose PRIMARY holder is the loan's own ETB customer and which existed before the loan started. EMI debits with a `loan_id` post only to that account, and posted debits equal paid repayments exactly.
- Another lender's EMI paid from a Bank of Baroda account carries a `tradeline_id` (never a `loan_id`) that points to a non-Bank-of-Baroda tradeline of the same customer.
- NTB repayment evidence exists only as external-bank statement lines on non-Bank-of-Baroda accounts held by the customer. They reference non-Bank-of-Baroda tradelines, and are never labelled as Bank of Baroda accounts. NTB customers have no Bank of Baroda loans.

**Ground-truth isolation (enforced in BigQuery, not by convention):** three service-account identities (no keys): `sa-app-runtime` (backend), `sa-agent-runtime` (agents) and `sa-truth-eval` (controlled generation and evaluation). The runtime identities hold only `roles/bigquery.jobUser` at project level and are listed as READER on `lh_raw` only. `lh_truth` lists only the evaluation identity and the project owner. `scripts/test_truth_isolation.py` proves this by acting as each identity. A code test also fails if any application code references ground-truth tables. The project owner retains administrative access to `lh_truth` by ownership (documented, by design); the runtime identities must stay unable to access it.

## 6. Policy

- One scheme file, `baroda_personal_loan`, with rules keyed by the six relationship × history segments. Where no difference is known, a segment uses common parameters inside the same scheme only.
- **Active figures have exactly two states:**
  - `VERIFIED`: read directly from an official document by us.
  - `DEMO_ASSUMPTION`: a deliberate demo value, never attributed to Bank of Baroda, and labelled in the UI, the sanction letter, the KFS and the exports.
- **Figures found only in unverified search excerpts are not active.** They are stored as `UNVERIFIED_CANDIDATE` in the knowledge base. The engine rejects any rule set that contains anything other than the two states above.
- **Exclusions (staff, NRI):** reviewed as flags. Until `VERIFIED`, they return `NEEDS_REVIEW`. An exclusion rule can produce `POTENTIALLY_INELIGIBLE` only when `VERIFIED`, and even then it is only a recommendation.
- Numeric demo limits (for example FOIR) may drive `POTENTIALLY_INELIGIBLE`, and each such outcome says "per demo assumption, not Bank of Baroda policy".
- Active rules live in versioned `policy_rule_sets` in Supabase (state and approvals). Each application records the `policy_version_id` it was evaluated under.
- **Gate B:** review of the demo-parameter sheet (every parameter, its label, and its candidate source).

## 7. Credit assessment by history state

All thresholds are `DEMO_ASSUMPTION`. The engine is deterministic Python.

**Principle:** history status alone never decides. The engine evaluates the evidence that exists, and refers only when evidence is insufficient, inconsistent, or needs human judgement.

**Evidence checks (all segments):**
- **Repayment behaviour** on any available tradelines (late payments, DPD, write-offs).
- **Tradeline facts:** count, age, and amount outstanding versus sanctioned.
- **Income:** verified from salary credits (enough months, regularity) and from documents.
- **Bank statement:** consistency with declared income, balances, bounced debits.
- **FOIR:** obligations from bank and bureau data against verified income.
- **Document completeness and consistency** (no unresolved discrepancies).

**Established:** score-based rules plus the evidence checks.

**Thin:**
- The score rule applies only if a score exists. If there is no score, that rule is **skipped, not failed**.
- Repayment behaviour on the available tradelines, income, FOIR and the bank statement carry the assessment.
- Strong, consistent evidence can yield `RECOMMENDED_FOR_APPROVAL`.
- Adverse behaviour or a failed hard limit (for example FOIR) can yield `POTENTIALLY_INELIGIBLE`.
- The recommendation is `REFER_FOR_FURTHER_REVIEW` **only** when evidence is insufficient (for example too few salary months), inconsistent (for example income mismatch), or genuinely needs judgement.

**NTC (no history):**
- The score rule is **skipped, not failed**. Missing history is never treated as a failed credit-score rule, and NTC is never rejected for lacking history.
- Assessment rests on verified income, the bank statement, FOIR and published conditions.
- The same three outcomes apply on the evidence, with the same referral conditions as thin.

The reasoning behind each outcome is stored with rule-by-rule results and shown to the reviewer.

## 8. Official policy monitoring (GitHub Actions)

GitHub Actions is the scheduler and orchestrator only. It never runs the OCR model and no GPU is assumed.

**Sources:**
- **RBI:** notifications and press-release RSS, filtered by lending keywords, plus Master Directions added to a watchlist.
- **Bank of Baroda:** a YAML watchlist (standard personal loan, retail rates, charges, MCLR). Pages are hashed as cleaned text, and PDF links on them are followed.

**Free-tier restrictions (documented, not removed):**
- **Frequency:** daily by default, adjustable to weekly. Scheduled runs can be delayed under load, run only on the default branch, and are **disabled after 60 days without repository activity** in a public repo.
- **Coverage:** starts with one RBI feed and three Bank of Baroda pages, and widens as run time allows.
- **PDF text extraction** is limited by the free OCR options (section 9). Born-digital policy PDFs use embedded-text extraction when live OCR is unavailable (labelled `EMBEDDED_TEXT`, with source document and page references). Scanned PDFs queue for OCR (`AWAITING_OCR`) and are processed by an optional Kaggle batch, or stay queued. HTML pages need no OCR.

**Incremental:** conditional GET (`ETag` / `Last-Modified`), then a content hash against the stored version. Unchanged means no download, OCR or Gemini call.

**Failure visibility:** each run writes to the Actions run summary and posts a run record to `monitor_runs` (visible in an admin health screen). A failure also opens or updates a labelled GitHub Issue, with no secrets in the text. Failure notifications go to the user who last edited the cron line. A "stale monitoring" alert fires if no run succeeded within twice the interval. The 60-day disablement is mitigated by committing change reports and a compact run log (real repository activity), plus a documented `gh workflow enable` re-enable.

**Three separate comparison layers:**

| Layer | Question | Method |
|---|---|---|
| A. Document change | Did the source change? | Hash, page-level text diff, publication date (RSS date, PDF metadata or "unknown") |
| B. Extracted-fact change | Did a stated fact change? | Gemini extracts a fixed `PolicyFacts` schema with page refs and quotes. A deterministic diff compares old and new |
| C. Rule impact | Does it affect an actual rule or calculation? | An explicit human-maintained `fact→rule` map compares facts to the active rule set. Gemini does not decide impact |

Classifications: `DOC_ONLY`, `FACT_CHANGED_NO_RULE_IMPACT`, `RULE_IMPACT`, `COMPLIANCE_ITEM` (RBI requirements that don't map to a rule, such as KFS content, go to a human checklist), `VERIFICATION_CANDIDATE` (an official source confirms a `DEMO_ASSUMPTION` value).

**Automatic:** documents, extracted text, facts, change reports, and the search index.
**Never automatic:** any active rule or calculation. Layer C creates proposals only.

**Activation:**
1. The authorised role (Branch Manager by default, per section 4) reviews the proposal.
2. They draft a candidate rule set, validated against the schema.
3. The golden-case tests run automatically and the result is shown.
4. They approve with a recorded reason.
5. The new version becomes `ACTIVE`, and in-flight applications keep their old version.

Change reports record the source URL, publication date, version, changed sections and pages, fact diffs with quotes, impacted rules, and classification.

## 9. OCR: Baidu Unlimited-OCR at zero cost

**Fixed:** `https://github.com/baidu/Unlimited-OCR` is the only OCR engine. There is **no silent switch** to another engine, and no Tesseract or generic OCR library.

**Findings (README and model card):** ~3B parameters, tested on an A800-80GB, no stated minimum VRAM, NVIDIA/CUDA only, no documented CPU path, README pins torch 2.13.0 and Python 3.10, page-level output with a `<PAGE>` marker per page, no bounding boxes documented, languages not stated. A third-party estimate is ~12 GB VRAM minimum.

**Your machine:** no NVIDIA GPU (AMD graphics only) and ~15 GB RAM, so live GPU OCR cannot run locally.

**Excluded:** Cloud Run GPU and any rented GPU (billing), and everything on Hugging Face (Spaces, ZeroGPU), by your decision. Only the official `baidu/Unlimited-OCR` implementation is used. No community forks, quantized builds or alternative engines.

**Free options for the same engine:**

| Option | What it gives | Limits |
|---|---|---|
| A. Kaggle free GPU (**optional**, offline batch) | Real Unlimited-OCR output for the demo documents and queued scanned PDFs. A notebook in the repo runs the official implementation on a folder of documents and writes a results file that is imported by hand | Manual, not a live service. ~30 GPU hours per week (unconfirmed), 12 h sessions, availability varies, internet access may need phone verification. **Never a dependency of the application** |
| B. CPU run of the unmodified official code on your machine | A few pages for testing, only if it runs without patching | No documented CPU path. If it needs code changes it is skipped, because modifying the official implementation is not allowed |

If Kaggle is unavailable, the demo uses precomputed OCR results or manual review (below). Nothing else substitutes.

**Gate C (zero-cost OCR feasibility):** recorded in `docs/GATE_C_OCR_FEASIBILITY.md`. The one-time weights download from the official ModelScope repository (verified against the official GitHub README) is allowed for a Kaggle-only, optional run, using `ocr/kaggle/run_unlimited_ocr.py`. It has not been run yet, and runs once demo documents exist. Nothing is rented or enabled. If the run fails, the demo uses embedded text, manual review and `PREPARED_DEMO_TEXT`.

**Fallbacks (none substitutes another OCR engine):**
- **Prepared demo data:** the demo documents are run once through the real Unlimited-OCR (option A or B). The results are stored and labelled `PRECOMPUTED_UNLIMITED_OCR`.
- **Manual review:** if live OCR is unavailable, the job shows `AWAITING_OCR`, retries, then moves to `NEEDS_MANUAL_REVIEW`, where the Loan Officer keys values from the document image (recorded as `source=MANUAL`).
- **Embedded-text extraction (born-digital RBI and Bank of Baroda PDFs only):** when live OCR is unavailable, text is read from the PDF's own text layer with a PDF text library (decided in Phase 7). This is **not OCR** and is never labelled as OCR.
  - Every page row carries `extraction_method`: `EMBEDDED_TEXT`, `UNLIMITED_OCR`, `PRECOMPUTED_UNLIMITED_OCR`, `MANUAL` or `PREPARED_DEMO_TEXT`.
  - Every row keeps the source document, its version and the page number.
  - Pages with no usable text layer (scans) are never passed off as extracted. They go to the OCR queue.
  - Reports and the UI show the method next to each extracted fact.
- **`PREPARED_DEMO_TEXT` (approved):** generated demonstration values for degraded-looking demo documents, used only to show the downstream flow. It is always displayed as "Prepared demo data — not OCR", is never represented as extracted or verified content, and never counts as verified evidence in an assessment.
- No community fork, quantized build or alternative engine is used, and no silent substitution happens.

**Integration:**
1. Upload goes to Supabase Storage and an `ocr_document` job is queued.
2. PDFs render at 300 DPI and split into windows of ≤10 pages. Results are stitched per `<PAGE>`.
3. `ocr_pages` stores text per page, `extraction_method`, engine version, timing and the source document and page number (landing in BigQuery `raw`).
4. Gemini returns a Pydantic-validated extraction with page refs and quoted snippets, cached by document hash so retries don't repeat calls.
5. The Loan Officer reviews page image, OCR text and fields side by side, edits or confirms, and every correction is stored and audited. Downstream steps use confirmed values.

## 10. Agent workflow and AI boundaries

**Document graph:** OCR text, then Gemini extraction, then a deterministic verifier produces discrepancies. The Loan Officer resolves each with a note and attests "documents verified".

**Assessment graph:**
1. Read customer 360, bureau features and mock KYC from BigQuery gold.
2. Policy engine returns rule-by-rule PASS / FAIL / SKIPPED / NEEDS_REVIEW, each with a confidence label (section 7).
3. Policy retrieval from the knowledge base, with citations.
4. Gemini writes the summary and explanation, and cannot change numbers.
5. Output: `RECOMMENDED_FOR_APPROVAL`, `REFER_FOR_FURTHER_REVIEW`, or `POTENTIALLY_INELIGIBLE`.

**AI can:** extract and understand documents, find missing information and discrepancies, retrieve policy text, run deterministic calculations, and recommend.

**AI cannot:** approve or reject, change eligibility calculations or rules, modify approved terms, or trigger sanction or disbursement.

**Enforcement:**
- DB triggers on `decisions`, approved terms and `policy_rule_sets` activation.
- Sanction generation requires a human `APPROVED` decision, and disbursement requires a human release.
- Agents have no write path to application status, terms, decisions or rule sets.
- Every decision stores a snapshot of the AI recommendation. A difference requires a written reason and writes a `DECISION_OVERRIDE` audit event. All human decisions are recorded in the audit trail.
- Negative tests prove each "AI cannot" item fails.

## 11. Personal Loan lifecycle

1. **Draft / Loan Enquiry:**
   - The Loan Officer searches by ID, mobile or PAN. If found, the fields prefill, and otherwise they register a new enquiry.
   - It shows an indicative amount, rate, tenure and a category-specific missing-documents list, all labelled indicative.
   - Credit history is staff-declared. There is no bureau pull, no OCR, no AI, and no approval workflow at this stage.
   - It can be saved as a draft and resumed later.
2. **Proceed:** an explicit button converts the enquiry into a formal application.
3. **Intake** (including NTB registration and KYC) and document upload.
4. **OCR, extraction, review and correction, discrepancy resolution, attestation.**
5. **Assessment graph.**
6. **Forward** by the Loan Officer.
7. **Review and decision** by an Assistant Manager or Branch Manager. The Branch Manager may also refer, take over, or adjust terms within demo limits with a reason.
8. **Sanction pack:** sanction letter, Key Facts Statement and EMI schedule, all labelled with demo parameters.
9. **Approval email** with all three PDFs.
10. **Simulated disbursement** with a banner and a fake reference. No real banking transaction.
11. **Separate disbursement confirmation email.**

**Statuses:** `ENQUIRY` → `INTAKE` → `DOCS_PENDING` → `DISCREPANCY` → `DOCS_VERIFIED` → `AI_ASSESSED` → `FORWARDED` → `UNDER_REVIEW` → `APPROVED` / `DECLINED` / `RECONSIDERATION` → `SANCTIONED` → `DISBURSED`.

A decline records the reason and sends no sanction pack. A referral returns the application to the review queue with a note.

## 12. Data and storage responsibilities

**Supabase (auth, workflow, roles, audit):**
- `staff_profiles`, `role_permissions`.
- `enquiries`, `applications`, `application_terms` (versioned), `documents` (metadata and Storage paths).
- `extraction_corrections`, `discrepancies`.
- `agent_runs`, `decisions`, `term_changes`.
- `policy_rule_sets`, `policy_change_proposals`, `policy_activation_log`, `monitor_runs`.
- `generated_documents`, `email_outbox`, `email_events`, `disbursements`.
- `jobs`, `analytics_outbox`.
- `audit_events` (append-only via DB trigger that rejects UPDATE and DELETE).
- **Storage:** private buckets for uploaded documents, generated PDFs and policy source files. Access only through short-lived signed URLs after a role check.

**BigQuery lakehouse (three datasets):**

| Layer | Contents |
|---|---|
| `lh_raw` | Generator output as loaded (customers, accounts, transactions, loans, repayments, bureau, tradelines, historical applications, mock KYC). `ocr_pages`. Policy source snapshots and metadata. Gemini extraction outputs. `events` (decision events) |
| `lh_curated` | Typed, deduplicated, conformed tables. Derived credit features (salary regularity, obligations, repayment flags). Policy chunks, facts and fact changes |
| `lh_gold` | `customer_360`, `credit_features`, `policy_current`, portfolio and decision KPIs for the analytics dashboard |

- Layers are built with load jobs and `CREATE OR REPLACE TABLE … AS SELECT`. Phase 0 confirms that CTAS works in the sandbox. If it doesn't, the transformation runs in Python and the result is loaded.
- The sandbox has **no DML and no streaming**, so appends use load jobs (`WRITE_APPEND`).
- **Sync:** a decision commits to Supabase together with an `analytics_outbox` row. A batch load appends it to BigQuery with the event ID for idempotency, with retry and backoff. Rows that keep failing are marked `FAILED` in an admin view, with a manual retry.
- Tables expire after 60 days in the sandbox, so the dataset and loads are rebuildable from the generator, manifest and job history.

**GCS is not used.** It needs a billing account (section 18). Supabase Storage replaces it.

## 13. Email (Resend)

- Resend API with `RESEND_API_KEY`. No Gmail SMTP.
- **Approval email:** sanction letter, KFS and EMI schedule attached.
- **Disbursement confirmation:** a separate email.
- **Outbox:** `email_outbox` and `email_events`, with status `QUEUED` → `SENDING` → `SENT` → `DELIVERED` / `DELAYED` / `BOUNCED` / `COMPLAINED` / `FAILED`.
- **Idempotency:** the outbox ID is sent as the `Idempotency-Key`, so retries never duplicate (keys last 24 hours).
- **Retry:** exponential backoff on 429, 5xx and network errors, up to N attempts. Permanent 4xx errors go straight to `FAILED`. Every attempt, status and error is logged without the API key. A bounce is shown to the Loan Officer, who can correct the address and resend (audited).
- **Delivery status:** webhooks to `/webhooks/resend` with signature verification (the free plan allows one endpoint). Local development polls the email status instead.
- **Demo routing:** synthetic customer addresses never receive mail. All demo mail is redirected to your own test inbox or to Resend's test addresses (`delivered@`, `bounced@`, `complained@resend.dev`), which also exercise failure handling.
- **Free limits:** 3,000 per month and 100 per day. A daily counter stops sending at 80.
- Email failure never blocks sanction. The status shows in the UI.

## 14. Secrets and public-repo hygiene

- The repo is **public**. Never commit credentials, API keys, service-account files, customer data, generated datasets or `.env` files. `.gitignore` already covers them.
- **Local:** `.env`, with an `.env.example` listing names only.
- **Deployment:** Render environment secrets (backend), Vercel environment variables (public values only), GitHub Actions secrets (the internal ingest token).
- **Variables:** `GEMINI_API_KEY`, `GEMINI_MODEL`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_REDIRECT_TO`, `RESEND_WEBHOOK_SECRET`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` (public by design), `SUPABASE_SERVICE_ROLE_KEY` (backend only), `GCP_PROJECT`, `BQ_*` dataset names, `BQ_SERVICE_ACCOUNT_JSON` (backend secret), `INTERNAL_API_TOKEN`.
- **Repository protection:** GitHub secret scanning and push protection. Workflows use minimal `permissions`, avoid `pull_request_target`, and pin third-party actions. Fork PRs receive no secrets.
- **Public data only in the repo:** policy documents and OCR text stay in Supabase Storage and BigQuery. The repo holds change-report summaries with short quotes, URLs and hashes.
- The Gemini free tier may use submitted content to improve Google products, so **only synthetic and public documents go to Gemini**. No real data is used anywhere.
- I will never ask for keys in chat. You add them to `.env` later.

**Repository authorship (mandatory):**
- All commits are authored and committed under your existing GitHub identity, `fbivinay`. The repo-local git identity is set to the same name and email as your existing commits in the reference repository.
- No AI tool or assistant identity appears as a GitHub collaborator, commit author or committer, or bot identity. No `Co-authored-by` or similar trailers, and no tool-attributed commit message or pull request footers.
- No AI tool is named as a contributor in the README, documentation, project credits or contributor lists. AI-tool working files are gitignored.
- Nothing is pushed to GitHub without your say-so.

## 14A. Frontend design (decided)

**Reference:** your own repo `fbivinay/Odoo-Hackathon-Finals-Textile-Manufacturing` and its live app. The design language is reused and adapted to loan processing. No textile content or branding is copied.

**Reused:**
- App shell: fixed 240 px white sidebar with icons and per-role navigation, with the page as an inset rounded panel that scrolls on its own.
- `PageHead` (mono eyebrow, display title, lede, actions), the brand-ringed figures panel with `Figure` cards (count-up, optionally clickable as filters), glass `card`, `DataTable` (sticky headers, sorting, skeleton and empty states), `StatusPill`, the History timeline card, Recharts bars with a line, and print-styled document views.
- Two-half sign-in (gradient brand panel with feature rows, form card on the ground colour) with **one-click demo role buttons** for Loan Officer, Assistant Manager and Branch Manager, as in the reference.
- Rule: neutral tokens everywhere, and colour appears only as brand or as status (good, watch, problem, neutral).
- Stack carried over: Tailwind v4, shadcn/ui (Radix), Geist, Recharts, TanStack Table, lucide-react, sonner. Ported from React Router to the Next.js App Router, with Supabase Auth in place of the custom session. `jspdf` is dropped because sanction documents are generated server side.

**Changed:**
- **Brand colour (decided): Baroda-inspired burnt orange, no official logo.** Proposed token `#C2410C` (about 5.2:1 contrast on white, so white-on-orange buttons and orange text meet 4.5:1), with a pale tint, a deep shade and a soft shade derived from it. Wordmark: a neutral "Baroda Lending Desk" with a "Demo" tag. Final values are verified for contrast at build time.
- **Desktop and laptop only** (section 2). No mobile or tablet layouts.
- **Motion:** shorter and quieter than the reference, with a skippable splash and `prefers-reduced-motion` respected.
- **Not included:** the reference's Gemini chat assistant (not required).

**Loan status colours:**

| Meaning | Statuses |
|---|---|
| good | Approved, sanctioned, disbursed, `RECOMMENDED_FOR_APPROVAL`, `VERIFIED` |
| watch | Discrepancy, under review, reconsideration, `REFER_FOR_FURTHER_REVIEW` |
| problem | Declined, `POTENTIALLY_INELIGIBLE` |
| neutral | Enquiry, pending, `DEMO_ASSUMPTION` |

AI recommendation pills always read "AI recommendation, human decides". Each extracted field carries an extraction-method chip (`EMBEDDED_TEXT`, `UNLIMITED_OCR`, `PRECOMPUTED_UNLIMITED_OCR`, `MANUAL`, `PREPARED_DEMO_TEXT`, the last always shown as "Prepared demo data — not OCR").

**Public pages (assignment D2–D4), reachable from sign-in:** `/approach`, `/architecture`, `/about`, `/faq`, `/contact`.

**Staff app pages** (sidebar items per role):
- **Overview:** role-specific queue and figures.
- **Enquiries:** Loan Officer manages them, others view. Includes the indicative calculator and the missing-documents list.
- **Applications:** list with clickable status figures as filters.
- **Review queue:** Assistant Manager and Branch Manager.
- **Policy:** knowledge base, change reports, proposals and rule sets. Read for all, activation by the Branch Manager as a configurable demo permission.
- **Disbursements:** Loan Officer tracks, Assistant Manager and Branch Manager release.
- **Analytics:** BigQuery gold KPIs, Assistant Manager and Branch Manager.
- **System status:** jobs, OCR queue, email, analytics sync and monitoring health, Assistant Manager and Branch Manager.
- **Settings:** profile and a demo-permissions panel.

**Application detail** (`/applications/[id]`):
- A header with status pills, the actions the role may take, and a figures panel (requested, indicative eligible, AI recommendation).
- A History timeline of statuses and audit events.
- Tabs: Summary (customer 360 with source chips), Documents (page image beside OCR text and editable fields with confidence and method), Discrepancies, Assessment (rule-by-rule results, policy citations, agent steps with the data each used), Decision, Sanction pack (print-styled letter, KFS and EMI schedule watermarked "Demo, not a loan offer"), Disbursement (simulated banner), Audit trail.

**Demo logins and the public site:** the one-click demo passwords will sit in a public bundle, so anyone can sign in to the live demo and change its synthetic state. Mitigations already in the plan: synthetic data only, daily budget caps on Gemini and Resend, all email redirected to your test inbox, and simulated disbursement. A local demo-data reset script restores a clean state.

## 15. Testing plan

- **Policy:**
  - Golden cases for each of the six segments.
  - **Thin file with clean repayment and stable income gives `RECOMMENDED_FOR_APPROVAL`.**
  - Thin file with insufficient or inconsistent evidence gives `REFER_FOR_FURTHER_REVIEW`.
  - Thin file with adverse behaviour or a failed hard limit gives `POTENTIALLY_INELIGIBLE`.
  - **NTC with strong evidence is recommended, and a missing score is `SKIPPED`, never `FAIL`.**
  - Staff and NRI return `NEEDS_REVIEW` while unverified.
  - An exclusion can produce `POTENTIALLY_INELIGIBLE` only if `VERIFIED`.
  - A rule set containing an `UNVERIFIED_CANDIDATE` is rejected at load.
- **Dataset:**
  - A 105-check validation suite (`data/validate.py`) plus 13 pytest tests, including negative tests that tamper with the data and require the suite to fail.
  - Segment counts match exactly, including **THIN: 80 ETB and 50 NTB**.
  - All three history states appear under both ETB and NTB.
  - Thin customers have 1–2 tradelines, oldest under 12 months, and **exactly 78 have a score and 52 do not**.
  - No thin customer has zero tradelines, and no NTC has any.
  - Canonical `BOB-C0001` IDs everywhere, and no `CUST-` anywhere.
  - Identical checksums for the same seed, in one process and across processes with different hash seeds.
  - Every PAN is the 12-character synthetic form, unique, and cannot match a real PAN.
  - Repayment accounts are active Bank of Baroda accounts held by the loan customer, EMI debits match repayments exactly, and NTB evidence stays on non-Bank-of-Baroda accounts.
  - Ground-truth isolation: 18 checks acting as each identity (`scripts/test_truth_isolation.py`).
  - After a BigQuery load (later phase), row counts and keys match the manifest and every foreign-key relationship holds.
- **Comparator:** fixtures for a document change only, a fact change with no rule impact, a rule impact, a compliance item, and a `DEMO_ASSUMPTION` verification candidate.
- **AI boundaries:** negative tests for every "AI cannot" item, plus authorization tests for every role and permission.
- **Draft:** no bureau, OCR, agent or workflow rows are written.
- **OCR:** failure, retry and manual-review path, and no silent substitution.
- **Email:** idempotent retry, permanent failure, bounce handling, and the daily cap.
- **Cost guards:** budget counters stop at their caps, and quota responses stop calls.
- **End-to-end:** one smoke test of the full journey.

## 16. Phases and gates

0. **Foundation:** repo scaffold, `.env` handling, connectivity checks (Supabase, BigQuery sandbox, Gemini, Resend test address), the **billing canary**, plus **Gate C: zero-cost OCR feasibility** (optional Kaggle run, no Hugging Face).
1. Auth, operational schema, audit, permissions config, human-only triggers. **Status: built, tested, applied to Supabase and staff seeded (section 16A).**
2. **Gate A:** dataset schema approval. **Gate B:** demo-parameter sheet review.
3. Policy engine (standard scheme, six segments, evidence-based thin/NTC logic, versioned rule sets, confidence checks), EMI and KFS APR. **Status: built and tested (section 16B). Gate B closed 26 Nov 2026 (`docs/GATE_B_PARAMETER_SHEET.md`), with required documents added to the rule set. Activating the rule set in Supabase is a human approval, run by the project owner with `scripts/activate_rule_set.py`.**
4. Dataset generator, BigQuery loader, raw to curated to gold layers, validation. **Status: Gate A approved. Done: generator, schema, 105-check validation, ground-truth isolation, 480 rendered sample documents, and the BigQuery load of `lh_raw` and `lh_truth` (15 tables, load jobs, `scripts/load_bigquery.py`) with 62 post-load checks passing (`scripts/validate_bigquery.py`). Also done: the curated and gold layers (`lh_curated.credit_features`, `lh_gold.customer_360`, built by `scripts/build_layers.py`, 47 independent checks passing). Phase 4 is complete.**
5. Draft enquiry. **Status: backend complete and verified live (section 16C); screens wait for frontend approval.**
6. Intake, NTB registration, document upload to Supabase Storage. **Status: backend complete and verified live (section 16D); screens wait for frontend approval.**
7. Job runner, OCR integration (per Gate C), Gemini extraction, review and correction, manual-review fallback, discrepancies. **Status: backend complete (section 16E); screens wait for frontend approval.**
8. Policy knowledge base (BigQuery curated, manual seed ingest), then the assessment graph with policy retrieval. **Status: backend complete and checked live (section 16F); screens wait for frontend approval.**
9. Review and decision. **Status: backend complete and checked live (section 16G); screens wait for frontend approval.**
10. Sanction pack and Resend emails. **Status: backend complete and checked live (section 16H); screens wait for frontend approval.**
11. Disbursement simulation. **Status: backend complete and checked live (section 16I); screens wait for frontend approval.**
12. **Policy monitoring:** Actions workflow, ingest, three-layer comparator, change reports, proposals, activation UI, health screen.
13. Analytics (BigQuery gold, dashboard).
14. Hardening, demo data, deployment on free tiers, and the assignment deliverables (section 17).

## 16A. Phase 1 delivered: operational schema, audit and human-only guards

Built and tested on real Postgres 17 and 15 (Docker, free). **Applied to the Supabase project on 26 Nov 2026** (Postgres 17, through the session pooler): 23 tables, every one with row-level security on, seeds loaded, and a re-run is a no-op. Live checks passed: the browser's anon key cannot read or write any table or call any workflow function through Supabase's REST API, the audit trail rejects updates, and no check left data behind. The three demo staff are seeded (Loan Officer, Assistant Manager, Branch Manager at `@demo.example.com`). Live checks: each signs in through Supabase Auth, the backend verifies the real token against the project's signing keys and reads the role from the database, a wrong password and public sign-up are refused, and the anon key is not accepted as a login. The seed script reads the password from `.env` and refuses to run unless Supabase reports public sign-up disabled.

- **Migrations** (`supabase/migrations`, applied in order, checksummed, edited files refused): `0001` tables, `0002` audit, `0003` guards, `0004` row-level security and seed data.
- **Tables:** staff and permissions (`staff_profiles`, `role_permissions`, `demo_config`), `applicants`, `enquiries`, `applications`, `status_transitions`, `application_terms`, `term_changes`, `decisions`, `policy_rule_sets`, `policy_activation_log`, `agent_runs`, `documents`, `discrepancies`, `extraction_corrections`, `generated_documents`, `email_outbox`, `email_events`, `disbursements`, `jobs`, `analytics_outbox`, `audit_events`. Policy change proposals and monitor runs arrive with Phase 12, and OCR text lives in BigQuery.
- **Roles and permissions:** exactly three roles, with permissions held in `role_permissions`. Fixed authority is protected by CHECK constraints: only an Assistant Manager or Branch Manager can ever hold `decision.make`, and only a Branch Manager can hold takeover, referral and term adjustment. Policy activation and disbursement release or tracking are CONFIGURABLE demo permissions, labelled as not Bank of Baroda's authorization structure. Demo limits (rate band, tenure range, amount cap) live in `demo_config` as `DEMO_ASSUMPTION`.
- **Human-only guards, in the database:**
  - `decisions`: only an active Assistant Manager or Branch Manager. The role, the AI-recommendation snapshot and the override flag are computed by the database, never trusted from the caller. A decline, a referral or an override of the AI needs a written reason of at least 10 characters. The decision, its audit rows, its analytics-outbox row and the status change happen in one transaction.
  - Status: workflow columns change only through `app_transition`, `app_record_assessment`, `app_claim` and `app_take_over`, which check permission, the status table, the version and preconditions. `AI_ASSESSED` is system-only. `APPROVED`, `DECLINED` and `RECONSIDERATION` need a matching human decision. `SANCTIONED` needs a human approval and all three documents. `DISBURSED` needs a human release.
  - Terms: approved terms are human only, tied to the approving decision or a recorded term change, and inside the demo limits. Term changes are Branch Manager only, with a reason, during review.
  - Policy: rule sets are created as DRAFT and become ACTIVE only with a human holding `policy.activate`, a reason and a passing test result. Active rule sets are immutable, and a value marked `UNVERIFIED_CANDIDATE` can never be inside one.
  - Disbursement: simulated only (a CHECK forbids a real transaction), released only by an authorised human after acceptance and the checklist.
  - Concurrency: every decision, term change and transition carries the application version, and a stale one fails with a serialization error. A real two-connection test confirms the second reviewer waits and then fails.
- **Audit:** append-only. Triggers reject UPDATE, DELETE and TRUNCATE, privileges are revoked even from the backend's service role, and `decisions`, `application_terms`, `term_changes` and `policy_activation_log` are immutable too. Agent activity is always recorded as `AGENT` regardless of session settings. Limit: the database owner can still alter or drop the trigger, and a hash chain is not implemented.
- **Deny by default:** row-level security is on for every table, and `anon` and `authenticated` hold no table privileges, including on tables added later. The browser uses Supabase only to sign in.
- **Backend** (`backend/app/auth.py`, `db.py`): tokens are verified against the project's public ES256 signing keys, with only asymmetric algorithms accepted. Expiry, audience, issuer, `sub` and `iat` are required, and the anon and service-role tokens are rejected. The role always comes from `staff_profiles`, never from token claims, and every endpoint declares `require_roles` or `require_permission`. `/me` returns the signed-in person's role and permissions.
- **Tests:** 116 pass (`backend/tests` 35, `supabase/tests` 81), including negative tests for every "AI cannot" item as it applies to the database: no decision, approved terms, sanction documents, policy activation or disbursement release without the right human.
- **Honest limits:** the database enforces who may act, but the backend connects as a privileged role and tells the database which person is acting. A compromised backend could still name a real manager. The generic audit actor comes from per-transaction settings the backend sets, whereas the decision, terms, policy and release guards use explicit, validated columns instead. Supabase public sign-up must be turned off (Authentication, Sign In / Providers) before seeding: `seed_staff.py` refuses to adopt any pre-existing account it did not create, so an email pre-registered through sign-up can never receive a staff role. Phase 1 does not include the sign-in screen, which belongs to the frontend that is still awaiting your go-ahead.

## 16B. Phase 3 delivered: policy engine, EMI and KFS figures

- **Rules** (`backend/app/policy/schemes/baroda_personal_loan.yaml`): 36 parameters plus overrides for four segments, every one `DEMO_ASSUMPTION` with a note. The loader (`ruleset.py`) refuses any `UNVERIFIED_CANDIDATE` value, any missing parameter or label, an incomplete set of six segments, and an exclusion that could fail a case without being VERIFIED. It also produces the content hash stored with each rule set in the database.
- **Engine** (`engine.py`, deterministic, no AI): 15 rules covering age, age at maturity, income evidence, income consistency, minimum income, job tenure, bureau score, repayment behaviour, account conduct, FOIR capacity, KYC, documents, and the staff, NRI and pensioner flags. It returns rule-by-rule results with confidence labels, the eligible amount by tenure, the recommended amount, tenure, EMI and rate, the FOIR after the loan, and ordered reasons. A failure always says it is a demo assumption. Thin and no-history applicants are never failed for lacking history: the score rule is SKIPPED, and they need six months of income evidence instead of three.
- **Finance** (`finance.py`): rupee-rounded EMI, a reducing-balance schedule that closes at exactly zero, processing fee with minimum, maximum and GST, and an APR equal to the IRR of the net amount received against the actual instalments.
- **Mapping** (`cases.py`): from a `customer_360` row to an engine case. NTB income evidence is supplied from the confirmed statement.
- **Tests:** 208 policy and finance tests (133 finance, 66 engine, 9 on the full book), including EMI and APR checked against an independent float implementation, golden cases for all six segments, the thin-file and no-history principles, exclusions, 20,000 randomised cases checking every invariant, and the whole 1,000-customer book. They also check that the Gate B sheet matches the rules, and that the shipped rule set is accepted and activatable by the database.
- **The book:** across all 1,000 synthetic customers the engine recommends 43%, refers 26% and finds 31% potentially ineligible. Every segment has approvable applicants, no applicant fails for lacking history, and staff, NRI and pensioner cases are referred, never declined for that reason. The most common reasons are capacity, bureau score and income consistency.
- **Assumptions to review (Gate B):** the rate band, the 60-month maximum tenure, a single minimum loan for all branches, pensioners routed to a separate scheme, and the APR shown nominal annualised. No broken-period interest, and the fee is deducted from disbursement.

## 16C. Phase 5 delivered: draft / loan enquiry (backend)

The API is built and tested. The screens wait for frontend approval.

- **Lookup** (`GET /customers/search`, Loan Officer only): by customer ID, mobile, PAN, account number or name, against BigQuery `lh_gold.customer_360`. It reads as the restricted `sa-app-runtime` identity, through the service-account key in deployment and by impersonation with application-default credentials locally. Existing customers only: a new-to-bank person is not found, as at a real branch.
- **Enquiry** (`POST/GET/PATCH /enquiries`, close, convert):
  - An existing customer's draft is prefilled from the bank's own records only: identity, employment, salary or business credits, Bank of Baroda EMIs and KYC status. A new customer is registered with basic details.
  - Credit history is declared by staff. There is no bureau pull, no external KYC, no AI, no document verification and no workflow. A test proves a draft creates no application, agent run, job, decision or document.
  - **Indicative figures** (`policy/indicative.py`) come from the active rule set: a rate range by declared history (plus the new-to-bank premium), an amount sized at the top of that range and on the lower of declared and observed income, tenure options limited by age, and notes. They are labelled "not a loan offer" in the response and enforced by the database.
  - **Documents:** required by employment type, minus what the customer already has. An existing customer with current KYC skips PAN and address proof, and one paid into a Bank of Baroda account skips the bank statement.
  - **Resume and change:** drafts are saved, searched by reference, mobile or name, and updated with a version check, so a stale screen gets a 409.
  - **Close** requires a reason.
  - **Convert** requires an explicit `confirm: true` and the current version. It creates the application (`INTAKE`) and its requested terms in one transaction, and nothing else starts.
  - Roles: Loan Officers create and change drafts, and managers can only view them.
  - **No active rule set means no figures** (HTTP 503), so indicative numbers always come from an approved rule set.
- **Bug found and fixed:** the database connection now uses autocommit. Without it, an endpoint that read before writing silently lost its write, because the audited transaction became a savepoint that was never committed. A regression test covers the real connection code.
- **Security review fix (migration 0005):** a draft no longer changes the shared applicant record. Before this, a second draft for the same customer could silently change the declared income, EMIs and credit history that an in-flight application relied on. Declared figures now live on each draft and are copied onto the application at conversion, where a trigger freezes them. Applicant records are now audited. Two tests cover it.
- **Tests:** 12 indicative-calculator tests and 22 API tests on real Postgres. 363 pass in total across backend and database. `scripts/live_check_enquiry.py` runs the flow against the live services, with real sign-in, BigQuery and Supabase. **Live run on 2026-11-26: 6/6 passed**, after the owner activated `baroda_personal_loan v1`.

## 16D. Phase 6 delivered: intake, registration and document upload (backend)

- **Intake** (Loan Officer, application in `INTAKE`):
  - An existing customer's identity, address and contact details come from the bank's records (BigQuery `customer_360`). Identity is not edited from a loan application; in a real branch that goes through a KYC update.
  - A new-to-bank applicant's registration is completed here: synthetic PAN, gender, residency, staff flag and current address.
  - Intake can't be completed until PAN, email and full address are on file. Those are needed for KYC and, later, the sanction pack.
  - The applicant record is shared. Once another application of the same person has moved past intake, its details can't be changed from here.
- **Complete intake** (`POST /applications/{id}/intake/complete`, version-checked):
  - Works out the document checklist once, from the active rule set and the customer's current bank records. Today's KYC status decides the waivers, not the status seen at enquiry time.
  - Freezes the checklist on the application together with its basis and policy identity, then moves the application to `DOCS_PENDING`.
  - Database guards (migration `0006`): the checklist can be written only during intake and never afterwards, and an application can't leave `INTAKE` without a completed intake.
- **Documents** (`PUT /applications/{id}/documents`):
  - The file is the raw request body, so there's no multipart parsing and no new dependency. Each file names the checklist requirement it satisfies and its physical type: for example, address proof can be a masked Aadhaar, a passport or a voter ID.
  - The type is decided from the file's own bytes (PDF, PNG or JPEG only), cross-checked against the name and declared type. Limit 10 MB. A photograph must be an image.
  - Each file is hashed with SHA-256, and the same file can't be attached twice. A requirement that was waived or isn't on the checklist is refused.
  - Files are stored in the private Supabase Storage bucket `loan-documents`, created by `scripts/setup_storage.py`, which refuses to continue if the bucket is public. The object path is generated by the backend, never taken from the user. The record and the file are written in one transaction, so a storage failure leaves no record behind.
  - Viewing a file gives a 60-second signed URL after a role check, and is recorded as a `DOCUMENT_VIEWED` audit event.
  - Documents are never deleted or changed. A wrong file is withdrawn, with a reason, by someone with the intake permission, and only before verification.
- **Roles:** every role can view applications (`application.view`, added in `0006`). Only the Loan Officer runs intake and uploads.
- **Tests:** 19 API tests on real Postgres (fake storage, auth and BigQuery), covering bad files, mismatched types, size limits, duplicates, waived requirements, storage failure, withdrawal, guards, roles and audit. 384 pass in total across backend and database.
- **Live run on 2026-11-26** (`scripts/live_check_intake.py`), **12/12 passed**:
  - The customer was an existing one, BOB-C0054, whose KYC had lapsed. The checklist therefore required PAN and address proof, and waived the bank statement because the salary is credited to their Bank of Baroda account.
  - Three rendered synthetic documents were uploaded; a duplicate was refused.
  - An Assistant Manager opened one through a signed link, and the bytes matched.
  - The browser's anon key cannot list the bucket, and the file has no public URL.
  - `APP-2026-000001` is left in `DOCS_PENDING` for Phase 7.

## 16E. Phase 7 delivered: document processing, review, discrepancies and attestation (backend)

- **Job runner** (`app/jobs.py`) on the Supabase `jobs` table:
  - Jobs are claimed with `FOR UPDATE SKIP LOCKED`, and a job stuck for 10 minutes is taken again. Retries back off over 30 s, 2 min, 10 min, 30 min and 1 h.
  - When retries run out, the person takes over: the document goes to manual review with the reason.
  - A drain runs in-process right after each upload, and on a schedule through `POST /internal/jobs/drain`. That endpoint uses a shared token and is off unless `INTERNAL_API_TOKEN` is set; it's meant for the Phase 12 Actions cron.
- **Text, from exactly one labelled method per document.** No other OCR engine is used and nothing is silently substituted:
  - `EMBEDDED_TEXT`: a born-digital PDF's own text layer, read with `pypdf` (BSD). Every page needs a real text layer, so scans never pass as extracted. Labelled "Embedded PDF text (not OCR)".
  - `PRECOMPUTED_UNLIMITED_OCR`: real output of the official `baidu/Unlimited-OCR` from a Kaggle batch. `scripts/import_ocr_results.py` imports it, keyed by the file's SHA-256, so an uploaded copy of the same file finds it.
  - `PREPARED_DEMO_TEXT`: generated demonstration values for the 240 degraded demo documents, loaded by the owner with `scripts/load_prepared_demo_text.py`. The script copies no generator labels, and the app identity still cannot read `lh_truth`.
    - Shown everywhere as "Prepared demo data — not OCR".
    - The database refuses to verify a document while any value is still prepared demo data. A person must confirm or re-key each value from the image.
  - Otherwise the document waits as `AWAITING_OCR`, retries, then goes to `NEEDS_MANUAL_REVIEW`, where the Loan Officer keys the values (`MANUAL`).
  - Photographs are checked by eye.
  - Live GPU OCR has no free host (section 19), so no live client is wired in. The documented fallbacks above cover it.
- **Lakehouse:**
  - Page text and raw AI output are appended to BigQuery `lh_raw.ocr_pages` and `lh_raw.extraction_outputs` with load jobs, because the sandbox has no DML or streaming. Created by `scripts/setup_ocr_tables.py`.
  - The app identity has a table-level grant to append to those two tables only. It cannot create tables or write elsewhere, and it still cannot read `lh_truth`; the isolation suite passes 18/18 again.
  - Reviewed values live in Supabase (`document_fields`), next to the workflow.
- **Extraction** with the Gemini Developer API (free tier):
  - **Synthetic PANs are replaced with placeholders before any text leaves the backend**, and restored afterwards. Raw outputs are stored with the placeholders.
  - Pydantic-validated JSON. The model must quote the snippet each value came from. A value whose quote isn't on the cited page is flagged, and so is one that won't normalise; dates become ISO and amounts plain numbers.
  - Results are cached by file hash, model and prompt version, so the same file is never sent twice.
  - A daily budget counter in Postgres (`take_budget`, default 200 calls) stops calls at the cap. Rate limits and errors retry, then fall back to manual review. Calls time out after 60 s.
  - Each run is recorded as an agent run (`DOCUMENT`) with its steps.
- **Review** (Loan Officer, `document.verify`):
  - The review view shows page text, fields with their method and source labels, and the correction history. Every role can read it.
  - Correcting or confirming a value writes an `extraction_corrections` row. The database refuses a person's value without one, refuses AI or system writes over a person's value, and refuses people writing anything but `MANUAL`.
  - **Verify** requires every flagged AI value to be confirmed first. Only a person can verify or reject a document, and a verified document's values are final.
  - Rejecting a document needs a reason, and the customer brings a new one.
- **Discrepancies** (`app/discrepancy.py`), run on verified values only:
  - Rules: name, date of birth, PAN, and income against the declared monthly net. The income tolerances are `DEMO_ASSUMPTION` values: salary slip 10%, bank-statement income credits 15%, ITR 20%. These rules catch the generator's injected cases: name variants, PAN typos, date-of-birth mismatches and income mismatches.
  - An open item moves the application to `DISCREPANCY`. The Loan Officer resolves it with ACCEPT, CORRECT or REQUEST_NEW_DOCUMENT plus a note; once nothing is open, it returns to `DOCS_PENDING`.
- **Attestation:** the Loan Officer attests with a written statement, and the database checks it is true before moving to `DOCS_VERIFIED`. Every checklist item needs a verified document, nothing can still be processing, and no discrepancy can be open. Verification decides nothing about the loan.
- **Security:** the new tables have row-level security, and the browser roles get no tables or functions. `take_budget` isn't callable by anon.
- **Live run on 2026-11-26** (`scripts/live_check_documents.py`):
  - **Documents:** the three documents already uploaded to `APP-2026-000001` were processed with the real storage, BigQuery and Gemini:
    - The PAN card and Aadhaar PDFs used their embedded text (`EMBEDDED_TEXT`). Gemini extracted 4 and 5 fields, none flagged, and they match the bank record: name, date of birth and PAN.
    - The degraded salary-slip PNG got labelled prepared demo data and waits in manual review.
  - **PAN redaction:** the output stored in BigQuery holds `[PAN-1]` and no synthetic PAN.
  - **Model:** Gemini's default had moved on. `gemini-2.5-flash` returns 404 for new users, and `gemini-3.5-flash-lite` answers in about 6 s on the unbilled key, so it's now the default; `GEMINI_MODEL` overrides it. A temporary 503 ("high demand") was retried and then succeeded.
  - **Left for you:** verifying the documents, which is a Loan Officer step.
- **Tests:** 14 Phase 7 tests on real Postgres with fake storage, BigQuery and Gemini. They cover each text method, PAN redaction, flagged quotes, prepared data never being evidence, retries and budget, the cache, the AI boundaries, roles, discrepancies, attestation and the internal token. 398 pass in total.

## 16F. Phase 8 delivered: policy knowledge base and assessment graph (backend)

- **Sources** (`backend/app/policy/sources.yaml`, the same list Phase 12 will monitor). Official public pages only:
  - Bank of Baroda, Baroda Personal Loan product page. It is readable now, which it was not during the Gate B research.
  - RBI, three Master Directions for commercial banks, all updated to 1 October 2026: Responsible Business Conduct (this is where KFS, APR, penal charges and floating-rate reset now sit), Know Your Customer, and Credit Risk Management.
  - RBI's consolidation of 28 November 2025 repealed the older stand-alone circulars, such as KFS (April 2024) and penal charges (August 2023). So the current Directions are indexed, not those circulars.
  - The Directions' PDF copies on `rbidocs.rbi.org.in` sit behind a bot check. The ingest does not get around it: it reads the HTML editions on `www.rbi.org.in`, and an error or bot-check page is never indexed.
- **Ingest** (`scripts/ingest_policy_sources.py`, owner tool, manual seed):
  - **Fetching:** a conditional GET using the stored ETag and Last-Modified, one request at a time and spaced out.
  - **Raw snapshots** go to a new private Supabase bucket, `policy-sources`.
  - **Text extraction:** HTML pages are read from their markup (`HTML_TEXT`). This is not OCR, and neither is `EMBEDDED_TEXT` from a born-digital PDF. A scanned PDF is recorded as `AWAITING_OCR` and given no text.
    - Only the named container is read. Site navigation, scripts and **HTML comments** are dropped; the Bank of Baroda page keeps stale figures inside comments.
  - **Storage:** versions are appended to `lh_raw.policy_sources` and `lh_raw.policy_chunks`. `lh_curated.policy_chunks` (`bigquery/curated/04_policy_chunks.sql`) holds the latest version of each source.
  - **Change detection:** a version is added only when the cleaned text changes, so markup churn is not a change. A second run recorded nothing.
- **Retrieval** (`app/policy_kb.py`): BM25 over the chunks. It is deterministic, free and needs no embeddings.
  - Each engine rule maps to a query and the sources it may be cited from. This map is maintained by people.
  - Citations are produced only for failed or referred rules, plus three conduct topics: KFS and APR, penal charges, and rates and charges.
  - Below a relevance floor, nothing is cited ("no matching passage"), rather than a weak guess.
  - Every citation carries the URL, source version, fetch time, section and a quote.
- **Assessment graph** (`app/assessment.py`, LangGraph), queued in the same transaction as the attestation and run by the job runner:
  1. Load the application, its verified documents and the rule set frozen at intake. An in-flight application keeps its version.
  2. Load the customer 360 from `lh_gold` as the app identity. Existing customers are found by customer ID. A new-to-bank applicant is found by PAN with a date-of-birth match, as a bureau pull would be.
     - **No hit:** the applicant is treated as new to credit, and the PAN as not verified (identity is referred).
     - **A PAN record with a different date of birth:** never used.
  3. Gather evidence, each item with its source:
     - **Income:** bank-held credits, then values a Loan Officer verified on a bank statement, salary slips or an ITR. Prepared demo data can never be evidence.
     - **Obligations:** the higher of bureau-reported and declared EMIs.
  4. Run the deterministic engine: rule-by-rule results and the recommendation.
  5. Retrieve policy passages with citations.
  6. Gemini writes the explanation.
     - It sees no name, PAN, ID or mobile number.
     - Its text is discarded, and a fixed template used instead, if it contains any number the engine did not produce or any decision language.
     - With no key, no budget left or an error, the template is used. The recommendation never depends on the explanation.
  - **Recording:** the run and its steps (with the data each used) go to `agent_runs`. The engine's eligible terms are inserted, then `app_record_assessment` moves the application to `AI_ASSESSED` as the system actor.
- **Guard** (`supabase/migrations/0008_assessment.sql`): `ELIGIBLE` terms cap what a human may approve. They are now accepted only from the system actor, at `DOCS_VERIFIED`, and only when they match a finished assessment run figure for figure and rule set for rule set. No person can write that cap.
- **API:**
  - `GET /applications/{id}/assessment` (every role), labelled "AI recommendation, human decides".
  - `POST /applications/{id}/assessment` (Loan Officer) queues the assessment again after a failure, only at `DOCS_VERIFIED`.
  - `GET /policy/sources` and `GET /policy/search` (every role).
- **Fixed on the way:**
  - The engine's identity message used `capitalize()`, which printed "Pan check" and "Kyc is expired". It now upper-cases only the first letter.
  - `psycopg` was missing from `requirements.lock.txt`, which would have broken a deploy from the lock.
- **Live run on 2026-11-26:**
  - **Ingest:** 4 sources, 603 chunks. A re-run found no change, and `sa-app-runtime` can read the curated table.
  - **Dry run** (`scripts/live_check_assessment.py`, writes nothing): one synthetic salaried customer per segment, run against real BigQuery, the knowledge base and Gemini.
    - ETB established: recommended.
    - ETB NTC and ETB thin: referred for evidence, job tenure and KYC, each with a citation.
    - NTB without documents: referred, because there is no income evidence. NTB established: potentially ineligible, from a score and repayment failure on its bureau file (demo assumption).
    - Score rules were skipped, never failed, for NTC.
    - All six Gemini explanations passed the number check.
- **Noted for Phase 12, not acted on:**
  - The Bank of Baroda page states figures that differ from some demo assumptions, for example a repayment period of up to 84 months and FOIR stated on gross monthly income.
  - These become `VERIFICATION_CANDIDATE` items through the fact-extraction layer. No rule set changed, because activation stays a human approval.
  - Facts and fact changes (`PolicyFacts`) are part of the Phase 12 comparator.
- **Tests:** 7 unit and 9 flow tests on real Postgres with fake BigQuery, knowledge base and Gemini. They cover:
  - extraction, chunking and retrieval;
  - evidence selection;
  - the explanation checks;
  - the full journey from attestation to `AI_ASSESSED`;
  - an NTB applicant with no bureau hit, and one with a different date of birth;
  - potentially ineligible on a written-off account;
  - retry and re-queue;
  - roles;
  - and the AI boundaries: no forward, decision or terms, and no second eligible cap.

  445 tests pass in total.

## 16G. Phase 9 delivered: review and decision (backend)

- **Flow** (`app/routers/review.py`). Every step carries the application version, so a stale screen gets a 409.
  1. **Forward:** the Loan Officer moves an `AI_ASSESSED` file to `FORWARDED`. An optional note for the reviewer is recorded with the transition.
  2. **Review queue:** for the Assistant Manager and Branch Manager only. It lists `FORWARDED`, `UNDER_REVIEW` and `RECONSIDERATION` files and can be filtered to all, mine, unassigned or one status. It shows the AI recommendation and the eligible amount.
  3. **Claim:** moves the file to `UNDER_REVIEW` and assigns it to the reviewer. If two reviewers claim at once, only one gets it.
  4. **Take-over:** Branch Manager only, with a written reason, recorded as an `APPLICATION_TAKEOVER` audit event.
  5. **Adjust terms:** Branch Manager only, on their own file while it is under review, with a reason. The change is stored in `term_changes`, and the database checks the rate band, the tenure range and the amount cap (all demo limits).
  6. **Decide:** only the reviewer who holds the file.
     - **Approve:** the approved terms are the latest adjustment, or else the engine's eligible terms. The reviewer sends back the terms they saw, and if those are no longer current the request is refused. The decision and the approved terms are written in one transaction.
     - **Decline:** needs a reason, and records no approved terms.
     - **Refer:** Branch Manager only, with a reason. The file goes back to the queue as `RECONSIDERATION`, and any reviewer can pick it up and see the referral note.
     - **Overrides:** a decline of a recommended case, or an approval of a potentially ineligible one, needs a reason. The database computes the AI snapshot and the override flag and writes `DECISION_OVERRIDE`. Every decision also writes an `analytics_outbox` row in the same transaction.
  7. **Review view** (`GET /applications/{id}/review`, all roles), with everything a reviewer needs:
     - the AI recommendation, explanation and reasons, labelled "AI recommendation, human decides";
     - the eligible, current and approved terms, each with its EMI;
     - the demo limits, labelled `DEMO_ASSUMPTION`;
     - the term changes, decisions and review history;
     - the actions the caller may take now.
- **Guards tightened** (`supabase/migrations/0009_review_and_decision.sql`):
  - **Assignment:** a decision needs the reviewer who holds the file. Before, a Branch Manager could decide another reviewer's file directly; now they take it over first, with a recorded reason.
  - **Amount cap:** when the engine found no eligible amount, the term cap used to be skipped. Now the cap is the requested amount, so terms can never exceed what was asked for. Approving such a file needs the Branch Manager to set terms first.
- **AI boundary:** unchanged and re-tested. Only these human actions decide. The assessment cannot forward, claim, decide or set approved terms.
- **Live run on 2026-11-26** (`scripts/live_check_review.py`), 12 of 12 checks passed:
  - **API:** all three demo staff signed in with Supabase Auth. The queue is reviewer-only. `APP-2026-000001`, not yet assessed, can be neither forwarded, claimed nor decided, and the Loan Officer can never decide.
  - **Guards:** both tightened guards were probed on the live database inside a transaction that was rolled back, and nothing was kept.
  - **Side effect:** sequences ignore rollback, so the probe used reference number `APP-2026-000002`. The next application will be `APP-2026-000003`.
- **Tests:** 9 review-flow tests on real Postgres. The applications reach `AI_ASSESSED` through the real attestation and assessment job. They cover:
  - forward, queue, claim and approve;
  - every role refusal;
  - a decline with a reason and no terms;
  - a Branch Manager term adjustment inside the limits, with stale terms refused;
  - referral and re-claim;
  - take-over;
  - a concurrent claim;
  - an override approval;
  - and no eligible amount.

  454 tests pass in total.

## 16H. Phase 10 delivered: sanction pack and approval email (backend)

- **Trigger:** a human approval queues the `sanction_pack` job in the same transaction as the decision. The job then:
  - renders the three PDFs from the approved terms and the rule set the application was assessed under (`app/sanction.py`, ReportLab, BSD licence);
  - stores them in a new private bucket, `generated-documents`;
  - in one system transaction: records `generated_documents`, moves the application to `SANCTIONED`, and queues the approval email in the outbox.
  - The database still refuses sanction documents without a human `APPROVED` decision and the human-approved terms. A declined or referred file gets no pack.
- **Documents.** Every figure comes from the same `finance.py` schedule, so the letter, the KFS and the schedule agree to the paisa:
  - **Sanction letter:**
    - the terms, EMI, fee with GST, net disbursal, APR, penal charges, prepayment and the cooling-off period;
    - the conditions before disbursement: acceptance within the KFS validity, the loan agreement, a NACH mandate and original KYC;
    - signed as the human approver, with a borrower acceptance line.
  - **Key Facts Statement**, following the RBI Commercial Banks Responsible Business Conduct Directions 2025, section B, paragraph 348:
    - a unique proposal number and a validity of 3 working days (weekends skipped; bank holidays not modelled);
    - Part 1 (interest rate and fees) and Part 2 (cooling-off, recovery, grievance redressal including the RBI Integrated Ombudsman, no undisclosed charges);
    - an APR computation sheet, the amortisation schedule, and a borrower acknowledgement.
  - **Penal charges** are disclosed as charges on the overdue amount: not added to the rate, never capitalised, and no penal charges on penal charges. This follows paragraph 351.
  - **EMI schedule:** the reducing-balance schedule with totals. The dates are illustrative until disbursement fixes them.
  - **Labels on every page:** an orange "Baroda Lending Desk (Demo)" header, a "DEMO - NOT A LOAN OFFER" watermark, and a footer stating synthetic data, demo parameters, not issued by Bank of Baroda.
  - **No PAN or mobile number is printed.** Rendering refuses if a PAN-like value appears anywhere in the data, because the pack leaves through an external email service.
  - **Rendering is deterministic:** the same inputs give the same bytes, so a retried job finds its own file.
- **APR check:**
  - RBI's worked example (₹20,000, 15%, 24 EMIs, ₹400 charges) gives 17.07% with our formula: monthly IRR on the net disbursed amount × 12.
  - Total interest comes out within ₹2 of RBI's ₹3,274, because RBI multiplies a rounded EMI while our last instalment clears the balance exactly.
- **Email** (`app/outbox.py`):
  - The outbox row is written in the same transaction as the sanction, and the `send_email` job sends it through Resend's HTTP API.
  - The outbox id is the `Idempotency-Key`. 429, 5xx and network errors retry with backoff; a permanent 4xx goes straight to `FAILED`. Every attempt is an `email_events` row, and the API key is never logged.
  - A daily cap (default 80) uses the same counter as Gemini.
  - **Demo routing:** mail always goes to `EMAIL_REDIRECT_TO`, or else `delivered@resend.dev`, and the body names the intended address. The database refuses any outbox address on `example.invalid`.
  - **Email failure never blocks the sanction.**
  - **Bounced or failed email:** the Loan Officer can correct the applicant's email, give a reason, and re-send with a new key. This is audited as `EMAIL_RESENT`. A complaint is never re-sent.
- **Delivery status:**
  - `POST /webhooks/resend` verifies Svix signatures, with replay protection (5 minutes) and de-duplication by message id. Status moves forward only, so a late "sent" never overwrites "delivered".
  - Locally, `GET /applications/{id}/emails?refresh=true` polls Resend instead.
- **Guards** (`supabase/migrations/0010_sanction_and_email.sql`):
  - Generated documents and email events are immutable.
  - An approval email needs a human approval, and may attach only that application's own documents.
  - An email's application, kind, subject and attachments are fixed once queued, and outbox rows are never deleted.
- **Endpoints:**
  - the sanction pack view;
  - signed 60-second document URLs, audited as `SANCTION_DOCUMENT_VIEWED`;
  - a retry of pack generation;
  - the email list with refresh;
  - re-send;
  - the webhook.
- **Live run on 2026-11-26** (`scripts/live_check_sanction.py`, changes no application):
  - **Storage:** the private bucket was created. A KFS rendered with the ACTIVE rule set's charges made a signed-URL round trip, was not reachable publicly, and was then removed.
  - **Email:** one real email with the three PDFs was accepted by Resend for `delivered@resend.dev`. Repeating the call with the same key returned the same message, so nothing was sent twice.
  - **Found, not changed:**
    - `EMAIL_FROM` in your `.env` is a `gmail.com` address, which Resend refuses (403, domain not verified). The check ran with `onboarding@resend.dev`, the free sender with no domain to buy.
    - The Resend key is send-only, so polling delivery status returns 401. The app now says so; status needs the webhook (after deployment) or a full-access key.
- **Tests:** 6 unit and 8 flow tests. They cover:
  - the RBI APR example;
  - the documents' content and labels;
  - no PAN or mobile number in the pack or the email;
  - deterministic rendering;
  - webhook signatures;
  - send once, with one key across retries;
  - permanent failure and re-send;
  - the daily cap;
  - forward-only webhook status;
  - polling and a send-only key;
  - the guards.

  468 tests pass in total.

## 16I. Phase 11 delivered: simulated disbursement and confirmation email (backend)

- **No real banking transaction, ever.** `is_simulated` is forced true by a database CHECK. Every view and email carries the "SIMULATED DISBURSEMENT - no money moves" banner, and the references are visibly synthetic (`SIM-…`, `SIMLN…`).
- **Steps** (`app/routers/disbursement.py`):
  1. **Acceptance:** the Loan Officer records it (`disbursement.track`) when the customer accepts the sanction and the KFS at the branch.
     - Within the KFS validity of 3 working days, no reason is needed.
     - After it, the officer records how the bank re-confirmed the terms. This follows RBI's rule that the lender is bound only within the validity period.
     - Once recorded, the acceptance cannot be changed.
  2. **Pre-disbursement checklist:** the Loan Officer confirms four items: KFS acknowledged, loan agreement executed, repayment mandate registered, and original KYC verified.
     - All four complete it, recorded with the name and time.
     - Unticking an item reopens it and blocks release.
  3. **Release:** by an Assistant Manager or Branch Manager (`disbursement.release`, a configurable demo permission). It must be a different person from whoever recorded the acceptance or completed the checklist (maker-checker).
     - It records a simulated reference, a loan account number, the first EMI date (one month after release, as the KFS illustrated) and the net amount credited (the approved amount minus the fee and GST).
     - The application moves to `DISBURSED`, and the confirmation email is queued, in one transaction.
  4. **Confirmation email:** separate from the approval email, with no attachments. It gives:
     - the loan account and the reference;
     - the sanctioned amount, the charges deducted and the net credited;
     - the EMI and the first and last due dates;
     - the rate and APR;
     - the cooling-off end date;
     - the simulated banner and the demo routing note.
     No PAN or mobile number is included. A failed email never undoes the disbursement.
- **Guards** (`supabase/migrations/0011_disbursement.sql`):
  - **Checklist:** completeness is checked in the database (acceptance recorded, all four items, and a person with `disbursement.track`).
  - **Maker-checker:** enforced even when one person holds both demo permissions.
  - **Approved terms:** the release is tied to the latest human-approved terms, chosen by the database rather than the caller.
  - **Net amount:** must be recorded and can never exceed the approved amount.
  - **Confirmation email:** needs a released disbursement.
- **Live run on 2026-11-26** (`scripts/live_check_disbursement.py`), 9 of 9 checks passed:
  - **API:** the demo logins see the banner on `APP-2026-000001`, which is not yet sanctioned, so nothing is allowed, and the Loan Officer cannot release.
  - **Guards:** a throwaway synthetic application with a fixed reference, so no reference number was used, ran from approval to `DISBURSED` against every live guard, inside a transaction that was rolled back.
  - **Email:** its real confirmation email was rendered from the live data and accepted by Resend for the demo inbox. Nothing was kept.
- **Tests:** 6 flow tests on real Postgres. Applications reach `SANCTIONED` through the real approval and sanction job. They cover:
  - the full path and its roles;
  - maker-checker with a configurable grant;
  - late acceptance;
  - a reopened checklist;
  - not sanctioned;
  - and a failed email.

  The Phase 1 disbursement tests were updated to the stricter checklist. The test harness now resets the daily budget counters per test, because the suite had begun to use up the test database's Gemini allowance and failed depending on test order. 475 tests pass in total.

## 17. Deliverables

**Build:**
1. Working demo, run locally and hosted on free tiers.
2. GitHub repo (public) with README and run instructions.
3. Sample sanction letter, KFS, EMI schedule and both emails.
4. Sample policy change reports from the monitor.
5. OCR feasibility report (Gate C).

**Design and documentation:**
6. Architecture, lifecycle swimlane and agent workflow diagrams.
7. Requirements pack: user stories, permissions matrix and status model.
8. Demo-parameter and policy document with confidence labels.
9. ERD and the lakehouse layer map.
10. Responsible-AI and governance note.
11. Demo script, one walkthrough per role.

**Assignment brief items (from `BA_Assignment Agentic AI Loan Approval Demo.pdf`):**
- D1 Demo journey.
- D2 Approach story page (lakehouse first, then agentic solution).
- D3 Architecture page.
- D4 Supporting pages (Contact, About, FAQ).
- D5 Research note (1–2 pages).
- D6 AI usage log (1 page): a **separate document kept outside the repository**, consistent with the authorship rule in section 14.
- A screen recording of 5 minutes or less.
- Bonus: hosted demo running on BigQuery data.

## 18. Free-tier register

| Service | Free tier (verified 25 Nov 2026 unless marked) | Limitation that matters here |
|---|---|---|
| BigQuery sandbox | No billing or card. 10 GiB lifetime storage (not refunded on deletion). 1 TiB queries per month | 60-day table expiry. No streaming, no DML, no Data Transfer. Unsupported features return `billingNotEnabled`. Docs don't address service accounts (tested in Phase 0) |
| Supabase Free | 500 MB database, 1 GB storage, 50 MB per upload, 5 GB egress, 50,000 MAU, 2 active projects | **Projects pause after 1 week of inactivity** (keepalive ping from Actions). No backups |
| Gemini Developer API | Free tier, no billing. Several Flash, Flash-Lite and 2.5 models free of charge | **Free-tier content may be used to improve Google products.** Per-model limits are in AI Studio (not confirmed). Exceeding limits returns a rate-limit error. Limits are per project. The key's project must have no billing linked. Embedding availability unconfirmed |
| Resend Free | 3,000 emails per month, 100 per day, 3 domains, 1 webhook endpoint, 30-day retention, attachments up to 40 MB, idempotency keys | Test addresses count against quota. Sandbox-sender restrictions unconfirmed, and I will not buy a domain |
| GitHub Actions + public repo | Standard runners free for public repos. Cron minimum 5 minutes | Delays under load. Default branch only. **Disabled after 60 days without repo activity.** Free-plan artifact storage is 500 MB |
| Vercel Hobby | Free, no card. Functions up to 300 s. Build up to 45 min | Personal and non-commercial use only. The Python limits were not read in full |
| Render free web service | 750 instance hours per month | Spins down after 15 minutes idle with ~1 minute cold start. No persistent disk. No background workers or cron on the free tier. RAM not stated on the page (commonly 512 MB, to be checked) |
| Kaggle free GPU (optional, OCR batches only) | Interactive notebooks, ~30 GPU hours per week (unconfirmed) | Manual, session-limited, availability varies, internet access may need phone verification. Never a dependency of the application |
| Google Cloud Storage, Cloud Run, Secret Manager | Free tier exists | **Requires a billing account, so excluded** |
| Cloud Scheduler | Not covered on the pricing page | Treated as billing-dependent, so excluded |

## 19. Components: free, blocked, and the free alternative

| Component | Status | Free approach |
|---|---|---|
| Frontend hosting | Free | Vercel Hobby |
| Auth, workflow, audit | Free | Supabase Free |
| Lakehouse | Free with limits | BigQuery sandbox. Load jobs and CTAS instead of DML, rebuildable data, deliberate reloads |
| Dataset generation | Free | Local, seeded |
| Gemini extraction and explanations | Free with limits | Developer API free tier, caching, budget counters, synthetic data only |
| Email | Free with limits | Resend free, test addresses, redirect to your own inbox |
| Policy monitoring | Free with limits | Actions cron, free tier restrictions documented, PDFs queue for OCR |
| Document storage | **Blocked (GCS)** | Supabase Storage (1 GB, 50 MB per file), signed URLs |
| Backend hosting | **Blocked (Cloud Run)** | Render free (cold starts) |
| Scheduler | **Blocked (Cloud Scheduler)** | GitHub Actions cron plus an in-process job runner |
| Secret storage | **Blocked (Secret Manager)** | Render, Vercel and GitHub secrets |
| **Live GPU OCR** | **Blocked** (no local GPU, no free always-on GPU, Hugging Face excluded) | Optional Kaggle free GPU batches (official model, run by hand), precomputed results labelled `PRECOMPUTED_UNLIMITED_OCR`, embedded-text extraction for born-digital policy PDFs, manual review |
| Domain / custom email sender | **Blocked** | Provider subdomains, Resend test addresses |

## 20. Risks and constraints (not scope changes)

- **Deadline:** the assignment brief gives 7 calendar days from receipt (6:00 PM IST on Day 7), and clarification questions are due by Day 2. The full scope in this plan is large against that window. This is flagged as a risk only. No feature has been removed.
- **OCR:** there is no free live GPU service, so live OCR is not guaranteed. Kaggle is manual and its availability varies. Gate C may show the model cannot run on the free notebooks at all. The demo then relies on precomputed results, embedded-text extraction for born-digital PDFs, and manual review. The application never depends on Kaggle.
- **Render cold starts** of ~1 minute and an unconfirmed RAM limit. A warm-up before demos is needed.
- **Supabase** pauses after a week of inactivity. A keepalive ping is part of the monitoring workflow.
- **BigQuery** 60-day expiry and a lifetime storage quota.
- **Gemini** free-tier limits and data-use terms. Only synthetic and public documents are used.
- **Existing-account billing state:** if your current Google Cloud project or the Gemini key's project already has a billing account linked, free-tier guarantees no longer hold. Verify and, if needed, use fresh unbilled projects.
- **Bank of Baroda site:** product pages returned HTTP 500 during research, and RBI may block runner IPs. Both are tested early.
- **Unverified policy figures** stay `DEMO_ASSUMPTION` until official documents are available.

## 21. Decisions

**Decided:**
1. No billing anywhere, and no project with billing is used.
2. No Hugging Face. Kaggle free GPU is optional for OCR batches and never a dependency.
3. Embedded-text extraction is allowed for born-digital policy PDFs, labelled and referenced.
4. Official `baidu/Unlimited-OCR` only. No forks, quantized builds or other engines.
5. Gate A decisions confirmed: credit-history status stays out of `customers`; about 480 sample documents for about 120 customers with `documents_truth`; the segment distribution is fixed at 440/80/80/250/50/100. Six review corrections are implemented (canonical ID, reproducibility, exact thin-file counts, ground-truth isolation, synthetic PAN safety, repayment-account consistency). Phase 4 does not continue until you approve. The 480 sample documents are rendered and validated.
6. Weights may be downloaded once from the verified official ModelScope repository, for an optional Kaggle-only run. No Hugging Face, ZeroGPU, forks, quantized builds or other engines. Kaggle is never a dependency.
7. `PREPARED_DEMO_TEXT` is approved for generated demonstration values only, always shown as "Prepared demo data — not OCR".
8. Phase 0 is complete (billing canary passed).

**Open:**
1. **Final review of the corrected Gate A** (`docs/GATE_A_DATASET_SCHEMA.md`) and go-ahead for the rest of Phase 4.
2. **Optional Kaggle OCR run:** approved in principle, not yet run (it needs demo documents, so Phase 4 onward). The outcome is recorded in `docs/GATE_C_OCR_FEASIBILITY.md`.
3. **Push to GitHub:** on hold until you have reviewed the documents in `docs/`.
4. **Official Bank of Baroda documents** for verification, when available. Not blocking.
