# Demo script

One walkthrough per role, then a 5-minute recording plan.
- **Before the demo:** warm up the backend (`/health`). Optionally reset the demo data (`scripts/reset_demo.py`, see [DEPLOYMENT.md](DEPLOYMENT.md)).
- **Screens:** they follow the approved design (plan section 14A). Until the frontend is built, each step names the API endpoint it uses. Every step works today through `/docs` locally, or through the live-check scripts.

Sign-ins are the three one-click demo accounts: Loan Officer, Assistant Manager, Branch Manager.

## 1. Loan Officer (about 6 minutes)

| Step | What to show | Endpoint |
|---|---|---|
| 1 | Search an existing customer by mobile or ID (for example `BOB-C0027`). Bank data prefills, and no bureau data appears yet. | `GET /customers/search` |
| 2 | Create an enquiry: indicative amount, rate range and missing documents, labelled "not a loan offer". | `POST /enquiries` |
| 3 | Convert it, complete intake (the checklist is frozen from the active rule set), and upload the documents. | `POST /enquiries/{id}/convert`, `POST /applications/{id}/intake/complete`, `PUT /applications/{id}/documents` |
| 4 | Open a processed document: page text beside the fields, each with its method and quote. Confirm a flagged value. | `GET .../documents/{doc}/review`, `PATCH .../fields`, `POST .../verify` |
| 5 | A name variant opens a discrepancy. Resolve it with a note, then attest. | `GET .../discrepancies`, `POST .../resolve`, `POST .../documents/attest` |
| 6 | The assessment arrives: recommendation, the 15 rules, evidence sources, policy citations and agent steps. Forward it with a note. | `GET /applications/{id}/assessment`, `POST .../forward` |
| 7 | After approval: the sanction pack and the approval email. Record the customer's acceptance and the checklist. | `GET .../sanction-pack`, `POST .../disbursement/acceptance`, `POST .../disbursement/checklist` |

## 2. Assistant Manager (about 4 minutes)

| Step | What to show | Endpoint |
|---|---|---|
| 1 | Review queue: the forwarded file with the AI recommendation and the eligible amount. Claim it. | `GET /review/queue`, `POST .../claim` |
| 2 | Review view: "AI recommendation, human decides", the reasons, eligible and current terms with EMI, and the demo limits. | `GET .../review` |
| 3 | Approve the exact terms shown. The sanction pack is generated, the status becomes SANCTIONED, and the approval email goes to the test inbox with three PDFs. | `POST .../decision` |
| 4 | Try declining a recommended case without a reason: refused. With a reason: recorded as an override. | `POST .../decision` |
| 5 | Release the simulated disbursement. It is refused for whoever worked on the checklist, then released by a different person. The confirmation email follows. | `POST .../disbursement/release` |
| 6 | Analytics: historical against live, AI against human, the funnel, the loan book. System health: queues, budgets, monitoring. | `GET /analytics/overview`, `GET /system/health` |

## 3. Branch Manager (about 5 minutes)

| Step | What to show | Endpoint |
|---|---|---|
| 1 | Take over a file held by an Assistant Manager, with a reason (audited). | `POST .../take-over` |
| 2 | Adjust the terms within the demo limits. An amount over the cap is refused; a lower amount with a reason is accepted. Approve. | `POST .../terms`, `POST .../decision` |
| 3 | Refer another file for reconsideration. It returns to the queue with the note. | `POST .../decision` (REFERRED) |
| 4 | Policy: the latest monitor run. Bank of Baroda's page confirms some demo figures and differs on others: ₹1 lakh minimum at metro branches, 84 months, 3% prepayment. | `GET /policy/reports`, `GET /policy/proposals` |
| 5 | Draft a candidate that marks the ₹20 lakh maximum `VERIFIED`, citing the proposal. The golden cases run. Activate with a reason. A candidate that breaks a principle is refused. | `POST /policy/rule-sets/draft`, `POST .../activate` |
| 6 | Review the RBI compliance item (KFS validity) and record the note. | `POST /policy/reports/{id}/review` |

## 4. The 5-minute recording

| Time | Scene |
|---|---|
| 0:00–0:30 | The problem and the approach: lakehouse first, then agents that recommend while people decide. The architecture diagram. |
| 0:30–1:30 | Loan Officer: customer search, enquiry, upload, extraction with quotes, a discrepancy, attestation. |
| 1:30–2:30 | The assessment: rule results, a thin-file or new-to-credit case where the score is skipped and not failed, policy citations, the explanation. |
| 2:30–3:30 | Assistant Manager: claim, approve exact terms, then the sanction letter, KFS (APR) and email. Show an override attempt without a reason being refused. |
| 3:30–4:15 | Simulated disbursement with maker-checker, and the confirmation email. |
| 4:15–5:00 | Branch Manager: a policy monitor finding from the real Bank of Baroda page, draft, golden cases, activation. Analytics. Close on "AI recommends, humans decide, everything is audited". |
