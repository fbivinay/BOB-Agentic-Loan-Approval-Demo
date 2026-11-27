"""Optional, Kaggle-only batch run of the official Baidu Unlimited-OCR 2 model.

The application never depends on this. It exists to produce real OCR output for demo documents,
which is later imported with the label PRECOMPUTED_UNLIMITED_OCR.

What it does:
  1. Refuses to run anywhere except a Kaggle notebook with a GPU.
  2. Downloads the official weights once from ModelScope (PaddlePaddle/Unlimited-OCR-2), the location
     listed in the official baidu/Unlimited-OCR README. No Hugging Face.
  3. Checks every downloaded file against ocr/kaggle/modelscope_manifest.json (SHA-256) and stops on any
     mismatch, so only the code and weights that were inspected are ever executed.
  4. Runs the official `infer_multi` API on page windows of at most 10 pages and writes one JSON line per page.

Written against the official README. It has NOT been run: no GPU or weights are available to the project owner
locally, so the first Kaggle run is also its first test.

Kaggle notebook cells (internet and a GPU must be enabled in the notebook settings). Kaggle's preinstalled torch is used: reinstalling it
costs minutes and a multi-GB download.
  !pip install -q modelscope pillow transformers==4.52.1 tokenizers==0.21.4 \
      huggingface-hub==0.36.0 addict==2.4.0 einops==0.8.2 easydict==1.13 pymupdf==1.27.2.2
  # upload this script, modelscope_manifest.json and your documents as a Kaggle dataset, then:
  !python run_unlimited_ocr.py --input /kaggle/input/<your-dataset>/docs --output /kaggle/working/ocr_out
  # later runs: add a previous run's output as an input and pass --weights <its unlimited-ocr-2 folder> to skip the download
  # (the files are still checked against the manifest)
Precision: bfloat16 on GPUs that support it natively (compute capability 8+), float16 on the free T4 and P100, which only emulate bfloat16.
Do not put any API key or secret into the notebook. It never needs one.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

MODEL_ID = "PaddlePaddle/Unlimited-OCR-2"
WINDOW = 10  # pages per request, as in the official serving setup
DPI = 300
IMAGE_EXT = {".png", ".jpg", ".jpeg"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(model_dir: Path, manifest: dict) -> None:
    bad = []
    for name, meta in manifest["files"].items():
        p = model_dir / name
        if not p.exists():
            bad.append(f"missing {name}")
        elif p.stat().st_size != meta["size"] or sha256(p) != meta["sha256"]:
            bad.append(f"changed {name}")
    if bad:
        sys.exit("Weights or code do not match the inspected manifest, refusing to execute:\n  " + "\n  ".join(bad))


def to_images(doc: Path, tmp: Path) -> list[Path]:
    """One image per page. PDFs are rendered at 300 DPI with PyMuPDF, as in the official README."""
    if doc.suffix.lower() in IMAGE_EXT:
        return [doc]
    import fitz  # PyMuPDF

    out = []
    with fitz.open(doc) as pdf:
        for i, page in enumerate(pdf, start=1):
            p = tmp / f"{doc.stem}_p{i:04d}.png"
            page.get_pixmap(dpi=DPI).save(p)
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="folder of PDFs and images")
    ap.add_argument("--output", required=True)
    ap.add_argument("--manifest", default=str(Path(__file__).with_name("modelscope_manifest.json")))
    ap.add_argument("--weights", help="folder holding already-downloaded weights (e.g. a previous run's output); skips the download")
    args = ap.parse_args()

    if not Path("/kaggle/working").exists():
        sys.exit("This script is Kaggle-only by project decision. Not running here.")
    import torch

    if not torch.cuda.is_available():
        sys.exit("No GPU: enable a GPU in the Kaggle notebook settings.")

    manifest = json.load(open(args.manifest, encoding="utf-8"))
    if args.weights:
        model_dir = Path(args.weights)
        print("Using downloaded weights from", model_dir, flush=True)
    else:
        from modelscope import snapshot_download

        model_dir = Path(
            snapshot_download(
                MODEL_ID,
                revision=manifest["revision"],
                local_dir="/kaggle/working/unlimited-ocr-2",
                allow_patterns=["*.json", "*.py", "*.safetensors"],  # skips the demo assets
            )
        )
    verify(model_dir, manifest)
    print("Manifest check passed:", len(manifest["files"]), "files", flush=True)

    from transformers import AutoModel, AutoTokenizer

    dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16  # T4/P100 only emulate bfloat16: very slow
    print("GPU", torch.cuda.get_device_name(0), "dtype", dtype, flush=True)
    tok = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        model_dir, trust_remote_code=True, use_safetensors=True, torch_dtype=dtype
    ).eval().cuda()
    if dtype is torch.float16:
        # The model's own image preprocessing hard-codes .to(torch.bfloat16). Its file is pinned by the manifest and is not edited: instead,
        # the model's own modules alone see torch.bfloat16 as float16, so the images match the float16 weights.
        import types

        class _Fp16Torch(types.ModuleType):
            def __getattr__(self, name):
                return getattr(torch, name)

        shim = _Fp16Torch("torch")
        shim.bfloat16 = torch.float16
        for name, mod in list(sys.modules.items()):
            if name.startswith("transformers_modules.") and getattr(mod, "torch", None) is torch:
                mod.torch = shim
    print("Model loaded", flush=True)

    out_dir = Path(args.output)
    tmp = out_dir / "_pages"
    tmp.mkdir(parents=True, exist_ok=True)
    docs = sorted(p for p in Path(args.input).iterdir() if p.suffix.lower() in IMAGE_EXT | {".pdf"})
    summary = []
    with open(out_dir / "results.jsonl", "w", encoding="utf-8") as results:
        for doc in docs:
            pages = to_images(doc, tmp)
            for start in range(0, len(pages), WINDOW):
                window = pages[start : start + WINDOW]
                torch.cuda.reset_peak_memory_stats()
                t0 = time.time()
                text, num_tokens = model.infer_multi(
                    tok, image_files=[str(p) for p in window], output_path=str(out_dir / "raw"), save_results=True
                )
                secs = time.time() - t0
                parts = [s.strip() for s in text.split("<PAGE>") if s.strip()]
                ok = len(parts) == len(window)  # the model emits one <PAGE> per input page
                for i, page_text in enumerate(parts if ok else [text]):
                    results.write(
                        json.dumps(
                            {
                                "document": doc.name,
                                "page_no": start + i + 1,
                                "text": page_text,
                                "extraction_method": "UNLIMITED_OCR",
                                "engine": "Unlimited-OCR-2 (ModelScope PaddlePaddle/Unlimited-OCR-2)",
                                "page_split_ok": ok,
                                "window_pages": len(window),
                                "window_seconds": round(secs, 2),
                                "num_tokens": int(num_tokens),
                                "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                summary.append({"document": doc.name, "window_start": start + 1, "pages": len(window), "seconds": round(secs, 2), "page_split_ok": ok})
                print(summary[-1], flush=True)
    json.dump(summary, open(out_dir / "summary.json", "w"), indent=1)
    print("Done. Download", out_dir / "results.jsonl", "and import it later; do not commit it if it holds personal-looking data.")


if __name__ == "__main__":
    main()
