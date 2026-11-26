"""Official policy monitor (plan section 8). GitHub Actions only schedules it (POST /internal/monitor/run); the work happens here.

For each watched source, and for new lending-related items in the RBI notifications feed:

  fetch       conditional GET (stored ETag / Last-Modified). 304 or identical cleaned text: nothing is downloaded again, extracted or sent to
              Gemini. A bot-check or error page is never stored.
  store       a changed source becomes a new version: raw bytes in the private policy-sources bucket, cleaned text chunks in lh_raw
              (policy_sources, policy_chunks; lh_curated.policy_chunks is a view of the latest versions, so retrieval sees it at once)
  Layer A     did the document change? Sections with added or removed lines, counts and short samples; publication date from the feed,
              the PDF metadata, or 'unknown'
  Layer B     did a stated fact change? Gemini fills a fixed PolicyFacts schema from the relevant passages, quoting each value; a value whose
              quote is not in the source is dropped. Old and new facts are compared deterministically.
  Layer C     does it affect a rule? An explicit, human-maintained fact -> parameter map (policy/fact_rule_map.yaml) compares each fact with the
              ACTIVE rule set. Gemini plays no part here.

Classifications: DOC_ONLY, FACT_CHANGED_NO_RULE_IMPACT, RULE_IMPACT, COMPLIANCE_ITEM, VERIFICATION_CANDIDATE. Reports and proposals are
written automatically; a rule never is. A person reviews proposals, drafts a rule set, the golden cases run, and they activate it.
"""

import difflib
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from app import policy_kb
from app.db import staff_tx

PROMPT_VERSION = "facts-v2"  # v2: quotes of table rows match without cell separators; facts are cached per source version and prompt version
FACT_MAP_FILE = Path(__file__).parent / "policy" / "fact_rule_map.yaml"
FACT_TEXT_CAP = 24000  # characters of relevant passages sent for one source (free-tier tokens per minute)
ORDER = ["RULE_IMPACT", "COMPLIANCE_ITEM", "VERIFICATION_CANDIDATE", "FACT_CHANGED_NO_RULE_IMPACT", "DOC_ONLY"]
FactKey = Literal["age_min_years", "age_max_at_maturity_years", "loan_amount_min", "loan_amount_max", "tenure_max_months", "income_min_monthly",
                  "foir_max_percent", "processing_fee_percent", "processing_fee_min", "processing_fee_max", "penal_charge_percent_pa",
                  "prepayment_charge_percent", "min_credit_score", "cooling_off_days", "kfs_validity_working_days", "penal_charges_rule",
                  "interest_rate", "benchmark_rate"]


class PolicyFact(BaseModel):
    key: FactKey
    value: str = Field(description="the figure or rule exactly as printed, e.g. '84 Months', 'Rs.20.00 lakhs', '2% + GST'")
    condition: str | None = Field(default=None, description="who or what it applies to, as printed (e.g. 'Govt employees with salary account'); null if general")
    quote: str = Field(description="the exact sentence or table row the value is read from")


class PolicyFacts(BaseModel):
    facts: list[PolicyFact]


def fact_map() -> dict:
    return yaml.safe_load(FACT_MAP_FILE.read_text(encoding="utf-8"))["facts"]


# ---------------------------------------------------------------- figures
_NUM = re.compile(r"(?i)(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr\b)?")


def numbers(value: str) -> list[Decimal]:
    """Every figure in a printed value, in rupees / months / percent as written: 'Rs.20.00 lakhs' -> 2000000, '48 to 60 Months' -> 48, 60."""
    out = []
    for n, unit in _NUM.findall(value or ""):
        try:
            d = Decimal(n.replace(",", ""))
        except InvalidOperation:
            continue
        u = (unit or "").lower()
        out.append(d * (100000 if u.startswith(("lakh", "lac")) else 10000000 if u.startswith("cr") else 1))
    return out


def squash(s: str | None) -> str:
    """Lower case, single spaces, and no table separators: the model quotes a table row without the ' | ' our extraction puts between cells."""
    return re.sub(r"[\s|]+", " ", (s or "")).strip().lower()


def _fid(f: dict) -> tuple:
    return (f["key"], squash(f.get("condition")))


