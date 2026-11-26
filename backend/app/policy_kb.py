"""Policy knowledge base: official source text, chunked, with lexical retrieval and citations.

Ingest (owner tool now, the Phase 12 monitor later) fetches each source in policy/sources.yaml, keeps the raw bytes in the private
`policy-sources` bucket, and appends the cleaned text to lh_raw (policy_sources, policy_chunks). lh_curated.policy_chunks holds the latest
version of every source and is what the assessment reads.

Text methods: HTML pages are read from their markup (HTML_TEXT, not OCR). Born-digital PDFs use their own text layer (EMBEDDED_TEXT, not
OCR). A PDF page with no text layer is never passed off as text: the source is recorded as AWAITING_OCR and has no chunks.

Retrieval is BM25 over the chunks of the latest versions. It is deterministic, needs no paid service, and every hit carries the source
URL, version, fetch time, section and (for PDFs) page, so a reviewer can open the exact passage. Retrieved text is shown as evidence only;
it never changes a rule or a calculation.
"""

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import yaml

SOURCES_FILE = Path(__file__).parent / "policy" / "sources.yaml"
CHUNK_CHARS = 1200
MIN_SCORE = 8.0  # BM25 relevance floor below which a passage is not cited (tuned on the seed corpus)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BarodaLendingDeskDemo/1.0 (policy knowledge base; manual seed)"
SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "form", "button", "select", "svg", "iframe"}
BLOCK_TAGS = {"p", "div", "li", "tr", "br", "table", "section", "article", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "blockquote"}
HEADINGS = {"h1", "h2", "h3", "h4"}


def load_sources() -> list[dict]:
    return yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]


# ---------------------------------------------------------------- HTML to text blocks
class _Extractor(HTMLParser):
    """Collect text blocks inside the container element(s); comments are dropped by HTMLParser (handle_comment is not overridden)."""

    def __init__(self, container: dict):
        super().__init__(convert_charrefs=True)
        self.tag, self.cls = container["tag"], container.get("class")
        self.inside = 0       # depth of the matched container (counting nested tags of the same name)
        self.skip = 0         # depth inside skipped tags
        self.blocks: list[tuple[str, bool]] = []  # (text, is_heading)
        self.buf: list[str] = []
        self.heading = False

    def _match(self, tag, attrs) -> bool:
        return tag == self.tag and (self.cls is None or self.cls in (dict(attrs).get("class") or "").split())

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.blocks.append((text, self.heading))
        self.buf, self.heading = [], False

    def handle_starttag(self, tag, attrs):
        if self.inside:
            if tag == self.tag:
                self.inside += 1
            if tag in SKIP_TAGS:
                self.skip += 1
            elif tag in BLOCK_TAGS:
                self._flush()
                self.heading = tag in HEADINGS
            elif tag == "td" or tag == "th":
                self.buf.append(" | " if self.buf else "")
        elif self._match(tag, attrs):
            self.inside = 1

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in BLOCK_TAGS:
            self._flush()
        if tag == self.tag:
            self.inside -= 1
            if not self.inside:
                self._flush()

    def handle_startendtag(self, tag, attrs):
        if self.inside and tag == "br":
            self._flush()

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.buf.append(data)


def html_blocks(html: str, container: dict, stop_at: list[str] | None = None) -> list[tuple[str, bool]]:
    p = _Extractor(container)
    p.feed(html)
    p.close()
    p._flush()
    out = []
    for text, heading in p.blocks:
        if stop_at and any(text.startswith(s) for s in stop_at):
            break
        out.append((unescape(text), heading))
    return out


def _looks_like_heading(text: str) -> bool:
    """RBI pages mark chapter and section titles with bold paragraphs, not heading tags."""
    return len(text) <= 90 and not text.endswith((".", ":", ";", ",")) and not re.match(r"^\(?\d+[.)]", text) and text[:1].isupper()


# ---------------------------------------------------------------- chunks
@dataclass
class Chunk:
    chunk_no: int
    text: str
    section: str | None = None
    page_no: int | None = None


