"""Document text and field extraction.

Text, from exactly one labelled method (never another OCR engine, never a silent substitution):
  EMBEDDED_TEXT              the PDF's own text layer, for born-digital PDFs. Not OCR, never labelled as OCR.
  PREPARED_DEMO_TEXT         generated demonstration values for degraded demo documents: "Prepared demo data — not OCR", never evidence
  GEMINI_VISION              any other scan or photo (new customers, KYC updates): Gemini reads the image itself, in one call that
                             returns the page text and the fields. Labelled "Read by Gemini (AI, not OCR)"; a person verifies every value.
  MANUAL                     the Loan Officer keys values from the document image
There is no OCR engine. Without GEMINI_API_KEY, scans go straight to manual review.
GEMINI_VISION sends the image as it is, so PANs cannot be replaced first: synthetic documents only, as for the rest of the demo.

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
READ_PROMPT_VERSION = "read-v1"
TEXT_METHODS = ("EMBEDDED_TEXT", "GEMINI_VISION", "MANUAL", "PREPARED_DEMO_TEXT")
PAGE_TEXT_METHODS = ("EMBEDDED_TEXT", "GEMINI_VISION")  # methods that store page text
MIN_CHARS_PER_PAGE = 20  # below this a PDF page is treated as a scan with no usable text layer

# field -> (kind, description). Kinds: text, date, amount, int, pan, gender
FIELDS: dict[str, dict[str, tuple[str, str]]] = {
    "PAN_CARD": {"name": ("text", "card holder's name"), "father_name": ("text", "father's name"), "dob": ("date", "date of birth"), "pan": ("pan", "PAN")},
    "AADHAAR_MASKED": {"name": ("text", "name"), "dob": ("date", "date of birth"), "gender": ("gender", "gender"),
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
    "EMPLOYMENT_PROOF": {"employee_name": ("text", "employee name"), "employer": ("text", "employer"), "designation": ("text", "designation"),
                         "date_of_joining": ("date", "date of joining")},
}


class ExtractedField(BaseModel):
    field: str
    value: str | None = Field(description="the value exactly as printed, or null if it is not on the document")
    page: int = Field(ge=1)
    snippet: str = Field(description="the exact text on that page the value was read from")


class Extraction(BaseModel):
    fields: list[ExtractedField]


class Reading(BaseModel):
    pages: list[str] = Field(description="the full text of each page, in order, copied exactly as printed")
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
PAN_RE = re.compile(r"\b(?:SYNP[0-9]{7}[A-Z]|[A-Z]{5}[0-9]{4}[A-Z])\b")  # synthetic or real: either is replaced before any text reaches Gemini
UNMASKED_AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")  # in an Aadhaar number field
# in free page text: the printed Aadhaar layout (three groups of four, first digit 2-9), so 12-digit account numbers are not mistaken for it
AADHAAR_IN_TEXT = re.compile(r"(?<![\dX])(?<![\dX][ -])[2-9]\d{3}[ -]\d{4}[ -]\d{4}(?![ -]?\d)")  # exactly three groups: not part of a longer number


def full_aadhaar_in(texts: list[str]) -> bool:
    return any(AADHAAR_IN_TEXT.search(t or "") for t in texts)


def redact(pages: list[str]) -> tuple[list[str], dict[str, str]]:
    """Replace every PAN with a placeholder (the mapping stays on the backend) and blank out any full Aadhaar number, which is never needed."""
    pages = [AADHAAR_IN_TEXT.sub("[AADHAAR-REMOVED]", p) for p in pages]
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
        return p, None if PAN_RE.fullmatch(p) else "not in the PAN format (five letters, four digits, a letter)"
    if kind == "gender":
        return normalise_gender(s)
    return re.sub(r"\s+", " ", s), None


GENDER = {"m": "M", "male": "M", "f": "F", "female": "F", "o": "O", "other": "O", "transgender": "O", "t": "O"}


def normalise_gender(raw: str | None) -> tuple[str | None, str | None]:
    """The record's code (M, F, O) for a printed gender ("Female", "MALE", "F"); anything else is kept as printed, with a note."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    code = GENDER.get(re.sub(r"[^a-z]", "", s.lower()))
    return (code, None) if code else (s, "gender not recognised")