# ---------------------------------------------------------------- Layer A
def diff_text(old: list[dict], new: list[dict]) -> dict:
    """Which sections gained or lost lines between two chunk lists ({section, text, page_no})."""
    def lines(chunks):
        return [(c.get("section") or ("page " + str(c["page_no"]) if c.get("page_no") else "(no heading)"), ln.strip())
                for c in chunks for ln in c["text"].split("\n") if ln.strip()]

    a, b = lines(old), lines(new)
    added, removed, sections, samples = 0, 0, {}, []
    sm = difflib.SequenceMatcher(a=[x[1] for x in a], b=[x[1] for x in b], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for sec, ln in a[i1:i2]:
            removed += 1
            sections.setdefault(sec, {"added": 0, "removed": 0})["removed"] += 1
            if len(samples) < 6:
                samples.append({"change": "removed", "section": sec, "text": ln[:200]})
        for sec, ln in b[j1:j2]:
            added += 1
            sections.setdefault(sec, {"added": 0, "removed": 0})["added"] += 1
            if len(samples) < 6:
                samples.append({"change": "added", "section": sec, "text": ln[:200]})
    return {"changed": bool(added or removed), "added_lines": added, "removed_lines": removed,
            "sections": [{"section": s[:200], **v} for s, v in list(sections.items())[:40]], "samples": samples}


# ---------------------------------------------------------------- Layer B
def relevant_text(chunks: list[dict], keywords: list[str]) -> str:
    keep = [c for c in chunks if any(k.lower() in (c.get("section") or "").lower() + " " + c["text"].lower() for k in keywords)]
    out, total = [], 0
    for c in keep:
        block = (f"[{c['section']}]\n" if c.get("section") else "") + c["text"]
        if total + len(block) > FACT_TEXT_CAP:
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


def facts_prompt(source: dict, text: str) -> str:
    keys = ", ".join(FactKey.__args__)
    return (f"You read an official document ({source['publisher']}: {source['title']}) for a bank's personal loan policy monitor.\n"
            "Extract only facts about personal loans (standard scheme) or rules that apply to all retail loans. For each fact give its key "
            f"(one of: {keys}), the value exactly as printed, the condition it applies to as printed (null if general), and the exact sentence or "
            "table row you read it from as the quote. One fact per value: if a figure differs by category, give one fact per category. Do not "
            "compute, convert or infer anything; skip anything not printed. Ignore home, vehicle, education, gold and digital loan products.\n\n"
            f"Document passages:\n{text}")


def check_facts(extracted: PolicyFacts, text: str) -> tuple[list[dict], int]:
    """Keep facts whose quote is really in the text and whose value is in the quote. (facts, dropped)"""
    hay, out, dropped = squash(text), [], 0
    for f in extracted.facts:
        q = squash(f.quote)
        if len(q) < 4 or q not in hay or squash(f.value) not in q:
            dropped += 1
            continue
        out.append({"key": f.key, "value": f.value.strip(), "condition": (f.condition or "").strip() or None, "quote": f.quote.strip()[:500],
                    "numbers": [str(n) for n in numbers(f.value)]})
    seen, uniq = set(), []
    for f in out:  # the same fact twice (e.g. repeated in a table) counts once
        if (_fid(f), f["value"]) not in seen:
            seen.add((_fid(f), f["value"]))
            uniq.append(f)
    return uniq, dropped


def diff_facts(old: list[dict] | None, new: list[dict]) -> dict:
    o = {(_fid(f)): f for f in old or []}
    n = {(_fid(f)): f for f in new}
    changed = [{"key": k[0], "condition": n[k]["condition"], "old": o[k]["value"], "new": n[k]["value"], "old_quote": o[k]["quote"], "new_quote": n[k]["quote"]}
               for k in o.keys() & n.keys() if squash(o[k]["value"]) != squash(n[k]["value"])]
    return {"baseline": old is None, "added": [n[k] for k in n.keys() - o.keys()] if old is not None else [], "removed": [o[k] for k in o.keys() - n.keys()],
            "changed": changed, "current": new}


# ---------------------------------------------------------------- Layer C
def impact(facts: list[dict], rs, fmap: dict, source: dict, version: int) -> list[dict]:
    """Each fact against the rule parameters the map says it bears on. Values are compared per segment as the rule set resolves them."""
    from app.policy.ruleset import SEGMENTS

    items = []
    for f in facts:
        m = fmap.get(f["key"], {})
        base = {"key": f["key"], "condition": f["condition"], "value": f["value"], "quote": f["quote"], "note": m.get("note"),
                "url": source["url"], "version": version}
        if m.get("compliance"):
            items.append(base | {"kind": "COMPLIANCE_ITEM"})
            continue
        nums = [Decimal(x) for x in f["numbers"]]
        if not m.get("params") or not nums:
            items.append(base | {"kind": "NO_RULE"})
            continue
        official = {"max": max(nums), "min": min(nums), "equal": nums[0]}[m.get("compare", "equal")] * Decimal(str(m.get("scale", 1)))
        cond = squash(f["condition"])
        for entry in m["params"]:
            spec = entry if isinstance(entry, dict) else {"name": entry}
            name = spec["name"]
            if cond and spec.get("when") and (not any(w in cond for w in spec["when"]) or any(u in cond for u in spec.get("unless", []))):
                continue  # e.g. a salaried age limit is not compared with the non-salaried parameter
            values = {}
            for seg in SEGMENTS:
                p = rs.param(name, seg)
                values.setdefault((json.dumps(p.value), p.confidence), []).append(seg)
            for (val, conf), segs in values.items():
                cur = json.loads(val)
                try:
                    same = Decimal(str(cur)) == official
                except (InvalidOperation, TypeError):
                    continue
                kind = "VERIFICATION_CANDIDATE" if same and conf == "DEMO_ASSUMPTION" else "CONFIRMED" if same else "RULE_IMPACT"
                items.append(base | {"kind": kind, "parameter": name, "segments": segs, "current_value": cur, "current_confidence": conf,
                                     "official_value": format(official.normalize(), "f")})
    return items


def classify(layer_a: dict, layer_b: dict, items: list[dict], feed_item: bool = False) -> str:
    kinds = {i["kind"] for i in items if i["kind"] in ORDER}
    if feed_item:
        kinds.add("COMPLIANCE_ITEM")  # a new lending-related RBI notification goes to a person's compliance checklist
    for k in ORDER[:3]:
        if k in kinds:
            return k
    fact_changed = bool(layer_b.get("added") or layer_b.get("removed") or layer_b.get("changed") or (layer_b.get("baseline") and layer_b.get("current")))
    return "FACT_CHANGED_NO_RULE_IMPACT" if fact_changed else "DOC_ONLY"


# ---------------------------------------------------------------- storage (BigQuery, as the app identity)
class PolicyStore:
    def latest(self, source_id: str) -> dict | None:
        from app.config import settings
        from app.lakehouse import _run

        rows = _run(f"SELECT * FROM `{settings.gcp_project}.lh_raw.policy_sources` WHERE source_id = @s ORDER BY version DESC LIMIT 1", {"s": source_id})
        return rows[0] if rows else None

    def chunks(self, source_id: str, version: int) -> list[dict]:
        from app.config import settings
        from app.lakehouse import _run

        return _run(f"SELECT chunk_no, page_no, section, text FROM `{settings.gcp_project}.lh_raw.policy_chunks` WHERE source_id = @s "
                    f"AND CAST(version AS STRING) = @v ORDER BY chunk_no", {"s": source_id, "v": str(version)})

    def facts(self, source_id: str, version: int, prompt_version: str = PROMPT_VERSION) -> list[dict] | None:
        """None when facts were never extracted for this version with this prompt (a marker row records an extraction, even an empty one)."""
        from app.config import settings
        from app.lakehouse import _run

        rows = _run(f"SELECT fact_key, value, `condition`, quote, numbers FROM `{settings.gcp_project}.lh_raw.policy_facts` WHERE source_id = @s "
                    f"AND CAST(version AS STRING) = @v AND prompt_version = @p ORDER BY fact_key", {"s": source_id, "v": str(version), "p": prompt_version})
        if not rows:
            return None
        return [{"key": r["fact_key"], "value": r["value"], "condition": r["condition"], "quote": r["quote"], "numbers": json.loads(r["numbers"] or "[]")}
                for r in rows if r["fact_key"] != "_extracted"]

    def known_sources(self, prefix: str) -> set[str]:
        from app.config import settings
        from app.lakehouse import _run

        return {r["source_id"] for r in _run(f"SELECT DISTINCT source_id FROM `{settings.gcp_project}.lh_raw.policy_sources` WHERE STARTS_WITH(source_id, @p)",
                                            {"p": prefix})}

    def append(self, table: str, rows: list[dict]) -> None:
        from google.cloud import bigquery

        from app.config import settings
        from app.lakehouse import _client

        assert table in ("policy_sources", "policy_chunks", "policy_facts")
        if not rows:
            return
        ref = f"{settings.gcp_project}.lh_raw.{table}"
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", create_disposition="CREATE_NEVER", source_format="NEWLINE_DELIMITED_JSON",
                                     schema=_client().get_table(ref).schema)
        _client().load_table_from_json(rows, ref, job_config=cfg).result()