def chunk_blocks(blocks: list[tuple[str, bool]], page_no: int | None = None, start: int = 1, limit: int = CHUNK_CHARS) -> list[Chunk]:
    """Pack whole blocks into chunks of about `limit` characters; a block that is longer is split at sentence ends. The section is the
    heading in force where the chunk starts."""
    chunks: list[Chunk] = []
    section, cur, cur_section = None, [], None

    def close():
        if cur:
            chunks.append(Chunk(start + len(chunks), "\n".join(cur), cur_section, page_no))
            cur.clear()

    for text, heading in blocks:
        if heading or _looks_like_heading(text):
            close()
            section = text[:200]
            continue
        pieces = [text] if len(text) <= limit else re.findall(rf".{{1,{limit}}}(?:[.;:](?=\s)|$)|.{{1,{limit}}}", text, re.S)
        for piece in (x.strip() for x in pieces):
            if not piece:
                continue
            if cur and sum(len(c) + 1 for c in cur) + len(piece) > limit:
                close()
            if not cur:
                cur_section = section
            cur.append(piece)
    close()
    return chunks


def pdf_chunks(pdf: bytes) -> tuple[list[Chunk] | None, int]:
    """Chunks per page from the PDF's own text layer; (None, pages) if any page has none (a scan, which waits for OCR)."""
    from app.docproc import embedded_text

    pages = embedded_text(pdf)
    if pages is None:
        from pypdf import PdfReader
        import io

        try:
            n = len(PdfReader(io.BytesIO(pdf)).pages)
        except Exception:
            n = 0
        return None, n
    out: list[Chunk] = []
    for i, text in enumerate(pages, 1):
        blocks = [(re.sub(r"\s+", " ", para).strip(), False) for para in re.split(r"\n\s*\n|\n(?=\d+\.\s)", text) if para.strip()]
        out += chunk_blocks(blocks, page_no=i, start=len(out) + 1)
    return out, len(pages)


def extract(source: dict, body: bytes, content_type: str) -> dict:
    """Text method, chunks and the hash of the cleaned text (what change detection compares, so markup churn is not a change)."""
    if body.startswith(b"%PDF-") or "pdf" in content_type:
        chunks, pages = pdf_chunks(body)
        if chunks is None:
            return {"extraction_method": "AWAITING_OCR", "chunks": [], "page_count": pages, "text_sha256": hashlib.sha256(body).hexdigest()}
        method, page_count = "EMBEDDED_TEXT", pages
    else:
        html = body.decode("utf-8", errors="replace")
        chunks, method, page_count = chunk_blocks(html_blocks(html, source["container"], source.get("stop_at"))), "HTML_TEXT", None
    text = "\n\n".join(c.text for c in chunks)
    return {"extraction_method": method, "chunks": chunks, "page_count": page_count, "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


# ---------------------------------------------------------------- retrieval
STOP = set("a an and are as at be by for from has have in is it its of on or shall that the their this to was were which will with not any all such "
           "may be been other under these those than then there where who whom whose".split())


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 1]


@dataclass
class Index:
    """BM25 over chunk rows ({chunk_id, source_id, version, publisher, title, url, fetched_at, section, page_no, text, extraction_method})."""

    rows: list[dict]
    min_score: float = MIN_SCORE  # absolute BM25 scores grow with corpus size: the floor belongs to the corpus
    k1: float = 1.5
    b: float = 0.75
    tf: list[Counter] = field(init=False)
    df: Counter = field(init=False)
    avg: float = field(init=False)

    def __post_init__(self):
        self.tf = [Counter(tokens(f"{r.get('section') or ''} {r['text']}")) for r in self.rows]
        self.df = Counter(t for c in self.tf for t in c)
        self.avg = sum(sum(c.values()) for c in self.tf) / max(1, len(self.tf))

    def search(self, query: str, k: int = 3, sources: tuple[str, ...] | None = None, min_score: float | None = None) -> list[dict]:
        # ponytail: linear scan over every chunk; fine for a few thousand chunks, add an inverted index if the corpus grows past that
        q, n, hits = set(tokens(query)), len(self.rows), []
        for r, tf in zip(self.rows, self.tf):
            if sources and r["source_id"] not in sources:
                continue
            length = sum(tf.values()) or 1
            score = sum(math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5)) * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * length / self.avg))
                        for t in q if tf[t])
            if score >= (self.min_score if min_score is None else min_score):
                hits.append((score, r))
        hits.sort(key=lambda h: -h[0])
        return [{**r, "score": round(s, 2)} for s, r in hits[:k]]


