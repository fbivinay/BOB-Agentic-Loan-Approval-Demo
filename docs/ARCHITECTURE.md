# Architecture

Baroda Lending Desk (Demo) is an agentic loan-processing demo for the standard Baroda Personal Loan, built on synthetic data and free tiers only. The AI extracts, checks, retrieves policy and recommends. People decide, and the database enforces that.

## System context

```mermaid
flowchart LR
  subgraph Staff["Branch staff (desktop browser)"]
    LO[Loan Officer]
    AM[Assistant Manager]
    BM[Branch Manager]
  end
  FE["Next.js staff app<br/>(Vercel Hobby, pending design approval)"]
  API["FastAPI backend<br/>(Render free)"]
  SB[("Supabase<br/>Auth, Postgres workflow + audit,<br/>private Storage buckets")]
  BQ[("BigQuery sandbox lakehouse<br/>lh_raw / lh_curated / lh_gold<br/>(lh_truth isolated)")]
  GEM["Gemini Developer API<br/>(extraction, policy facts, explanations)"]
  RS["Resend<br/>(approval and disbursement emails)"]
  GHA["GitHub Actions<br/>(hourly drain, daily policy monitor)"]
  SRC["Official sources<br/>RBI Master Directions + feed,<br/>Bank of Baroda pages"]
  OCR["baidu/Unlimited-OCR<br/>(optional Kaggle batch, imported)"]

  Staff --> FE -->|Supabase JWT| API
  API --> SB
  API -->|app identity, no ground truth| BQ
  API -->|synthetic text, PANs redacted| GEM
  API --> RS
  GHA -->|internal token| API
  API -->|allow-listed hosts only| SRC
  OCR -.->|results file, keyed by file hash| BQ
```

- **The backend is the only client of the database, storage, BigQuery, Gemini and Resend.** The browser holds only the Supabase anon key and the user's JWT.
- **Roles and permissions** are read from the database on every request, never from token claims.
- **Ground truth:** `lh_truth` holds the generator's answers, used only for evaluation. The app and agent identities have no access to it, and a test proves this (18 checks).

## Lifecycle swimlane

```mermaid
sequenceDiagram
  autonumber
  actor LO as Loan Officer
  participant SYS as System / agents
  actor REV as Assistant or Branch Manager
  participant CUS as Customer (synthetic)
  LO->>LO: Enquiry: search the customer, indicative figures, missing documents
  LO->>SYS: Convert to an application, intake (KYC details, frozen checklist)
  LO->>SYS: Upload documents (private bucket, type checked)
  SYS->>SYS: Document graph: text by one labelled method, Gemini fields with quotes
  LO->>SYS: Review and confirm fields, resolve discrepancies, attest
  SYS->>SYS: Assessment graph: customer 360, policy engine, policy citations, explanation
  SYS-->>LO: AI_ASSESSED: recommendation only
  LO->>REV: Forward with a note
  REV->>REV: Claim (or Branch Manager take-over), review, adjust terms (Branch Manager)
  REV->>SYS: Decide: approve / decline (reason) / refer (Branch Manager)
  SYS->>SYS: Sanction pack (letter, KFS, EMI schedule), SANCTIONED
  SYS-->>CUS: Approval email with the three PDFs (redirected to a test inbox)
  LO->>SYS: Customer acceptance, pre-disbursement checklist
  REV->>SYS: Release (a different person): simulated disbursement
  SYS-->>CUS: Disbursement confirmation email
```

**Statuses:** `INTAKE` → `DOCS_PENDING` ⇄ `DISCREPANCY` → `DOCS_VERIFIED` → `AI_ASSESSED` → `FORWARDED` → `UNDER_REVIEW` → `APPROVED` / `DECLINED` / `RECONSIDERATION` → `SANCTIONED` → `DISBURSED`. See [REQUIREMENTS.md](REQUIREMENTS.md#status-model) for who may make each move.

## Agent workflows

### Document graph (job runner)

```mermaid
flowchart LR
  U[Upload] --> T{Text method}
  T -->|born-digital PDF| E[EMBEDDED_TEXT]
  T -->|imported Kaggle batch| P[PRECOMPUTED_UNLIMITED_OCR]
  T -->|degraded demo document| D[PREPARED_DEMO_TEXT<br/>never evidence]
  T -->|none| W[AWAITING_OCR, retries,<br/>then manual review]
  E & P --> R[Redact synthetic PANs] --> G[Gemini: fixed schema,<br/>value + page + quote]
  G --> C[Check: quote on page,<br/>value in quote, normalise]
  C --> F[Fields: AI_EXTRACTED<br/>flagged ones need a person]
  F --> H[Loan Officer confirms or corrects<br/>verifies, resolves discrepancies, attests]
```

### Assessment graph (LangGraph)

```mermaid
flowchart LR
  A[load_application<br/>verified docs, frozen rule set] --> B[load_credit_record<br/>customer 360 by ID, or PAN + DOB]
  B --> C[gather_evidence<br/>income and obligations, with sources]
  C --> D[evaluate_policy<br/>deterministic engine, 15 rules]
  D --> E[retrieve_policy<br/>BM25 over official sources, citations]
  E --> F[write_summary<br/>Gemini; rejected if a number is invented<br/>or it speaks as the decider]
  F --> G[app_record_assessment<br/>recommendation only]
```

- **The recommendation comes from the engine alone:** `RECOMMENDED_FOR_APPROVAL`, `REFER_FOR_FURTHER_REVIEW` or `POTENTIALLY_INELIGIBLE`.
- **A missing credit history is never a failure.** Thin-file and new-to-credit applicants are assessed on the evidence that exists.

### Policy monitor (daily)

```mermaid
flowchart LR
  S[Watchlist + RBI feed] --> F[Conditional fetch<br/>allow-listed hosts]
  F --> A[Layer A: document change]
  A --> B[Layer B: Gemini facts with quotes]
  B --> C[Layer C: human-maintained<br/>fact to rule map]
  C --> R[Change report + proposals]
  R --> H[Branch Manager drafts a rule set]
  H --> T[Golden cases run]
  T --> X[Activation with a reason<br/>database refuses if tests failed]
```

## Where things live

| Concern | Where |
|---|---|
| Workflow state, audit trail (append-only), human-only guards | Supabase Postgres (`supabase/migrations/0001`–`0014`) |
| Uploaded documents, sanction PDFs, policy source snapshots | Supabase Storage, three private buckets, 60-second signed URLs |
| Synthetic bank data, credit features, customer 360, OCR text, policy chunks and facts, analytics events and KPIs | BigQuery `lh_raw` → `lh_curated` → `lh_gold` ([DATA_MODEL.md](DATA_MODEL.md)) |
| Policy rules | Versioned rule sets in Supabase. Each application keeps the version frozen at its intake. |
| Scheduling | GitHub Actions: `jobs-drain.yml` (hourly), `policy-monitor.yml` (daily) |
| Secrets | Local `.env` (gitignored), Render environment, GitHub Actions secrets |
