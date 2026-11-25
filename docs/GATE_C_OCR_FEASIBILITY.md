# Gate C: OCR Feasibility Under the Zero-Cost Decisions

Status: **route approved conditionally (Kaggle only, optional). Not yet run. The application does not depend on it.**

## 1. Decisions in force

- Official `baidu/Unlimited-OCR` only. No forks, quantized builds or alternative OCR engines.
- No Hugging Face and no ZeroGPU.
- One-time weights download allowed from the official ModelScope repository, only after verifying it is the official upstream source, and only for a Kaggle free-GPU run.
- Kaggle is optional and never a dependency of the application. If the route fails, the project keeps manual review and embedded-text extraction.
- Strictly zero cost.

## 2. Source verification (done 2026-11-25)

The weights location is `https://modelscope.cn/models/PaddlePaddle/Unlimited-OCR-2`.

| Evidence | Finding |
|---|---|
| Official GitHub README (`baidu/Unlimited-OCR`, owned by the GitHub-verified `baidu` organisation) | Links to this exact ModelScope model as where "our model is now available" |
| ModelScope model README | Links back to `github.com/baidu/Unlimited-OCR`, shows Baidu branding, license MIT |
| ModelScope metadata | `FromSite: github`, architecture `UnlimitedOCRForCausalLM`, two safetensors shards totalling 6.69 GB (consistent with a ~3B model in bf16), and `modeling_unlimitedocr.py` present |

**Caveats:**
- The ModelScope namespace is `PaddlePaddle` (Baidu's framework organisation), not `baidu`, and that organisation is not GitHub-verified. The link chain from Baidu's own README is the main evidence.
- No independent upstream hash exists (the Hugging Face copy is not consulted). The SHA-256 values in `ocr/kaggle/modelscope_manifest.json` come from ModelScope's own metadata, so they detect later changes or corruption but are not an independent signature.
- The model repo contains ten Python files that run on load (`trust_remote_code`). The script therefore executes nothing until every file matches the manifest, and the notebook must hold no secrets.

Conclusion: strong evidence that this is the official upstream distribution, short of a cryptographic proof.

## 3. What was prepared

`ocr/kaggle/run_unlimited_ocr.py` runs the official `infer_multi` API on page windows of at most 10 pages, after the manifest check, and writes `results.jsonl` (one line per page). It refuses to run anywhere except a Kaggle notebook with a GPU. It was written against the official README and **has not been run**. The first Kaggle run is its first test, and it may fail (for example on bf16 support for the free GPU types, or on package versions).

Nothing was downloaded to this machine, and no OCR test has been run. The run needs documents, so it happens after the sample documents exist (Phase 4 onward).

## 4. What the system does instead, labelled honestly

| Situation | Method | Label on every page row |
|---|---|---|
| Born-digital PDF (policy PDFs, our generated sample PDFs) | Read the PDF's embedded text layer | `EMBEDDED_TEXT` (not OCR) |
| Real Unlimited-OCR output from a Kaggle batch | Imported results | `PRECOMPUTED_UNLIMITED_OCR` |
| Scanned image or PDF with no text layer, and no batch result | Job waits in `AWAITING_OCR`, then `NEEDS_MANUAL_REVIEW`: the Loan Officer keys values from the image | `MANUAL` |
| Degraded-looking demo documents, for demonstrating the downstream flow | Generated demonstration values | `PREPARED_DEMO_TEXT`, always shown as **"Prepared demo data — not OCR"** |

`PREPARED_DEMO_TEXT` (approved) is used only for generated demonstration values. It is never represented as extracted or verified document content, and it never feeds an assessment as verified evidence.

Every row keeps the source document, version and page number.

## 5. Hardware and hosting (unchanged)

No NVIDIA GPU locally (AMD graphics only), about 15 GB RAM, and no free always-on GPU host. Cloud Run GPU and rented GPUs need billing, and Hugging Face is excluded. Kaggle's free GPU (about 30 GPU hours per week, unconfirmed) is the only option, and it is a manual batch.
