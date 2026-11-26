# Research note: agentic AI for personal-loan processing at a bank branch

*Bank of Baroda, standard personal loan. Demo built on synthetic data; findings as of 26 November 2026.*

## 1. The problem

A branch personal loan passes through many hands:
- the Loan Officer collects documents and keys values;
- someone checks names, dates of birth, PAN and income across documents;
- a manager weighs the bureau report, bank statement and obligations, and decides;
- the branch issues a sanction letter and Key Facts Statement, collects acceptance, and disburses.

**Where the time goes:** mostly checking documents and assembling evidence. In the demo's synthetic history of 1,902 decided applications (2024–2026), the average turnaround is 5.6 days and 70.9% are approved.

**What AI can speed up:** reading, checking and assembling. The decision should remain a person's, and that is a regulatory expectation as well as a design choice.

## 2. Regulatory frame (what the demo follows)

- **Current rules:** on 28 November 2025 RBI consolidated thousands of circulars into function-wise Master Directions. For a commercial bank, the rules that touch a personal loan now sit mainly in the *Commercial Banks – Responsible Business Conduct Directions, 2025* and the *Know Your Customer Directions, 2025*, updated to 1 October 2026. The older stand-alone KFS (April 2024) and penal-charges (August 2023) circulars are repealed.
- **Key Facts Statement** (paragraph 348):
  - a unique proposal number;
  - validity of at least three working days;
  - an APR computation sheet (IRR on the net disbursed amount, reducing balance) and the amortisation schedule;
  - no charge that is not in the KFS.
  - The demo reproduces RBI's worked example: ₹20,000 at 15% over 24 EMIs with ₹400 charges gives an APR of 17.07%.
- **Penal charges** (paragraph 351): charges on the amount in default, not interest added to the rate. They are never capitalised, and no further penal charges are levied on unpaid penal charges.
- **KYC:** customer due diligence with an officially valid document and PAN, with periodic updation. An expired KYC is referred in the demo, not declined.
- **Domains:** Bank of Baroda's site now redirects to `bankofbaroda.bank.in`.

## 3. Bank of Baroda's own figures, compared with the demo's assumptions

The policy monitor read the official product, rate and charges pages on 26 November 2026.

| Item | Official page | Demo assumption | Status |
|---|---|---|---|
| Minimum age; maximum age at the end of repayment | 21; 60 (salaried), 65 (others) | same | Can be marked `VERIFIED` |
| Loan amount | ₹1 lakh (metro and urban) or ₹50,000 (rural and semi-urban), up to ₹20 lakh by occupation and relationship | ₹50,000 to ₹20 lakh | The metro and urban minimum differs |
| Repayment period | Up to 84 months, depending on category | 60 months | Differs |
| Prepayment, fixed-rate loan | 3% + GST on amounts prepaid in the first 3 years | 0% | Differs |
| Pricing | Repo-linked rate (repo 5.50%) or MCLR (1 year 8.85% from 12.11.2026) plus a spread | A score-band table (10.50%–19.00%) | Recorded, not compared |

No rule was changed automatically. Each difference is a proposal for the Branch Manager.

## 4. Approach

1. **Lakehouse first.**
   - Synthetic bank, bureau and KYC data are loaded into BigQuery.
   - The curated and gold layers give a customer 360 and credit features (salary regularity, obligations, days past due).
   - The generator's ground truth is isolated in a dataset the application cannot read, so the AI is evaluated, not trained, on it.
2. **Deterministic core.**
   - Eligibility, pricing, EMI and APR are plain code with versioned, labelled parameters.
   - Every rule result says whether it rests on a `DEMO_ASSUMPTION` or a `VERIFIED` figure.
   - Thin-file and new-to-credit applicants are assessed on the evidence that exists; a missing score is skipped, not failed.
3. **Agents where judgement-free reading helps.**
   - A document graph: one labelled text method, extraction that quotes its source, deterministic discrepancy checks.
   - An assessment graph: evidence, engine, policy retrieval with citations, and a checked explanation.
   - A daily monitor that reads official sources and proposes rule reviews.
4. **Human control, enforced by the database:** decisions, approved terms, verification, rule activation and disbursement release. Overrides need reasons, and everything is audited.

## 5. Constraints and what they taught

- **OCR:** the official `baidu/Unlimited-OCR` needs an NVIDIA GPU, which a zero-cost budget cannot host live.
  - The demo uses PDF text layers where they exist, labelled as not OCR.
  - It imports real Unlimited-OCR output from an optional free Kaggle batch.
  - Otherwise it falls back to manual keying, never to a different engine.
  - The lesson: say exactly how each value was read.
- **Free tiers:**
  - BigQuery's sandbox has no DML: hence load jobs and views.
  - Render sleeps: hence warm-up and a scheduled drain.
  - Gemini's free tier may use inputs: hence synthetic and public text only, with PANs redacted.
  - Resend allows no custom domain: hence a test inbox.
- **Model output is checked, never trusted.**
  - Extraction values must appear in their quotes.
  - Explanations may not invent numbers or decide.
  - Fact extraction from official pages dropped values whose quotes were not in the source.

## 6. Recommendations for a real rollout

1. Keep the decision human. Measure the AI on time saved in checking and on discrepancy recall, not on approval rates.
2. Make the policy monitor's fact-to-rule map part of the credit policy change process, with compliance review of every `COMPLIANCE_ITEM`.
3. Replace lexical retrieval with hybrid retrieval over the bank's own policy library, keeping citations to the paragraph.
4. Host OCR in-region on approved infrastructure, and keep the same labelling of how each value was read.
5. Add cryptographic actor attestation between the frontend, backend and database, which the demo documents as a limit.

## Sources

- Reserve Bank of India, Master Directions index and the *Commercial Banks – Responsible Business Conduct* (id 13140), *Know Your Customer* (id 13141) and *Credit Risk Management* (id 13153) Directions, 2025: https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx
- News on AIR, "RBI repeals over 9,000 circulars, consolidates rules into 244 Master Directions" (28 Nov 2025): https://www.newsonair.gov.in/rbi-repeals-9000-circulars-consolidates-rules-into-244-master-directions-to-ease-compliance/
- Bank of Baroda, Baroda Personal Loan: https://bankofbaroda.bank.in/loans/personal-loan/baroda-personal-loan
- Bank of Baroda, Retail Loans Interest Rates: https://bankofbaroda.bank.in/interest-rate-and-service-charges/retail-loans-interest-rates
- Bank of Baroda, Service Charges: https://bankofbaroda.bank.in/interest-rate-and-service-charges/service-charges
- baidu/Unlimited-OCR (official repository): https://github.com/baidu/Unlimited-OCR
