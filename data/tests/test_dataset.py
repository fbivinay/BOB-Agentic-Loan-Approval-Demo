"""Dataset generation, validation, reproducibility and negative tests.

Run from the repo root:  backend/.venv/Scripts/python -m pytest data/tests -q
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data"))
from generate import generate  # noqa: E402
from generator.validate import run  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ds")
    generate(out)
    return out


def failures(out: Path) -> list[str]:
    return [name for name, ok, _ in run(out) if not ok]


def test_full_validation_suite_passes(dataset):
    assert failures(dataset) == []


def test_approved_distribution_unchanged(dataset):
    m = json.loads((dataset / "manifest.json").read_text())
    assert m["segment_counts"] == {"ETB_EST": 440, "ETB_THIN": 80, "ETB_NTC": 80, "NTB_EST": 250, "NTB_THIN": 50, "NTB_NTC": 100}
    assert m["total_customers"] == 1000
    assert m["thin_file_scores"] == {"thin_total": 130, "score_present": 78, "score_null": 52, "present_by_relationship": {"ETB": 48, "NTB": 30}}


def test_reproducible_in_process(dataset, tmp_path):
    again = generate(tmp_path)
    first = json.loads((dataset / "manifest.json").read_text())
    assert again["canonical_sha256"] == first["canonical_sha256"]
    assert again["tables"] == first["tables"]


def test_reproducible_across_processes_and_hash_seeds(tmp_path):
    shas = []
    for i, hashseed in enumerate(("1", "4242")):
        out = tmp_path / f"p{i}"
        subprocess.run([sys.executable, str(ROOT / "data" / "generate.py"), "--out", str(out)], check=True, capture_output=True, env={**os.environ, "PYTHONHASHSEED": hashseed})
        shas.append(json.loads((out / "manifest.json").read_text())["canonical_sha256"])
    assert shas[0] == shas[1]


def test_volatile_metadata_is_outside_the_checksums(dataset):
    manifest_text = (dataset / "manifest.json").read_text()
    assert "generated_at" not in manifest_text
    assert json.loads((dataset / "run_info.json").read_text())["generated_at"]


def test_different_seed_changes_the_data(tmp_path):
    assert generate(tmp_path, seed=1)["canonical_sha256"] != generate(tmp_path / "b", seed=2)["canonical_sha256"]


# ---- negative tests: the suite must actually fail when the data is wrong
def tamper(dataset: Path, tmp_path: Path, table: str, fn) -> Path:
    out = tmp_path / "bad"
    shutil.copytree(dataset, out)
    p = next(out.rglob(f"{table}.csv"))
    p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")
    return out


def test_detects_real_format_pan(dataset, tmp_path):
    out = tamper(dataset, tmp_path, "customers", lambda s: re.sub(r"SYNP0*(\d{0,7})[A-Z]", "ABCDE1234F", s, count=1))
    assert any("PAN" in f for f in failures(out))


def test_detects_competing_customer_id_format(dataset, tmp_path):
    out = tamper(dataset, tmp_path, "customers", lambda s: s.replace("BOB-C0001", "CUST-0001", 1))
    assert any(f.startswith("D") for f in failures(out))


def test_detects_wrong_thin_score_count(dataset, tmp_path):
    def nuke(s):
        lines = s.splitlines()
        for i, line in enumerate(lines[1:], start=1):
            cols = line.split(",")
            if cols[2] == "THIN" and cols[3]:
                cols[3] = ""
                lines[i] = ",".join(cols)
                break
        return "\n".join(lines) + "\n"

    out = tamper(dataset, tmp_path, "bureau_profiles", nuke)
    assert any(f.startswith("F3") for f in failures(out))


def test_detects_repayment_account_owned_by_someone_else(dataset, tmp_path):
    import csv

    def swap(s):
        rows = list(csv.reader(s.splitlines()))
        j = next(k for k in range(2, len(rows)) if rows[k][-1] != rows[1][-1])  # a loan repaid from a different account
        rows[1][-1], rows[j][-1] = rows[j][-1], rows[1][-1]  # swap the two repayment accounts
        import io

        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        return buf.getvalue()

    out = tamper(dataset, tmp_path, "existing_loans", swap)
    assert any(f.startswith("G3") for f in failures(out))


def test_detects_external_emi_on_a_bank_of_baroda_account(dataset, tmp_path):
    import csv
    import io

    acc_rows = list(csv.DictReader((dataset / "lh_raw" / "accounts.csv").read_text().splitlines()))
    bob = next(r["account_id"] for r in acc_rows if r["is_bob"] == "true")

    def move(s):
        rows = list(csv.reader(s.splitlines()))
        head = rows[0]
        i = next(k for k, r in enumerate(rows[1:], 1) if r[head.index("category")] == "EMI")
        rows[i][head.index("account_id")] = bob
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        return buf.getvalue()

    out = tamper(dataset, tmp_path, "external_statement_lines", move)
    assert any(f.startswith("E6") for f in failures(out))


def test_detects_checksum_drift(dataset, tmp_path):
    out = tamper(dataset, tmp_path, "branches", lambda s: s.replace("Mumbai", "Mumbaj", 1))
    assert any(f.startswith("A1") for f in failures(out))


# ---- ground-truth isolation at code level (the cloud-level proof is scripts/test_truth_isolation.py)
def test_application_code_never_references_ground_truth():
    banned = re.compile(r"lh_truth|ground_truth|documents_truth|external_statement_lines|segment_code")
    hits = [str(p) for p in (ROOT / "backend" / "app").rglob("*.py") if banned.search(p.read_text(encoding="utf-8"))]
    assert hits == []
