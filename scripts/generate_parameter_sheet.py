"""Generate docs/GATE_B_PARAMETER_SHEET.md from the scheme YAML, so the review sheet can never drift from the rules the engine runs.

    backend/.venv/Scripts/python scripts/generate_parameter_sheet.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.policy import ruleset  # noqa: E402

OUT = ROOT / "docs" / "GATE_B_PARAMETER_SHEET.md"

# What search-engine excerpts of Bank of Baroda pages said during research (25 Nov 2026). The official pages returned HTTP 500,
# so none of this is verified, none of it is used by the engine, and it is shown only so the reviewer can compare.
EXCERPTS = {
    "age.min_years": "21",
    "age.max_at_maturity.salaried": "60 (another excerpt said 58)",
    "age.max_at_maturity.non_salaried": "65",
    "employment.min_current_tenure_months": "1 year employment / 1 year stable business",
    "loan.min_amount": "1.00 lakh metro and urban, 0.50 lakh rural and semi-urban",
    "loan.max_amount": "20.00 lakh, linked to occupation and account relationship",
    "tenure.max_months": "84 / 72 / 48-60 months by category",
    "bureau.min_score": "aggregators only: 650 to 750 (no official figure found)",
    "pricing.score_bands": "existing customers floating 12.15-15.75%; others 14.15-17.75% (CIBIL linked)",
    "pricing.ntb_premium_bp": "about 200 bp between the two excerpt bands",
    "fees.processing_bp": "1.00% to 2.00% plus GST (pages conflict)",
    "fees.processing_min": "1,000 plus GST",
    "fees.processing_max": "10,000 plus GST",
    "fees.penal_interest_percent_pa": "2%",
    "fees.prepayment_charge_percent": "nil (no pre-closure charges)",
    "exclusion.staff": "staff not eligible",
    "exclusion.nri": "NRI/PIO not eligible",
    "scope.pensioner_other_scheme": "a separate pensioner loan page exists",
}


def fmt(v) -> str:
    if isinstance(v, list) and v and "code" in v[0]:
        return "<br>".join(f"{d['label']} ({', '.join(d['applies']).lower()}" + (f"; waived if {', '.join(d['waived_if']).lower()}" if d['waived_if'] else "") + ")" for d in v)
    if isinstance(v, list):
        return "<br>".join(f"{b['min_score']}+: {b['rate_bp'] / 100:.2f}%" for b in v)
    if isinstance(v, bool):
        return "applies" if v else "off"
    return f"{v:,}" if isinstance(v, int) else str(v)


def build() -> str:
    rs = ruleset.load()
    c = rs.content
    lines = [
        "# Gate B: Demo Parameter Sheet (Standard Baroda Personal Loan)",
        "",
        f"Generated from `backend/app/policy/schemes/baroda_personal_loan.yaml`. Rule set **{rs.identity}**. Do not edit by hand: run `scripts/generate_parameter_sheet.py`.",
        "",
        "**Every value is a `DEMO_ASSUMPTION`.** Nothing has been verified against an official Bank of Baroda document, and nothing here is Bank of Baroda policy. "
        "The last column shows what unverified search excerpts said, for comparison only. The engine never reads that column, and the loader rejects any value labelled `UNVERIFIED_CANDIDATE`.",
        "",
        "Status: **Gate B closed on 26 Nov 2026** on the project owner's instruction to finish it, with the resolutions below. Values stay `DEMO_ASSUMPTION`; "
        "when an official document confirms one, it moves to `VERIFIED` through a new rule-set version activated by an authorised human.",
        "",
        "| Parameter | Demo value | Label | Meaning | Unverified excerpt (not used) |",
        "|---|---|---|---|---|",
    ]
    for name, p in c["parameters"].items():
        extra = f" On fail: {p['on_fail']}." if p.get("kind") == "EXCLUSION" else ""
        lines.append(f"| `{name}` | {fmt(p['value'])} | {p['confidence']} | {p.get('note', '')}{extra} | {EXCERPTS.get(name, '')} |")
    lines += ["", "## Segment overrides", "", "| Segment | Parameter | Value | Label | Why |", "|---|---|---|---|---|"]
    for seg, over in c.get("segment_overrides", {}).items():
        for name, p in over.items():
            lines.append(f"| {seg} | `{name}` | {fmt(p['value'])} | {p['confidence']} | {p.get('note', '')} |")
    lines += [
        "", "## How the engine uses them", "",
        "- **Recommendation:** any rule FAIL gives `POTENTIALLY_INELIGIBLE`; otherwise any NEEDS_REVIEW gives `REFER_FOR_FURTHER_REVIEW`; otherwise `RECOMMENDED_FOR_APPROVAL`. A human Assistant Manager or Branch Manager decides.",
        "- **Missing history is never a failure:** the score rule is SKIPPED when there is no score, and thin or no-history applicants are asked for six months of income evidence instead.",
        "- **Exclusions only refer:** staff, NRI and pensioner coverage are flagged for review, because they are unverified. They can fail a case only once VERIFIED.",
        "- **Capacity:** income used is the lower of declared and observed. The EMI room is the FOIR limit times income minus existing obligations, and the eligible amount is capped by the maximum loan and the income multiple and rounded down to the step.",
        "- **Pricing:** by score band, mid band when there is no score, plus the new-to-bank premium, clamped to the floor and ceiling.",
        "- **KFS:** the processing fee plus GST is deducted from the amount disbursed. APR is the IRR of the actual cash flows, shown nominal annualised (monthly x 12) with the effective annual rate beside it.",
        "",
        "## Gate B resolutions",
        "",
        "1. **Rate band kept at 10.50% to 19.00%.** It is in line with what Indian banks generally charge for unsecured personal loans. The excerpt figures are not copied in, because unverified Bank of Baroda figures must not be implemented.",
        "2. **Maximum tenure kept at 60 months**, the common market maximum for personal loans and the more conservative choice. Up to 84 months stays a candidate until verified.",
        "3. **One minimum loan of 50,000 for every branch**, because branch-tier minimums are unverified and would add a rule the demo cannot justify.",
        "4. **Pensioners are referred**, not assessed under this scheme, so that two schemes are never mixed.",
        "5. **APR shown nominal annualised** (monthly IRR x 12) with the effective annual rate beside it, both labelled on the KFS.",
        "6. **Required documents added** (`documents.required`), by employment type, with the two waivers an existing customer earns: current KYC, and income credited to their Bank of Baroda account.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT))