# Which passages each engine rule is checked against: (query, sources to search). Human-maintained; retrieval informs the reviewer and
# never changes a result. A rule with no passage above the relevance floor shows "no matching passage", never a weak guess.
BOB, CONDUCT, KYC, CREDIT = "bob_personal_loan", "rbi_cb_responsible_business_conduct", "rbi_cb_kyc", "rbi_cb_credit_risk"
RULE_QUERIES = {
    "R01": ("eligibility age personal loan applicant", (BOB,)),
    "R02": ("repayment period tenure months personal loan", (BOB,)),
    "R03": ("income proof salary slips income tax return bank statement documents", (BOB,)),
    "R04": ("income proof salary slips income tax return bank statement documents", (BOB,)),
    "R05": ("eligibility minimum income salary personal loan", (BOB,)),
    "R06": ("eligibility minimum continuous service employees", (BOB,)),
    "R07": ("CIBIL score credit information bureau", (BOB, CREDIT)),
    "R08": ("credit appraisal repayment default overdue borrower", (CREDIT, BOB)),
    "R09": ("account relationship satisfactory account", (BOB,)),
    "R10": ("repayment capacity FOIR EMI income", (BOB,)),
    "R11": ("customer due diligence individual PAN officially valid document Aadhaar e-KYC", (KYC,)),
    "R12": ("documents required personal loan application", (BOB,)),
    "R13": ("loans advances bank employees staff", (BOB, CREDIT)),
    "R14": ("non-resident Indian NRI loan", (BOB,)),
    "R15": ("pensioners pension loan", (BOB,)),
}
TERMS_QUERIES = {
    "Key Facts Statement and APR": ("Key Facts Statement annual percentage rate loan borrower", (CONDUCT,)),
    "Penal charges": ("penal charges loan accounts non-compliance material terms", (CONDUCT,)),
    "Interest rate and charges": ("interest rates charges processing charges penal interest", (BOB,)),
}


def cite(hit: dict) -> dict:
    """What the assessment stores and the reviewer sees for one retrieved passage."""
    return {"chunk_id": hit["chunk_id"], "source_id": hit["source_id"], "version": hit["version"], "publisher": hit["publisher"], "title": hit["title"],
            "url": hit["url"], "fetched_at": str(hit["fetched_at"]), "section": hit.get("section"), "page_no": hit.get("page_no"),
            "extraction_method": hit["extraction_method"], "score": hit["score"], "quote": hit["text"][:700]}


def retrieve_for(index: Index, rules: list[dict], k: int = 2) -> dict:
    """Citations for every rule that failed or was referred, plus the conduct topics every sanction touches."""
    by_rule = {r["id"]: [cite(h) for h in index.search(*RULE_QUERIES[r["id"]][:1], k, RULE_QUERIES[r["id"]][1])]
               for r in rules if r["status"] in ("FAIL", "NEEDS_REVIEW") and r["id"] in RULE_QUERIES}
    terms = {topic: [cite(h) for h in index.search(q, 1, src)] for topic, (q, src) in TERMS_QUERIES.items()}
    return {"by_rule": by_rule, "terms": terms}


class KnowledgeBase:
    """Reads lh_curated.policy_chunks as the app identity, once per process."""

    def __init__(self):
        self._index: Index | None = None

    def index(self) -> Index:
        if self._index is None:
            from app.config import settings
            from app.lakehouse import _run

            rows = _run(f"SELECT chunk_id, source_id, version, publisher, title, url, fetched_at, section, page_no, text, extraction_method "
                        f"FROM `{settings.gcp_project}.lh_curated.policy_chunks` ORDER BY chunk_id", {})
            self._index = Index(rows)
        return self._index


_kb = KnowledgeBase()


def get_kb() -> KnowledgeBase:
    return _kb
