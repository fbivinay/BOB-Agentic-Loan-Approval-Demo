# Responsible AI and governance note

## What the AI does, and what it may not do

| The AI may | The AI may not (each enforced in the database, with a negative test) |
|---|---|
| Read documents' text and propose field values, each with the page and the exact quote | Verify a document, or overwrite a value a person entered |
| Find discrepancies with deterministic rules | Approve, decline or refer an application |
| Assemble evidence and run the deterministic policy engine | Change a rule, a calculation, eligible terms or approved terms |
| Retrieve policy passages from official sources, with citations | Activate a rule set, or mark a value `VERIFIED` |
| Recommend: `RECOMMENDED_FOR_APPROVAL`, `REFER_FOR_FURTHER_REVIEW`, `POTENTIALLY_INELIGIBLE` | Trigger a sanction, an email of its own accord, or a disbursement |
| Explain the recommendation in plain words | Use a number the engine did not produce, or speak as the decider |
| Extract facts from changed official documents and propose rule reviews | Decide that a change affects a rule (a human-maintained map does) |

**The recommendation comes from the deterministic engine, never from the model.** Gemini writes only the explanation, and that text is discarded for a fixed template if:
- it contains any number not in the engine's output;
- it uses decision language;
- the service is unavailable or over budget.

## Human oversight

- **Every decision is a person's:** an Assistant or Branch Manager who holds the file.
  - The database records the AI recommendation snapshot and whether the decision overrides it.
  - An override needs a written reason and writes a `DECISION_OVERRIDE` audit event.
- **Every document value** that becomes evidence is confirmed or keyed by the Loan Officer. A value whose quote is not on its page is flagged and must be confirmed. Prepared demo data is never evidence.
- **Maker-checker:**
  - Loan Officers cannot decide.
  - Whoever recorded the customer's acceptance or worked on the pre-disbursement checklist cannot release the disbursement.
  - A Branch Manager takes over another reviewer's file only with a recorded reason.
- **Policy changes:**
  1. The monitor writes reports and proposals.
  2. A Branch Manager drafts a candidate.
  3. Golden cases run against it.
  4. Activation needs a reason and passing tests.
  5. Applications in progress keep the rule set frozen at their intake.

## Fairness in the rules

- **No credit history is never a failure.** For new-to-credit and thin-file applicants, the score rule is `SKIPPED` and the evidence that exists (income, bank statement, repayment on any tradelines, FOIR) carries the assessment.
- **Referrals happen only for insufficient, inconsistent or judgement cases**, and each states why.
- **Staff, NRI and pensioner exclusions** can only refer, never fail, until they are `VERIFIED` against the official scheme text.
- **Every limit that can make a case potentially ineligible** says it is a demo assumption, not Bank of Baroda policy.

## Transparency

- **Each assessment stores:**
  - the 15 rule results with messages and confidence labels (`DEMO_ASSUMPTION` or `VERIFIED`);
  - the evidence with its sources;
  - the policy citations (URL, version, fetch time, quote);
  - the agent's steps, with the data each step used.
- **Each extracted value** carries its method (`EMBEDDED_TEXT`, `PRECOMPUTED_UNLIMITED_OCR`, `MANUAL`, or "Prepared demo data — not OCR") and its quote.
- **Every screen, document and email** says "AI recommendation, human decides" where a recommendation appears. Demo documents and emails say they are not from Bank of Baroda and not a loan offer.

## Data handling

- **Synthetic data only.** Customer IDs are `BOB-C0001`. PANs use the 12-character form `SYNP#######X`, which cannot be a real PAN. Email addresses use the reserved `example.invalid` domain.
- **Synthetic PANs never reach an external service:**
  - Gemini receives placeholders such as `[PAN-1]`, restored only on the backend.
  - The sanction pack and the emails carry no PAN or mobile number, and rendering refuses if one appears.
- **Only synthetic text and public official documents go to Gemini**, because the free tier may use submitted content.
- **All email goes to a test inbox.** The database refuses an `example.invalid` recipient.
- **Analytics events** carry workflow facts only: no name, PAN, mobile, email or address.
- **The generator's ground truth (`lh_truth`)** is unreachable by the app and agent identities, enforced in BigQuery and tested.

## Known limits

- **The backend connects as a privileged database role** and tells the database who is acting. A compromised backend could act as a real member of staff. The human-only guards rely on explicit columns, but not on cryptographic proof of the actor.
- **Retrieval is lexical (BM25)**, so some rules find no passage above the relevance floor, and the reviewer sees "no matching passage".
- **Fact extraction quality depends on the model.** Values whose quote is not in the source are dropped, but a correct-looking quote with a misread condition is possible. The Branch Manager reviews every proposal.
- **The one-click demo logins are public**, so anyone can change the synthetic state. Budgets cap cost, and `scripts/reset_demo.py` restores a clean state.
- **All rates, fees and limits are demo assumptions** unless marked `VERIFIED`. The policy monitor has found real differences: the minimum loan at metro branches, an 84-month tenure, and a 3% prepayment charge on fixed-rate loans. These await a person's review.