# A statement's income is per calendar month of its period, never per credit: a business with 58 receipts in six months
# earns its six-month total over 6, not over 58. The checklist asks for six months, assumed only when the period is unreadable.
ASSUMED_STATEMENT_MONTHS = 6
MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# year-month, day/month/year, month/year, or a month name and year (an optional day first)
YEAR_MONTH = re.compile(r"(?i)\b(?:(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?|\d{1,2}[-/.](\d{1,2})[-/.](\d{4})|(\d{1,2})[-/.](\d{4})"
                        r"|(?:\d{1,2}[ -])?([a-z]{3})[a-z]*[ ,.-]*(\d{4}))\b")


def statement_months(period: str | None) -> int | None:
    """Calendar months a printed statement period covers: "2026-05..2026-10", "01/05/2026 to 31/10/2026", "May 2026 - Oct 2026" are all 6."""
    found = []
    for y1, m1, m2, y2, m3, y3, name, y4 in YEAR_MONTH.findall(period or ""):
        if y1 or y2 or y3:
            found.append((int(y1 or y2 or y3), int(m1 or m2 or m3)))
        elif name.lower() in MONTH_NAMES:
            found.append((int(y4), MONTH_NAMES.index(name.lower()) + 1))
    if len(found) < 2 or not all(1 <= m <= 12 for _, m in (found[0], found[-1])):
        return None
    n = (found[-1][0] - found[0][0]) * 12 + found[-1][1] - found[0][1] + 1
    return n if 1 <= n <= 36 else None


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


NUMBER_TOKEN = re.compile(r"(?i)(?:rs\.?|inr|₹)?\s*\d[\d,]*(?:\.\d+)?")


def printed_in(kind: str, raw: str | None, value: str | None, snippet: str) -> bool:
    """Is the value actually inside the quote?
    Numbers: the normalised value must equal one of the quote's own numbers exactly (so 62 does not match 62,000).
    Everything else: the printed form must appear on word boundaries, not as part of a longer word or number."""
    if value is None or not raw or not raw.strip():
        return True
    if kind in ("amount", "int"):
        return any(normalise(kind, t.strip()) == (value, None) for t in NUMBER_TOKEN.findall(snippet))
    return re.search(r"(?<![\w])" + re.escape(squash(raw)) + r"(?![\w])", squash(snippet)) is not None


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
        if value is None:
            notes.append("not found on the document")  # the same note as a field the model left out: the reviewer looks for it
        else:
            if not 1 <= f.page <= len(pages):
                notes.append(f"cited page {f.page} does not exist")
            elif not snippet.strip() or squash(snippet) not in squash(pages[f.page - 1]):
                notes.append(f"quoted text not found on page {f.page}")
            elif not printed_in(spec[f.field][0], restore(f.value, mapping), value, snippet):
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


def read_prompt(doc_type: str) -> str:
    spec = "\n".join(f"- {k}: {d} ({kind})" for k, (kind, d) in FIELDS[doc_type].items())
    return (f"The attached file is a {doc_type.replace('_', ' ').lower()} for a bank loan file. First write out the full text of every page, "
            "exactly as printed, one entry per page. Then extract the fields below. Rules: copy each value exactly as printed; do not compute, "
            "infer or guess; use null if a field is not printed or not legible; give the page number and, as the snippet, the exact text from "
            f"your page text that the value was read from.\nFields:\n{spec}")


class Gemini:
    """The Gemini Developer API (free tier, GEMINI_API_KEY). Only synthetic text with PANs removed is sent."""

    def __init__(self, api_key: str, model: str):
        from google import genai
        from google.genai import types

        # a hung call must not stall the job runner: it fails after 60 s and the job retries
        self.client, self.model = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000)), model

    def generate(self, text: str, schema: type[BaseModel], file: tuple[bytes, str] | None = None) -> BaseModel:
        """One structured call: the reply must validate against `schema`. `file` is (bytes, mime type), sent inline with the prompt."""
        from google.genai import types

        contents = [types.Part.from_bytes(data=file[0], mime_type=file[1]), text] if file else text
        r = self.client.models.generate_content(model=self.model, contents=contents,
                                                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0))
        return r.parsed if isinstance(r.parsed, schema) else schema.model_validate_json(r.text)

    def extract(self, doc_type: str, pages: list[str]) -> Extraction:
        return self.generate(prompt(doc_type, pages), Extraction)

    def read(self, doc_type: str, data: bytes, mime: str) -> Reading:
        """A scan or photo: page text and fields from the image, in one call."""
        return self.generate(read_prompt(doc_type), Reading, (data, mime))


def is_rate_limit(e: Exception) -> bool:
    """Quota, rate limit or temporary overload: retry later rather than count it as a failure of the document."""
    return getattr(e, "code", None) in (429, 503) or any(s in str(e)[:120] for s in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503"))