# ---------------------------------------------------------------- one run
@dataclass
class Outcome:
    source: dict
    status: str  # unchanged | changed | baseline | error | skipped
    version: int | None = None
    note: str | None = None
    report_id: str | None = None
    proposals: int = 0
    extra: dict = field(default_factory=dict)


def _fetch(http, url: str, prev: dict | None):
    headers = {k: v for k, v in (("If-None-Match", (prev or {}).get("etag")), ("If-Modified-Since", (prev or {}).get("last_modified"))) if v}
    return http.get(url, headers=headers)


def _store_version(deps, source: dict, r, x: dict, prev: dict | None) -> int:
    version = int(prev["version"]) + 1 if prev else 1
    raw_sha = hashlib.sha256(r.content).hexdigest()
    ext = "pdf" if r.content.startswith(b"%PDF-") else "html"
    path = f"{source['id']}/v{version}-{raw_sha[:12]}.{ext}"
    try:
        deps.policy_storage.put(path, r.content, "application/pdf" if ext == "pdf" else "text/html")
    except Exception as e:  # noqa: BLE001  an identical object from an earlier, interrupted run is fine
        if "409" not in str(e) and "Duplicate" not in str(e) and "400" not in str(e):
            raise
    now = datetime.now(timezone.utc).isoformat()
    deps.policy_store.append("policy_chunks", [{"source_id": source["id"], "version": version, "chunk_no": c.chunk_no, "page_no": c.page_no, "section": c.section,
                                                "text": c.text, "extraction_method": x["extraction_method"], "created_at": now} for c in x["chunks"]])
    deps.policy_store.append("policy_sources", [{"source_id": source["id"], "version": version, "publisher": source["publisher"], "title": source["title"],
                                                 "url": source["url"], "fetched_at": now, "http_status": r.status_code,
                                                 "content_type": r.headers.get("content-type", "").split(";")[0].strip(), "etag": r.headers.get("etag"),
                                                 "last_modified": r.headers.get("last-modified"), "raw_sha256": raw_sha, "text_sha256": x["text_sha256"],
                                                 "storage_path": path, "extraction_method": x["extraction_method"], "page_count": x["page_count"],
                                                 "chunk_count": len(x["chunks"]), "created_at": now}])
    return version


