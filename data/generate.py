"""Generate the synthetic dataset as canonical CSV files plus manifest.json.

    backend/.venv/Scripts/python data/generate.py [--seed N] [--out data/out]

Output is gitignored. Nothing is loaded anywhere: loading into BigQuery is a later phase.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.build import AS_OF, SEED, build  # noqa: E402
from generator.canonical import write_canonical  # noqa: E402


def generate(out: Path, seed: int = SEED) -> dict:
    tables, summary = build(seed)
    return write_canonical(tables, out, seed, AS_OF.isoformat(), summary)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    a = ap.parse_args()
    t0 = time.time()
    m = generate(Path(a.out), a.seed)
    print(f"{m['dataset_version']}  canonical_sha256={m['canonical_sha256']}  ({time.time() - t0:.1f}s)")
    for k, v in m["tables"].items():
        print(f"  {k:<40} {v['rows']:>8} rows")
