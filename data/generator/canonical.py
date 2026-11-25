"""Canonical serialisation and checksums.

Deterministic dataset content = the canonical CSV files and `manifest.json`. They contain no runtime timestamps,
hostnames, versions of Python or similar. Volatile execution metadata (when it ran, on what) goes to
`run_info.json`, which is deliberately outside every checksum.
"""

import csv
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from .schema import TABLES

GENERATOR_VERSION = "1.0.0"


def fmt(v, t: str) -> str:
    if v is None:
        return ""
    if t == "BOOL":
        return "true" if v else "false"
    if t == "MONEY":
        return f"{int(v)}.00"
    if t in ("BP", "PCT2"):
        return f"{int(v) // 100}.{int(v) % 100:02d}"
    if t == "DATE":
        return v.isoformat()
    if t == "INT64":
        return str(int(v))
    if t in ("JSON", "ARRAY_INT64"):
        return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(v)  # STRING, TIMESTAMP (already an ISO string)


def table_bytes(dataset: str, table: str, rows: list[dict]) -> bytes:
    pk, cols = TABLES[dataset][table]
    names = [c for c, _ in cols]
    types = dict(cols)
    out = []
    # ordering by primary key makes the file independent of generation order
    for row in sorted(rows, key=lambda r: tuple(str(r[k]) for k in pk)):
        extra = set(row) - set(names)
        if extra:
            raise ValueError(f"{table}: unexpected columns {sorted(extra)}")
        out.append([fmt(row.get(n), types[n]) for n in names])
    import io

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(names)
    w.writerows(out)
    return buf.getvalue().encode("utf-8")


def write_canonical(tables: dict[str, dict[str, list[dict]]], out_dir: Path, seed: int, as_of: str, summary: dict) -> dict:
    files = {}
    for ds, tbls in tables.items():
        (out_dir / ds).mkdir(parents=True, exist_ok=True)
        for name, rows in tbls.items():
            data = table_bytes(ds, name, rows)
            (out_dir / ds / f"{name}.csv").write_bytes(data)
            files[f"{ds}/{name}"] = {"rows": len(rows), "sha256": hashlib.sha256(data).hexdigest()}
    canonical = hashlib.sha256("\n".join(f"{k} {v['sha256']}" for k, v in sorted(files.items())).encode()).hexdigest()
    manifest = {
        "dataset_version": f"v{GENERATOR_VERSION}-seed{seed}-{canonical[:12]}",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of_date": as_of,
        "synthetic_notice": "All data is synthetic. No real person, PAN, Aadhaar, mobile number or account is represented.",
        "canonical_sha256": canonical,
        "tables": dict(sorted(files.items())),
        **summary,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Volatile metadata: written separately and excluded from all checksums by design.
    (out_dir / "run_info.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "note": "Execution metadata. Not part of the canonical dataset and not checksummed.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest
