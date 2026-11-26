"""Document text and field extraction.

Text, from exactly one labelled method (never another OCR engine, never a silent substitution):
  EMBEDDED_TEXT              the PDF's own text layer, for born-digital PDFs. Not OCR, never labelled as OCR.
  PRECOMPUTED_UNLIMITED_OCR  real output of the official baidu/Unlimited-OCR from an imported Kaggle batch for this exact file
  PREPARED_DEMO_TEXT         generated demonstration values for degraded demo documents: "Prepared demo data — not OCR", never evidence
  MANUAL                     the Loan Officer keys values from the document image
Scans with no text layer and no imported OCR wait (AWAITING_OCR), are retried, and then go to manual review.

Fields come from the Gemini Developer API (free tier). Synthetic PANs are replaced with placeholders before any text leaves the backend
and restored afterwards. The model must quote the snippet each value came from; a value whose snippet is not on the cited page is kept but
flagged for the reviewer. Values are normalised (dates to ISO, amounts to plain numbers). The model only extracts: it decides nothing.
"""

import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

PROMPT_VERSION = "extract-v1"
TEXT_METHODS = ("EMBEDDED_TEXT", "UNLIMITED_OCR", "PRECOMPUTED_UNLIMITED_OCR", "MANUAL", "PREPARED_DEMO_TEXT")
MIN_CHARS_PER_PAGE = 20  # below this a PDF page is treated as a scan with no usable text layer

# field -> (kind, description). Kinds: text, date, amount, int, pan
FIELDS: dict[str, dict[str, tuple[str, str]]] = {
    "PAN_CARD": {"name": ("text", "card holder's name"), "father_name": ("text", "father's name"), "dob": ("date", "date of birth"), "pan": ("pan", "PAN")},
    "AADHAAR_MASKED": {"name": ("text", "name"), "dob": ("date", "date of birth"), "gender": ("text", "gender"),
                       "aadhaar_masked": ("text", "masked Aadhaar number as printed, e.g. XXXX XXXX 1234"), "address": ("text", "address")},
    "PASSPORT": {"name": ("text", "name"), "dob": ("date", "date of birth"), "passport_number": ("text", "passport number"), "address": ("text", "address")},
    "VOTER_ID": {"name": ("text", "name"), "epic_number": ("text", "EPIC number"), "address": ("text", "address")},
    "SALARY_SLIP": {"employee_name": ("text", "employee name"), "employer": ("text", "employer"), "month": ("text", "pay month as YYYY-MM"),
                    "gross": ("amount", "gross monthly pay"), "deductions": ("amount", "total deductions"), "net": ("amount", "net monthly pay")},
    "ITR": {"assessment_year": ("text", "assessment year, e.g. 2026-27"), "name": ("text", "assessee name"), "pan": ("pan", "PAN"),
            "gross_total_income": ("amount", "gross total income for the year")},
    "BANK_STATEMENT": {"account_holder": ("text", "account holder"), "institution": ("text", "bank"), "account_id": ("text", "account number"),
                       "period": ("text", "statement period"), "opening_balance": ("amount", "opening balance"), "closing_balance": ("amount", "closing balance"),
                       "total_credits": ("amount", "total credits"), "total_debits": ("amount", "total debits"),
                       "income_credit_count": ("int", "number of salary, pension or business-receipt credits"),
                       "income_credit_total": ("amount", "total of salary, pension or business-receipt credits"),
                       "emi_debit_count": ("int", "number of loan EMI debits"), "emi_debit_total": ("amount", "total of loan EMI debits")},
    "BUSINESS_PROOF": {"business_name": ("text", "business name"), "registration_number": ("text", "registration or GSTIN number"),
                       "proprietor_name": ("text", "proprietor or partner name")},
    "PENSION_ORDER": {"pensioner_name": ("text", "pensioner name"), "ppo_number": ("text", "pension payment order number"), "monthly_pension": ("amount", "monthly pension")},
    "APPLICATION_FORM": {"applicant_name": ("text", "applicant name"), "requested_amount": ("amount", "loan amount applied for"),
                         "signed": ("text", "whether the form is signed: yes or no")},
    "PHOTOGRAPH": {},
}


class ExtractedField(BaseModel):
    field: str
    value: str | None = Field(description="the value exactly as printed, or null if it is not on the document")
    page: int = Field(ge=1)
    snippet: str = Field(description="the exact text on that page the value was read from")


class Extraction(BaseModel):
    fields: list[ExtractedField]


# ---------------------------------------------------------------- text
def embedded_text(pdf: bytes) -> list[str] | None:
    """Page texts from a PDF's own text layer, or None if any page lacks one (a scan): those go to OCR, never passed off as extracted."""
    from pypdf import PdfReader

    try:
        pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]
    except Exception:
        return None
    if not pages or any(len(re.sub(r"\s", "", t)) < MIN_CHARS_PER_PAGE for t in pages):
        return None
    return pages


# ---------------------------------------------------------------- synthetic PAN protection
PAN_RE = re.compile(r"\bSYNP[0-9]{7}[A-Z]\b")


def redact(pages: list[str]) -> tuple[list[str], dict[str, str]]:
    """Replace every synthetic PAN with a placeholder; the mapping stays on the backend."""
    mapping: dict[str, str] = {}

    def sub(m):
        pan = m.group(0)
        for k, v in mapping.items():
            if v == pan:
                return k
        k = f"[PAN-{len(mapping) + 1}]"
        mapping[k] = pan
        return k

    return [PAN_RE.sub(sub, p) for p in pages], mapping


def restore(s: str | None, mapping: dict[str, str]) -> str | None:
    if s is None:
        return None
    for k, v in mapping.items():
        s = s.replace(k, v)
    return s


# ---------------------------------------------------------------- normalisation
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y")


def normalise(kind: str, raw: str | None) -> tuple[str | None, str | None]:
    """(value, problem). Unparseable values are kept as printed, with a note for the reviewer."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    if kind == "date":
        for f in DATE_FORMATS:
            try:
                return datetime.strptime(s, f).date().isoformat(), None
            except ValueError:
                pass
        return s, "date not in a recognised format"
    if kind in ("amount", "int"):
        t = re.sub(r"(?i)^(rs\.?|inr|₹)\s*", "", s).replace(",", "").replace(" ", "")
        if not re.fullmatch(r"\d{1,13}(\.\d{1,2})?", t):  # plain digits only: no sign, exponent, NaN or Infinity
            return s, "not a number"
        try:
            d = Decimal(t)
        except InvalidOperation:
            return s, "not a number"
        if kind == "int":
            return (str(int(d)), None) if d == int(d) else (s, "not a whole number")
        return (str(d.quantize(Decimal("0.01"))).removesuffix(".00"), None)
    if kind == "pan":
        p = s.upper().replace(" ", "")
        return p, None if PAN_RE.fullmatch(p) else "not in the synthetic PAN format"
    return re.sub(r"\s+", " ", s), None


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def printed_in(raw: str | None, snippet: str) -> bool:
    """Is the value, as the model says it was printed, actually inside the quote? Numbers compare by their digits (1,20,000 = 120000)."""
    if not raw or not raw.strip():
        return True
    if squash(raw) in squash(snippet):
        return True
    digits = re.sub(r"\D", "", raw)
    return len(digits) >= 2 and digits in re.sub(r"\D", "", snippet)


def check_and_shape(doc_type: str, extraction: Extraction, pages: list[str], mapping: dict[str, str]) -> list[dict]:
    """Keep only the fields this document type has, restore PANs, normalise, and check each quoted snippet against the cited page."""
    spec = FIELDS[doc_type]
    out = {}
    for f in extraction.fields:
        if f.field not in spec or f.field in out:
            continue
        value, problem = normalise(spec[f.field][0], restore(f.value, mapping))
        snippet = restore(f.snippet, mapping) or ""
        notes = [problem] if problem else []
        if value is not None:
            if not 1 <= f.page <= len(pages):
                notes.append(f"cited page {f.page} does not exist")
            elif not snippet.strip() or squash(snippet) not in squash(pages[f.page - 1]):
                notes.append(f"quoted text not found on page {f.page}")
            elif not printed_in(restore(f.value, mapping), snippet):
                notes.append("value does not appear in the quoted text")  # a real quote with a different value
        out[f.field] = {"field": f.field, "value": value, "page_no": f.page if 1 <= f.page <= len(pages) else None, "snippet": snippet[:500] or None,
                        "check_note": "; ".join(notes) or None}
    for name in spec:  # every expected field is present, null when the model did not find it
        out.setdefault(name, {"field": name, "value": None, "page_no": None, "snippet": None, "check_note": "not found on the document"})
    return [out[n] for n in spec]


def prompt(doc_type: str, pages: list[str]) -> str:
    spec = "\n".join(f"- {k}: {d} ({kind})" for k, (kind, d) in FIELDS[doc_type].items())
    body = "\n\n".join(f"<page {i}>\n{t}\n</page {i}>" for i, t in enumerate(pages, 1))
    return (f"You extract fields from a {doc_type.replace('_', ' ').lower()} for a bank loan file. The document text is below.\n"
            "Rules: copy each value exactly as printed; do not compute, infer or guess; use null if a field is not printed; give the page "
            "number and the exact text you read it from as the snippet. Placeholders like [PAN-1] stand for a PAN: copy them unchanged.\n"
            f"Fields:\n{spec}\n\n{body}")


class Gemini:
    """The Gemini Developer API (free tier, GEMINI_API_KEY). Only synthetic text with PANs removed is sent."""

    def __init__(self, api_key: str, model: str):
        from google import genai
        from google.genai import types

        # a hung call must not stall the job runner: it fails after 60 s and the job retries
        self.client, self.model = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000)), model

    def extract(self, doc_type: str, pages: list[str]) -> Extraction:
        from google.genai import types

        r = self.client.models.generate_content(model=self.model, contents=prompt(doc_type, pages),
                                                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Extraction,
                                                                                   temperature=0))
        return r.parsed if isinstance(r.parsed, Extraction) else Extraction.model_validate_json(r.text)


def is_rate_limit(e: Exception) -> bool:
    """Quota, rate limit or temporary overload: retry later rather than count it as a failure of the document."""
    return getattr(e, "code", None) in (429, 503) or any(s in str(e)[:120] for s in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503"))