def _extract_facts(conn, deps, source: dict, chunks: list[dict], version: int) -> tuple[list[dict] | None, str | None]:
    """(facts, note). None facts: not extracted (no key, budget, error) - the reason is in the note and the next run tries again."""
    from app.docproc import is_rate_limit

    text = relevant_text(chunks, source["facts"]["keywords"])
    if not text:
        facts = []
    elif deps.gemini is None:
        return None, "fact extraction not configured (GEMINI_API_KEY)"
    elif not conn.execute("SELECT take_budget('gemini', %s) AS ok", (deps.gemini_cap,)).fetchone()["ok"]:
        return None, f"daily Gemini budget of {deps.gemini_cap} calls reached; facts on the next run"
    else:
        try:
            got = deps.gemini.generate(facts_prompt(source, text), PolicyFacts)
        except Exception as e:  # noqa: BLE001  recorded on the run; the document change is still reported
            return None, f"fact extraction failed ({'rate limit' if is_rate_limit(e) else type(e).__name__}); facts on the next run"
        facts, dropped = check_facts(got, text)
        if dropped:
            source = source | {"_dropped": dropped}
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"source_id": source["id"], "version": version, "fact_key": f["key"], "value": f["value"], "condition": f["condition"], "quote": f["quote"],
             "numbers": json.dumps(f["numbers"]), "model": deps.model, "prompt_version": PROMPT_VERSION, "extracted_at": now} for f in facts]
    rows.append({"source_id": source["id"], "version": version, "fact_key": "_extracted", "value": str(len(facts)), "condition": None, "quote": "",
                 "numbers": "[]", "model": deps.model, "prompt_version": PROMPT_VERSION, "extracted_at": now})
    deps.policy_store.append("policy_facts", rows)
    return facts, (f"{source['_dropped']} extracted value(s) dropped: quote not found in the source" if source.get("_dropped") else None)


