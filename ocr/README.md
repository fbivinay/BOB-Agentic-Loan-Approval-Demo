# Optional OCR batch (Kaggle only)

The application does **not** depend on this folder. It exists so real Unlimited-OCR output can be produced once, for demo documents, on a free Kaggle GPU.

- **Model:** the official Baidu Unlimited-OCR 2 (`baidu/Unlimited-OCR` on GitHub). No forks, quantized builds or other OCR engines, and no Hugging Face.
- **Weights:** downloaded once, inside the Kaggle notebook, from ModelScope (`PaddlePaddle/Unlimited-OCR-2`). The official GitHub README links to that page as the place the model is available, and the ModelScope page links back to the GitHub repo. See `kaggle/modelscope_manifest.json` for the provenance notes and SHA-256 of every file that will be executed or loaded.
- **Integrity:** the script verifies every file against the manifest and stops on any mismatch. The model repo contains Python files that are executed (`trust_remote_code`), which is why the check exists. Run it only in a notebook that holds no secrets.
- **Status:** written against the official README and **not yet run**. The first Kaggle run is its first test, and may fail (for example on bf16 support for the free GPU types). If it fails, the project keeps embedded-text extraction and manual review.
- **Output:** `results.jsonl`, one line per page. Imported later with the label `PRECOMPUTED_UNLIMITED_OCR`. Generated demo-style documents are synthetic, but the output is still not committed.

How to run: see the notebook cells at the top of `kaggle/run_unlimited_ocr.py`.

## Labels used for page text

| Label | Meaning |
|---|---|
| `EMBEDDED_TEXT` | Read from a born-digital PDF's text layer. Not OCR |
| `UNLIMITED_OCR` / `PRECOMPUTED_UNLIMITED_OCR` | Real Unlimited-OCR output, live or from a Kaggle batch |
| `MANUAL` | Keyed in by the Loan Officer |
| `PREPARED_DEMO_TEXT` | Generated demonstration values, shown as "Prepared demo data — not OCR". Never presented as extracted or verified content |
