# Gate C: OCR Feasibility Under the Zero-Cost, No-Download Decisions

Status: **result recorded at the end of Phase 0. Decision needed from the project owner (section 4).**

## 1. Result

**The official Baidu Unlimited-OCR model cannot be run, on any host, without obtaining its weights.** The official README loads them with `from_pretrained("baidu/Unlimited-OCR-2")`, and lists two official locations:

- the Hugging Face Hub (`baidu/Unlimited-OCR-2`), and
- ModelScope (`PaddlePaddle/Unlimited-OCR-2`).

The project decisions are: no Hugging Face, no ZeroGPU, no model-weight downloads, no forks or alternative engines, and no spending. Together these mean **no real Unlimited-OCR output can be produced by this project**, whether locally, on Kaggle, or anywhere else. Kaggle's free GPU does not change this, because the notebook would still have to fetch the weights (a Kaggle-hosted copy of the model was not found and is unverified).

Consequences for the plan:

- **`PRECOMPUTED_UNLIMITED_OCR` cannot be generated.** It would require running the model once. It stays defined in the schema but unused unless the decision changes.
- **Live OCR is unavailable.** The OCR client interface and the `AWAITING_OCR` job state are still built, so an endpoint can be plugged in later without changing anything else.
- **No other OCR engine is substituted.** Nothing is passed off as OCR.

## 2. What the system does instead (all free, all honestly labelled)

| Situation | Method | Label on every page row |
|---|---|---|
| Born-digital PDF (RBI and Bank of Baroda policy PDFs, and our own generated sample documents) | Read the PDF's embedded text layer | `EMBEDDED_TEXT` |
| Scanned image or scanned PDF with no text layer | Job waits in `AWAITING_OCR`, then `NEEDS_MANUAL_REVIEW`: the Loan Officer keys values from the document image | `MANUAL` |
| Demo documents meant to look scanned or degraded | **Proposed, needs approval:** the generator's known text is stored as prepared demo text so the downstream flow (extraction, review, discrepancies) can be shown | `PREPARED_DEMO_TEXT` |

`PREPARED_DEMO_TEXT` is **not OCR output** and would be shown as "prepared demo data, not OCR" wherever it appears. Without it, scanned-looking documents in the demo can only go through manual review.

Every row keeps the source document, version and page number.

## 3. What was checked in Phase 0

- Hardware: no NVIDIA GPU locally (AMD graphics only), about 15 GB RAM. The official code is CUDA-only, and no CPU path is documented.
- Free hosts: no free always-on GPU is available. Cloud Run GPU and rented GPUs need billing, and Hugging Face is excluded by decision.
- Kaggle free GPU: available in principle (about 30 GPU hours per week, unconfirmed), but only useful if weights may be fetched.
- No model files were downloaded, and no OCR test was run.

## 4. Decision needed

Choose one:

1. **Stay as is** (current default): embedded text, manual review, and optionally `PREPARED_DEMO_TEXT` for degraded demo documents. Live OCR is documented as a limitation.
2. **Allow a one-time weights download on Kaggle only** (from ModelScope, which the official README lists, or the Hugging Face Hub), to run the official model in a notebook on a folder of demo documents and import the results as `PRECOMPUTED_UNLIMITED_OCR`. The application itself never depends on it.

And separately: approve or reject the `PREPARED_DEMO_TEXT` label (section 2).