def _report(conn, run_id, source: dict, *, publication_date: str, from_v, to_v, from_sha, to_sha, layer_a, layer_b, items, feed_item=False) -> tuple[str, int]:
    cls = classify(layer_a, layer_b, items, feed_item)
    with staff_tx(conn, actor_type="SYSTEM"):
        rid = conn.execute("INSERT INTO policy_change_reports (run_id, source_id, publisher, title, url, publication_date, from_version, to_version, from_sha256, "
                           "to_sha256, classification, layer_a, layer_b, layer_c) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                           (run_id, source["id"], source["publisher"], source["title"], source["url"], publication_date, from_v, to_v, from_sha, to_sha, cls,
                            json.dumps(layer_a), json.dumps(layer_b), json.dumps(items))).fetchone()["id"]
        n = 0
        for i in items:
            if i["kind"] not in ("RULE_IMPACT", "VERIFICATION_CANDIDATE"):
                continue
            evidence = {"url": i["url"], "version": i["version"], "quote": i["quote"], "condition": i["condition"], "fact": i["key"], "value": i["value"],
                        "segments": i["segments"], "note": i["note"]}
            # the same official figure for the same parameter and condition is one proposal, however the model printed it ("21" / "21 years")
            sha = hashlib.sha256(json.dumps([i["parameter"], i["key"], squash(i["condition"]), i["official_value"]]).encode()).hexdigest()
            n += conn.execute("INSERT INTO policy_change_proposals (report_id, kind, parameter, current_value, current_confidence, official_value, evidence, evidence_sha256) "
                              "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (parameter, kind, evidence_sha256) DO NOTHING",
                              (rid, "RULE_CHANGE" if i["kind"] == "RULE_IMPACT" else "VERIFY_VALUE", i["parameter"], json.dumps(i["current_value"]),
                               i["current_confidence"], json.dumps({"printed": i["value"], "number": i["official_value"]}), json.dumps(evidence), sha)).rowcount
    return str(rid), n


def check_source(conn, deps, run_id, source: dict, rs, fmap: dict) -> Outcome:
    prev = deps.policy_store.latest(source["id"])
    r = _fetch(deps.http, source["url"], prev)
    if r.status_code == 304:
        return _maybe_baseline_facts(conn, deps, run_id, source, prev, rs, fmap, Outcome(source, "unchanged", int(prev["version"]), "not modified (304)"))
    if r.status_code != 200:
        return Outcome(source, "error", note=f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "").split(";")[0].strip()
    if not (r.content.startswith(b"%PDF-") or ctype == "text/html"):
        return Outcome(source, "error", note=f"unexpected content ({ctype}); a bot check or error page is never stored")
    x = policy_kb.extract(source, r.content, ctype)
    if x["extraction_method"] == "HTML_TEXT" and len(x["chunks"]) < 3:
        return Outcome(source, "error", note=f"only {len(x['chunks'])} chunk(s) in the container; the page layout may have changed")
    if prev and prev["text_sha256"] == x["text_sha256"]:
        return _maybe_baseline_facts(conn, deps, run_id, source, prev, rs, fmap, Outcome(source, "unchanged", int(prev["version"]), "text unchanged"))
    old_chunks = deps.policy_store.chunks(source["id"], int(prev["version"])) if prev else []
    version = _store_version(deps, source, r, x, prev)
    new_chunks = [{"chunk_no": c.chunk_no, "page_no": c.page_no, "section": c.section, "text": c.text} for c in x["chunks"]]
    layer_a = diff_text(old_chunks, new_chunks) | {"method": x["extraction_method"], "publication_date_basis": "not stated by the source"}
    old_facts = deps.policy_store.facts(source["id"], int(prev["version"])) if prev and source.get("facts") else None
    facts, note = _extract_facts(conn, deps, source, new_chunks, version) if source.get("facts") else ([], None)
    layer_b = diff_facts(old_facts, facts or []) | ({"note": note} if note else {})
    changed = (layer_b["current"] if layer_b["baseline"] else layer_b["added"] + [f for f in layer_b["current"] if (f["key"], f["value"]) in
                                                                                   {(c["key"], c["new"]) for c in layer_b["changed"]}])
    items = impact(changed, rs, fmap, source, version)
    rid, n = _report(conn, run_id, source, publication_date="unknown", from_v=int(prev["version"]) if prev else None, to_v=version,
                     from_sha=prev["text_sha256"] if prev else None, to_sha=x["text_sha256"], layer_a=layer_a, layer_b=layer_b, items=items)
    return Outcome(source, "changed" if prev else "baseline", version, note, rid, n)


