"""Validation of the rendered sample documents (files on disk against documents_truth and the dataset).

    backend/.venv/Scripts/python data/validate_docs.py [--dir data/out]
"""

import hashlib
import json
import re
from pathlib import Path

import pymupdf
from PIL import Image

from .render import NOTICE, dmy, inr, read

REAL_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def run(out: Path, only: set | None = None) -> list[tuple[str, bool, str]]:
    res: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        res.append((name, bool(ok), detail))

    docs = [d for d in read(out, "lh_truth", "documents_truth") if only is None or d["doc_id"] in only]
    cust = {r["customer_id"]: r for r in read(out, "lh_raw", "customers")}
    emp = {r["customer_id"]: r for r in read(out, "lh_raw", "employment") if r["is_current"] == "true"}
    docdir = out / "documents"
    manifest = json.loads((out / "documents_manifest.json").read_text(encoding="utf-8"))
    present = {p.name for p in docdir.iterdir()}
    expected = {d["file_name"] for d in docs}
    check(f"M1 {len(expected)} expected files exist, with the exact names in documents_truth, and no extras", present == expected, f"missing={sorted(expected - present)[:3]} extra={sorted(present - expected)[:3]}")
    check("M2 documents_manifest checksums and sizes match the files", all(
        hashlib.sha256((docdir / k).read_bytes()).hexdigest() == v["sha256"] and (docdir / k).stat().st_size == v["bytes"] for k, v in manifest["files"].items()) and len(manifest["files"]) == len(expected))

    bad_pages, bad_meta, bad_text, bad_scan, blank, bad_disc, real_pan, bad_fields = [], [], [], [], [], [], [], []
    for d in docs:
        path, q, t = docdir / d["file_name"], d["quality"], d["doc_type"]
        f = json.loads(d["expected_fields"])
        pages = int(d["page_count"])
        if path.suffix == ".pdf":
            with pymupdf.open(path) as pdf:
                if pdf.page_count != pages:
                    bad_pages.append(d["doc_id"])
                subj = pdf.metadata.get("subject") or ""
                if NOTICE not in subj:
                    bad_meta.append(d["doc_id"])
                text = norm(" ".join(pg.get_text() for pg in pdf))
                if q == "CLEAN":
                    if norm(NOTICE) not in text:
                        bad_text.append(d["doc_id"])
                    want = {"PAN_CARD": lambda: [f["name"], f["pan"], dmy(f["dob"])], "AADHAAR_MASKED": lambda: [f["name"], f["aadhaar_masked"], f["address"]],
                            "SALARY_SLIP": lambda: [f["employee_name"], f["employer"], inr(f["net"]), inr(f["gross"])], "ITR": lambda: [f["name"], f["pan"], inr(f["gross_total_income"])],
                            "BANK_STATEMENT": lambda: [f["account_holder"], f["account_id"], inr(f["closing_balance"]), inr(f["total_credits"]), inr(f["total_debits"])]}[t]()
                    if not all(norm(w) in text for w in want):
                        bad_fields.append(d["doc_id"])
                    if REAL_PAN.search(" ".join(pg.get_text() for pg in pdf)):
                        real_pan.append(d["doc_id"])
                    c = cust[d["customer_id"]]
                    disc = d["injected_discrepancy"]
                    shown = {"NAME_VARIANT": f.get("name"), "PAN_TYPO": f.get("pan"), "DOB_MISMATCH": f.get("dob")}.get(disc)
                    record = {"NAME_VARIANT": c["full_name"], "PAN_TYPO": c["pan"], "DOB_MISMATCH": c["date_of_birth"]}.get(disc)
                    if disc in ("NAME_VARIANT", "PAN_TYPO", "DOB_MISMATCH") and not (shown != record and norm(dmy(shown) if disc == "DOB_MISMATCH" else shown) in text):
                        bad_disc.append(d["doc_id"])
                    if disc == "INCOME_MISMATCH" and not (str(int(emp[d["customer_id"]]["declared_monthly_net"].split(".")[0])) != str(f.get("net", f.get("gross_total_income", 0) // 12 if t == "ITR" else None))):
                        bad_disc.append(d["doc_id"])
                elif q == "SCANNED_NOISE":
                    if text.strip() or not all(pg.get_images() for pg in pdf):
                        bad_scan.append(d["doc_id"])
        else:
            with Image.open(path) as im:
                im.load()
                if q == "SKEWED":
                    meta_ok = NOTICE in (im.text.get("Description", "") if hasattr(im, "text") else "")
                else:
                    meta_ok = NOTICE.encode() in (im.info.get("comment") or b"")
                if not meta_ok:
                    bad_meta.append(d["doc_id"])
                if im.height < 842 * 80 // 72 * pages or im.width < 400:
                    bad_pages.append(d["doc_id"])
                g = im.convert("L")
                if g.getextrema()[1] - g.getextrema()[0] < 100:
                    blank.append(d["doc_id"])
    check("M3 page counts agree with documents_truth (images: tall enough for every stacked page)", not bad_pages, str(bad_pages[:3]))
    check("M4 every file declares itself synthetic in its metadata", not bad_meta, str(bad_meta[:3]))
    check("M5 every clean PDF has a text layer carrying the synthetic notice", not bad_text, str(bad_text[:3]))
    check("M6 clean PDFs contain the expected field values exactly as in documents_truth", not bad_fields, str(bad_fields[:3]))
    check("M7 scanned PDFs have no text layer and one image per page", not bad_scan, str(bad_scan[:3]))
    check("M8 image files are not blank", not blank, str(blank[:3]))
    check("M9 injected discrepancies are visible in the rendered clean documents and differ from the customer record", not bad_disc, str(bad_disc[:3]))
    check("M10 no clean PDF contains anything in the real 10-character PAN format", not real_pan, str(real_pan[:3]))
    return res


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[1] / "out"))
    a = ap.parse_args(argv)
    results = run(Path(a.dir))
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} document checks passed")
    return 1 if failed else 0
