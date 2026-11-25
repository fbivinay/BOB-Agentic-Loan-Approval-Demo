"""Render the ~480 synthetic sample documents from data/out (run data/generate.py first).

    backend/.venv/Scripts/python data/render_documents.py [--dir data/out]

Output (gitignored): data/out/documents/*.{pdf,png,jpg,scan.pdf}, documents_manifest.json.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.render import render_all  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent / "out"))
    a = ap.parse_args()
    t0 = time.time()
    m = render_all(Path(a.dir))
    print(f"{m['document_count']} documents, documents_sha256={m['documents_sha256']} ({time.time() - t0:.1f}s)")