def _maybe_baseline_facts(conn, deps, run_id, source, prev, rs, fmap, out: Outcome) -> Outcome:
    """An unchanged source whose facts were never extracted (e.g. ingested before the monitor existed) gets its facts and a baseline report."""
    if not source.get("facts") or deps.policy_store.facts(source["id"], int(prev["version"])) is not None:
        return out
    chunks = deps.policy_store.chunks(source["id"], int(prev["version"]))
    facts, note = _extract_facts(conn, deps, source, chunks, int(prev["version"]))
    if facts is None:
        out.note = f"{out.note}; {note}"
        return out
    layer_b = diff_facts(None, facts) | ({"note": note} if note else {})
    items = impact(facts, rs, fmap, source, int(prev["version"]))
    rid, n = _report(conn, run_id, source, publication_date="unknown", from_v=None, to_v=int(prev["version"]), from_sha=None, to_sha=prev["text_sha256"],
                     layer_a={"changed": False, "note": "document unchanged; first fact extraction for this version (baseline)"}, layer_b=layer_b, items=items)
    out.status, out.report_id, out.proposals = "baseline", rid, n
    return out


def check_feed(conn, deps, run_id, feed: dict, rs, fmap: dict) -> list[Outcome]:
    r = deps.http.get(feed["url"])
    if r.status_code != 200:
        return [Outcome({"id": feed["id"], "url": feed["url"]}, "error", note=f"feed HTTP {r.status_code}")]
    if b"<!DOCTYPE" in r.content[:4096].upper() or b"<!ENTITY" in r.content.upper():  # an RSS feed never needs one: no entity tricks, ever
        return [Outcome({"id": feed["id"], "url": feed["url"]}, "error", note="feed contains a DOCTYPE or ENTITY declaration; refused")]
    try:  # stdlib parser on Python 3.13 (expat 2.6+): external entities are not resolved and entity expansion is bounded
        items = ET.fromstring(r.content).findall(".//item")
    except ET.ParseError as e:
        return [Outcome({"id": feed["id"], "url": feed["url"]}, "error", note=f"feed is not valid XML ({e})")]
    known = deps.policy_store.known_sources(f"{feed['id']}_")
    out = []
    for it in items:
        title, link, pub = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip(), it.findtext("pubDate")
        m = re.search(r"[?&]Id=(\d+)", link)
        if not m or not link.startswith("https://"):
            continue
        sid = f"{feed['id']}_{m.group(1)}"
        relevant = (any(k.lower() in title.lower() for k in feed["keywords"]) and not any(x.lower() in title.lower() for x in feed["exclude"]))
        if sid in known or not relevant:
            continue
        if len(out) >= feed["max_new_per_run"]:
            break
        source = {"id": sid, "publisher": feed["publisher"], "title": title[:300], "url": link, "container": feed["container"]}
        try:
            g = deps.http.get(link)
            if g.status_code != 200 or g.headers.get("content-type", "").split(";")[0].strip() != "text/html":
                out.append(Outcome(source, "error", note=f"HTTP {g.status_code} {g.headers.get('content-type', '')}"))
                continue
            x = policy_kb.extract(source, g.content, "text/html")
            version = _store_version(deps, source, g, x, None)
            try:
                pub_date = parsedate_to_datetime(pub).date().isoformat() if pub else "unknown"
            except (TypeError, ValueError):
                pub_date = "unknown"
            layer_a = {"changed": True, "new_document": True, "chunks": len(x["chunks"]), "publication_date_basis": "RSS pubDate" if pub else "not stated",
                       "samples": [{"change": "added", "section": c.section, "text": c.text[:200]} for c in x["chunks"][:3]]}
            rid, n = _report(conn, run_id, source, publication_date=pub_date, from_v=None, to_v=version, from_sha=None, to_sha=x["text_sha256"],
                             layer_a=layer_a, layer_b={"baseline": True, "added": [], "removed": [], "changed": [], "current": [],
                                                       "note": "new RBI notification: read by a person; no automatic fact extraction"},
                             items=[], feed_item=True)
            out.append(Outcome(source, "baseline", version, "new lending-related RBI notification", rid, n))
        except Exception as e:  # noqa: BLE001  one bad item never stops the run
            out.append(Outcome(source, "error", note=f"{type(e).__name__}: {e}"[:300]))
    return out


