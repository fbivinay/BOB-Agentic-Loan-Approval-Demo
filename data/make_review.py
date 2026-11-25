"""Build a local review package from the rendered documents: contact sheets plus a few sample files.

    backend/.venv/Scripts/python data/make_review.py

Output (gitignored): data/out/review/contact_sheet_*.png and data/out/review/samples/*.
"""

import csv
import shutil
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pymupdf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.render import FONT_DIR  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
THUMB_W, THUMB_H = 330, 470


def first_page(path: Path) -> Image.Image:
    if path.suffix == ".pdf":
        with pymupdf.open(path) as pdf:
            pix = pdf[0].get_pixmap(dpi=70)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    im = Image.open(path).convert("RGB")
    return im.crop((0, 0, im.width, round(im.width * 842 / 595)))  # first page of a stacked image


def main() -> None:
    docs = list(csv.DictReader(open(OUT / "lh_truth" / "documents_truth.csv", encoding="utf-8", newline="")))
    pick, seen = [], set()
    want = [("PAN_CARD", "CLEAN", "NONE"), ("PAN_CARD", "CLEAN", "PAN_TYPO"), ("PAN_CARD", "SKEWED", "NONE"), ("AADHAAR_MASKED", "CLEAN", "NAME_VARIANT"),
            ("AADHAAR_MASKED", "LOW_RES", "NONE"), ("SALARY_SLIP", "CLEAN", "NONE"), ("SALARY_SLIP", "CLEAN", "INCOME_MISMATCH"), ("SALARY_SLIP", "SCANNED_NOISE", "NONE"),
            ("ITR", "CLEAN", "NONE"), ("BANK_STATEMENT", "CLEAN", "NONE"), ("BANK_STATEMENT", "SKEWED", "NONE"), ("BANK_STATEMENT", "SCANNED_NOISE", "NONE")]
    for t, q, disc in want:
        d = next(x for x in docs if x["doc_type"] == t and x["quality"] == q and x["injected_discrepancy"] == disc and x["doc_id"] not in seen)
        seen.add(d["doc_id"])
        pick.append(d)
    review = OUT / "review"
    (review / "samples").mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONT_DIR / "Vera.ttf"), 12)
    for sheet in range(2):
        group = pick[sheet * 6:(sheet + 1) * 6]
        canvas = Image.new("RGB", (3 * (THUMB_W + 14) + 14, 2 * (THUMB_H + 46) + 14), (235, 235, 238))
        dr = ImageDraw.Draw(canvas)
        for i, d in enumerate(group):
            im = first_page(OUT / "documents" / d["file_name"])
            im.thumbnail((THUMB_W, THUMB_H))
            x, y = 14 + (i % 3) * (THUMB_W + 14), 14 + (i // 3) * (THUMB_H + 46)
            canvas.paste(im, (x, y + 34))
            dr.text((x, y), f"{d['doc_id']}  {d['doc_type']}", fill=(0, 0, 0), font=font)
            dr.text((x, y + 16), f"{d['quality']}  |  discrepancy: {d['injected_discrepancy']}  |  {d['file_name']}", fill=(80, 80, 80), font=font)
            shutil.copy(OUT / "documents" / d["file_name"], review / "samples" / d["file_name"])
        canvas.save(review / f"contact_sheet_{sheet + 1}.png")
    print("review package:", review)


if __name__ == "__main__":
    main()
