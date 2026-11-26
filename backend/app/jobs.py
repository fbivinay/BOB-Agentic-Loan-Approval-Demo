"""In-process job queue on the Supabase `jobs` table (Render's free tier has no background workers or cron).

Jobs are claimed with FOR UPDATE SKIP LOCKED, so two drains never take the same job; a job stuck RUNNING for 10 minutes is taken again.
Failures retry with backoff up to max_attempts. A drain runs right after an upload (in-process, after the response) and on a schedule
through POST /internal/jobs/drain (GitHub Actions cron).

Handlers:
  process_document   — gets the document's text from one labelled method, extracts fields with Gemini, records an agent run. It only reads
                       documents and writes text, fields and processing states; it never verifies a document or moves an application.
  assess_application — runs the assessment graph (app/assessment.py) and records its recommendation; a human decides.
  sanction_pack      — after a human approval: renders the sanction letter, KFS and EMI schedule, moves the application to SANCTIONED and
                       queues the approval email (app/sanction.py)
  send_email         — sends one outbox email through Resend (app/outbox.py)
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from app import docproc
from app.db import staff_tx

BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)
STALE_MINUTES = 10


class Retry(Exception):
    def __init__(self, note: str, delay: int | None = None):
        super().__init__(note)
        self.note, self.delay = note, delay


@dataclass
class Deps:
    connect: Callable
    storage: object
    store: object
    gemini: object | None  # None when GEMINI_API_KEY is not configured
    model: str
    gemini_cap: int
    kick: bool = True  # drain right after an upload (in-process, after the response)
    lakehouse: object | None = None  # assessment: customer 360 as the app identity
    kb: object | None = None  # assessment: the policy knowledge base
    today: Callable = date.today
    generated: object | None = None  # sanction pack: the private generated-documents bucket
    resend: object | None = None  # None when RESEND_API_KEY is not configured
    email_from: str = "onboarding@resend.dev"
    email_redirect_to: str = ""
    resend_cap: int = 80


def enqueue(conn, kind: str, payload: dict, application_id, key: str) -> None:
    conn.execute("INSERT INTO jobs (kind, payload, application_id, idempotency_key) VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
                 (kind, json.dumps(payload), application_id, key))


def claim(conn) -> dict | None:
    return conn.execute(
        "UPDATE jobs SET status = 'RUNNING', attempts = attempts + 1, locked_at = now(), updated_at = now() WHERE id = ("
        " SELECT id FROM jobs WHERE (status = 'QUEUED' AND run_after <= now()) OR (status = 'RUNNING' AND locked_at < now() - make_interval(mins => %s))"
        " ORDER BY run_after FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *", (STALE_MINUTES,)).fetchone()


def drain(deps: Deps, limit: int = 10) -> list[dict]:
    """Run up to `limit` ready jobs. Returns what happened to each."""
    results = []
    conn = deps.connect()
    try:
        for _ in range(limit):
            job = claim(conn)
            if job is None:
                break
            try:
                HANDLERS[job["kind"]](conn, job, deps)
                conn.execute("UPDATE jobs SET status = 'DONE', locked_at = NULL, last_error = NULL, updated_at = now() WHERE id = %s", (job["id"],))
                results.append({"job": str(job["id"]), "kind": job["kind"], "result": "DONE"})
            except Exception as e:  # noqa: BLE001  every failure is recorded on the job; nothing is swallowed silently
                note = e.note if isinstance(e, Retry) else f"{type(e).__name__}: {e}"[:500]
                final = job["attempts"] >= job["max_attempts"]
                delay = (e.delay if isinstance(e, Retry) and e.delay else None) or BACKOFF_SECONDS[min(job["attempts"], len(BACKOFF_SECONDS)) - 1]
                conn.execute("UPDATE jobs SET status = %s, locked_at = NULL, last_error = %s, run_after = now() + make_interval(secs => %s), updated_at = now() "
                             "WHERE id = %s", ("FAILED" if final else "QUEUED", note, delay, job["id"]))
                if final:
                    on_final_failure(conn, job, note)
                results.append({"job": str(job["id"]), "kind": job["kind"], "result": "FAILED" if final else "RETRY", "note": note})
    finally:
        conn.close()
    return results


def on_final_failure(conn, job: dict, note: str) -> None:
    """Out of retries: the person takes over. The document goes to manual review with the reason; nothing is substituted."""
    if job["kind"] == "send_email":
        from app.outbox import on_final_failure as email_failed

        email_failed(conn, job, note)
    if job["kind"] == "process_document":
        with staff_tx(conn, actor_type="SYSTEM"):
            conn.execute("UPDATE documents SET status = 'NEEDS_MANUAL_REVIEW', processing_note = %s WHERE id = %s AND status IN ('UPLOADED', 'AWAITING_OCR')",
                         (f"Automatic processing stopped after retries ({note}). Key the values from the document image.", job["payload"]["document_id"]))


# ---------------------------------------------------------------- document processing
def _set_doc(conn, doc_id, status: str, note: str, **cols) -> None:
    extra = "".join(f", {k} = %s" for k in cols)
    with staff_tx(conn, actor_type="SYSTEM"):
        conn.execute(f"UPDATE documents SET status = %s, processing_note = %s{extra} WHERE id = %s", (status, note, *cols.values(), doc_id))  # noqa: S608


def _save_fields(conn, doc_id, rows: list[dict], source: str, text_method: str, model: str | None) -> None:
    with staff_tx(conn, actor_type="AGENT" if source == "AI_EXTRACTED" else "SYSTEM"):
        for r in rows:
            conn.execute("INSERT INTO document_fields (document_id, field, value, source, text_method, page_no, snippet, check_note, model) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (document_id, field) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, "
                         "text_method = EXCLUDED.text_method, page_no = EXCLUDED.page_no, snippet = EXCLUDED.snippet, check_note = EXCLUDED.check_note, model = EXCLUDED.model "
                         "WHERE document_fields.source <> 'MANUAL'",  # a value a person entered is never overwritten
                         (doc_id, r["field"], r["value"], source, text_method, r.get("page_no"), r.get("snippet"), r.get("check_note"), model))


def process_document(conn, job: dict, deps: Deps) -> None:
    doc_id = job["payload"]["document_id"]
    doc = conn.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()
    if doc is None or doc["status"] not in ("UPLOADED", "AWAITING_OCR"):
        return  # withdrawn, already processed, or taken over by a person
    steps: list[dict] = []

    def step(name: str, **detail):
        steps.append({"step": name, "at": datetime.now(timezone.utc).isoformat(), **detail})

    with staff_tx(conn, actor_type="AGENT"):
        run = conn.execute("INSERT INTO agent_runs (application_id, graph, steps) VALUES (%s, 'DOCUMENT', '[]') RETURNING id", (doc["application_id"],)).fetchone()["id"]

    def finish(status: str, output: dict, error: str | None = None):
        with staff_tx(conn, actor_type="AGENT"):
            conn.execute("UPDATE agent_runs SET status = %s, steps = %s, output = %s, model = %s, error = %s, finished_at = now() WHERE id = %s",
                         (status, json.dumps(steps), json.dumps(output), deps.model if any(s["step"] == "extract" for s in steps) else None, error, run))

    try:
        _process(conn, doc, job, deps, step)
        finish("DONE", {"document_id": str(doc_id)})
    except Retry as e:
        finish("FAILED", {"document_id": str(doc_id), "will_retry": job["attempts"] < job["max_attempts"]}, e.note)
        raise
    except Exception as e:
        finish("FAILED", {"document_id": str(doc_id)}, f"{type(e).__name__}: {e}"[:500])
        raise


def _process(conn, doc: dict, job: dict, deps: Deps, step) -> None:
    doc_id, spec = doc["id"], docproc.FIELDS.get(doc["doc_type"], {})
    if not spec:
        step("no_fields", doc_type=doc["doc_type"])
        _set_doc(conn, doc_id, "NEEDS_MANUAL_REVIEW", "Check this document by eye against the applicant; there are no fields to extract.")
        return

    # 1. text, from one labelled method
    data = deps.storage.get(doc["storage_path"])
    pages, method, engine = None, None, None
    if doc["mime_type"] == "application/pdf":
        pages = docproc.embedded_text(data)
        if pages:
            method, engine = "EMBEDDED_TEXT", "pypdf text layer (not OCR)"
    if not pages:
        pre = deps.store.precomputed_pages(doc["sha256"])
        if pre:
            pages, method = [p["text"] for p in pre], "PRECOMPUTED_UNLIMITED_OCR"
            engine = f"{pre[0]['engine']} {pre[0]['engine_version'] or ''}".strip()
    if not pages:
        prepared = deps.store.prepared_values(doc["sha256"])
        if prepared:
            step("text", method="PREPARED_DEMO_TEXT")
            rows = [{"field": k, "value": docproc.normalise(spec[k][0], prepared.get(k))[0], "check_note": "Prepared demo data — not OCR"} for k in spec]
            _save_fields(conn, doc_id, rows, "PREPARED_DEMO_TEXT", "PREPARED_DEMO_TEXT", None)
            _set_doc(conn, doc_id, "NEEDS_MANUAL_REVIEW", "Prepared demo data — not OCR. Confirm or re-key each value from the document image before verifying.",
                     text_method="PREPARED_DEMO_TEXT")
            return
        step("text", method=None, result="no text layer and no Unlimited-OCR result for this file")
        _set_doc(conn, doc_id, "AWAITING_OCR", "Scanned document: waiting for Unlimited-OCR output. It goes to manual review if none arrives.")
        raise Retry("awaiting OCR output for a scanned document")
    step("text", method=method, engine=engine, pages=len(pages))
    if not deps.store.document_pages(str(doc_id)):
        deps.store.append("ocr_pages", [{"source_sha256": doc["sha256"], "document_id": str(doc_id), "application_id": str(doc["application_id"]), "page_no": i,
                                         "text": t, "extraction_method": method, "engine": engine, "engine_version": None} for i, t in enumerate(pages, 1)])
    with staff_tx(conn, actor_type="SYSTEM"):
        conn.execute("UPDATE documents SET text_method = %s, page_count = %s WHERE id = %s", (method, len(pages), doc_id))

    # 2. fields, with Gemini (cached by file hash, model and prompt version)
    cached = deps.store.cached_extraction(doc["sha256"], deps.model, docproc.PROMPT_VERSION)
    redacted, mapping = docproc.redact(pages)
    if cached:
        extraction = docproc.Extraction.model_validate(cached)
        step("extract", cached=True)
    else:
        if deps.gemini is None:
            step("extract", result="GEMINI_API_KEY not configured")
            _set_doc(conn, doc_id, "NEEDS_MANUAL_REVIEW", "AI extraction is not configured. Key the values from the document.")
            return
        if not conn.execute("SELECT take_budget('gemini', %s) AS ok", (deps.gemini_cap,)).fetchone()["ok"]:
            raise Retry(f"daily Gemini budget of {deps.gemini_cap} calls reached; retrying later", delay=3600)
        try:
            extraction = deps.gemini.extract(doc["doc_type"], redacted)
        except Exception as e:
            if docproc.is_rate_limit(e):
                raise Retry("Gemini rate limit or temporary overload; retrying later") from e
            raise
        step("extract", cached=False, model=deps.model, pan_placeholders=len(mapping))
        deps.store.append("extraction_outputs", [{"source_sha256": doc["sha256"], "document_id": str(doc_id), "application_id": str(doc["application_id"]),
                                                  "model": deps.model, "prompt_version": docproc.PROMPT_VERSION, "output_json": extraction.model_dump_json()}])
    rows = docproc.check_and_shape(doc["doc_type"], extraction, pages, mapping)
    _save_fields(conn, doc_id, rows, "AI_EXTRACTED", method, deps.model)
    flagged = sum(1 for r in rows if r["check_note"])
    step("check", fields=len(rows), flagged=flagged)
    _set_doc(conn, doc_id, "EXTRACTED", f"{len(rows)} fields extracted from {method.replace('_', ' ').lower()}; {flagged} flagged for a closer look. "
                                         "A person reviews and verifies them.")


def assess_application(conn, job: dict, deps: Deps) -> None:
    from app.assessment import assess_job

    assess_job(conn, job, deps)


def sanction_pack(conn, job: dict, deps: Deps) -> None:
    from app.sanction import sanction_job

    sanction_job(conn, job, deps)


def send_email(conn, job: dict, deps: Deps) -> None:
    from app.outbox import send_job

    send_job(conn, job, deps)


HANDLERS = {"process_document": process_document, "assess_application": assess_application, "sanction_pack": sanction_pack, "send_email": send_email}


def get_job_deps() -> Deps:
    from app.config import settings
    from app.db import connect
    from app.ocr_store import get_ocr_store
    from app.storage import get_storage

    model = settings.gemini_model or "gemini-3.5-flash-lite"  # fast and free on an unbilled key (checked 26 Nov 2026); override with GEMINI_MODEL
    from app.lakehouse import get_lakehouse
    from app.outbox import Resend
    from app.policy_kb import get_kb
    from app.storage import get_generated_storage

    return Deps(connect=connect, storage=get_storage(), store=get_ocr_store(),
                gemini=docproc.Gemini(settings.gemini_api_key, model) if settings.gemini_api_key else None, model=model, gemini_cap=settings.gemini_daily_cap,
                lakehouse=get_lakehouse(), kb=get_kb(), generated=get_generated_storage(),
                resend=Resend(settings.resend_api_key) if settings.resend_api_key else None, email_from=settings.email_from,
                email_redirect_to=settings.email_redirect_to, resend_cap=settings.resend_daily_cap)