def run(conn, deps, trigger: str = "SCHEDULE", actions_run_url: str | None = None) -> dict:
    from app.policy import ruleset

    with staff_tx(conn, actor_type="SYSTEM"):
        run_id = conn.execute("INSERT INTO monitor_runs (trigger, actions_run_url) VALUES (%s, %s) RETURNING id", (trigger, actions_run_url)).fetchone()["id"]
    outcomes: list[Outcome] = []
    try:
        row = conn.execute("SELECT content FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()
        rs, fmap, catalogue = ruleset.validate(row["content"]), fact_map(), yaml.safe_load(policy_kb.SOURCES_FILE.read_text(encoding="utf-8"))
        for source in catalogue["sources"]:
            try:
                outcomes.append(check_source(conn, deps, run_id, source, rs, fmap))
            except Exception as e:  # noqa: BLE001  one failing source never hides the others; it is recorded on the run
                outcomes.append(Outcome(source, "error", note=f"{type(e).__name__}: {e}"[:300]))
        for feed in catalogue.get("feeds", []):
            try:
                outcomes += check_feed(conn, deps, run_id, feed, rs, fmap)
            except Exception as e:  # noqa: BLE001
                outcomes.append(Outcome({"id": feed["id"], "url": feed["url"]}, "error", note=f"{type(e).__name__}: {e}"[:300]))
        status = "FAILED" if outcomes and all(o.status == "error" for o in outcomes) else "PARTIAL" if any(o.status == "error" for o in outcomes) else "SUCCEEDED"
    except Exception as e:  # noqa: BLE001
        status = "FAILED"
        outcomes.append(Outcome({"id": "run", "url": ""}, "error", note=f"{type(e).__name__}: {e}"[:300]))
    policy_kb.get_kb()._index = None  # retrieval sees the new versions from the next request on
    summary = [{"source": o.source["id"], "status": o.status, "version": o.version, "note": o.note, "report_id": o.report_id, "proposals": o.proposals}
               for o in outcomes]
    with staff_tx(conn, actor_type="SYSTEM"):
        conn.execute("UPDATE monitor_runs SET status = %s, finished_at = now(), sources = %s, reports = %s, proposals = %s, errors = %s WHERE id = %s",
                     (status, json.dumps(summary), sum(1 for o in outcomes if o.report_id), sum(o.proposals for o in outcomes),
                      json.dumps([s for s in summary if s["status"] == "error"]), run_id))
    return {"run_id": str(run_id), "status": status, "sources": summary, "reports": sum(1 for o in outcomes if o.report_id),
            "proposals": sum(o.proposals for o in outcomes)}


def summary_markdown(result: dict, reports: list[dict]) -> str:
    """For the GitHub Actions job summary (and, if enabled, the committed run log): URLs, hashes, classifications, short quotes only."""
    lines = [f"## Policy monitor run `{result['run_id'][:8]}`: {result['status']}", "",
             f"Reports: {result['reports']} | New proposals: {result['proposals']}", "", "| Source | Result | Version | Note |", "|---|---|---|---|"]
    lines += [f"| {s['source']} | {s['status']} | {s['version'] or ''} | {(s['note'] or '').replace('|', '/')[:120]} |" for s in result["sources"]]
    for r in reports:
        lines += ["", f"### {r['classification']}: {r['title']}", f"- Source: {r['url']} (v{r['to_version']}, text sha256 `{r['to_sha256'][:16]}`)",
                  f"- Publication date: {r['publication_date']}"]
        for i in (r["layer_c"] or [])[:8]:
            if i["kind"] in ("RULE_IMPACT", "VERIFICATION_CANDIDATE", "COMPLIANCE_ITEM"):
                target = f" vs `{i['parameter']}` = {i['current_value']} ({i['current_confidence']})" if i.get("parameter") else ""
                lines.append(f"- {i['kind']}: {i['key']} \"{i['value']}\"{target}. Quote: \"{i['quote'][:140]}\"")
    lines += ["", "Nothing is activated automatically: a person reviews proposals, drafts a rule set, the golden cases run, and they activate it."]
    return "\n".join(lines)

