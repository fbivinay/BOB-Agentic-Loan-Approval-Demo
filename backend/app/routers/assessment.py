"""The AI assessment of an application, and the policy knowledge base.

  GET  /applications/{id}/assessment   the latest assessment run: recommendation, rule-by-rule results, evidence, citations, the agent's steps
                                       and the eligible terms the engine computed. Every view says "AI recommendation, human decides".
  POST /applications/{id}/assessment   queue the assessment again (Loan Officer) when the last attempt failed; only while DOCS_VERIFIED
  GET  /policy/sources                 the sources in the knowledge base, latest version of each
  GET  /policy/search?q=               passages from the knowledge base, with citations

Nothing here records a decision. The recommendation is written only by the assessment job through app_record_assessment.
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app import jobs, policy_kb
from app.assessment import HUMAN_DECIDES
from app.auth import Staff, require_permission
from app.db import get_conn, staff_tx
from app.routers.applications import load

router = APIRouter(tags=["assessment"])


class Requeue(BaseModel):
    expected_version: int


def _pending(conn, app_id) -> dict | None:
    return conn.execute("SELECT status, attempts, max_attempts, last_error, run_after FROM jobs WHERE application_id = %s AND kind = 'assess_application' "
                        "AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1", (app_id,)).fetchone()


@router.get("/applications/{app_id}/assessment")
def get_assessment(app_id: UUID, staff: Staff = Depends(require_permission("assessment.view")), conn=Depends(get_conn)):
    a = load(conn, app_id)
    run = conn.execute("SELECT id, status, steps, output, recommendation, model, error, started_at, finished_at FROM agent_runs "
                       "WHERE application_id = %s AND graph = 'ASSESSMENT' ORDER BY (id = %s) DESC, started_at DESC LIMIT 1",
                       (app_id, a["ai_recommendation_run_id"])).fetchone()
    pending = _pending(conn, app_id)
    terms = conn.execute("SELECT amount, rate_bp, tenure_months, seq, created_at FROM application_terms WHERE application_id = %s AND kind = 'ELIGIBLE' "
                         "ORDER BY seq DESC LIMIT 1", (app_id,)).fetchone()
    last_job = conn.execute("SELECT status, attempts, last_error FROM jobs WHERE application_id = %s AND kind = 'assess_application' ORDER BY created_at DESC LIMIT 1",
                            (app_id,)).fetchone()
    return {
        "application": {"id": str(a["id"]), "reference": a["reference"], "status": a["status"], "version": a["version"],
                        "ai_recommendation": a["ai_recommendation"], "assessed_at": a["assessed_at"].isoformat() if a["assessed_at"] else None},
        "label": HUMAN_DECIDES,
        "state": "RECORDED" if a["ai_recommendation"] else "PENDING" if pending else "FAILED" if last_job and last_job["status"] == "FAILED" else "NOT_STARTED",
        "job": {k: (str(v) if k == "run_after" and v else v) for k, v in (pending or last_job or {}).items()} or None,
        "run": None if run is None else {"id": str(run["id"]), "status": run["status"], "recommendation": run["recommendation"], "model": run["model"],
                                         "error": run["error"], "started_at": run["started_at"].isoformat(),
                                         "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None, "steps": run["steps"], "output": run["output"]},
        "eligible_terms": None if terms is None else {"amount": str(terms["amount"]), "rate_bp": terms["rate_bp"], "tenure_months": terms["tenure_months"],
                                                       "label": "Computed by the policy engine under demo parameters; a human sets approved terms"},
    }


@router.post("/applications/{app_id}/assessment", status_code=202)
def requeue_assessment(app_id: UUID, body: Requeue, background: BackgroundTasks, staff: Staff = Depends(require_permission("application.forward")),
                       conn=Depends(get_conn), deps=Depends(jobs.get_job_deps)):
    a = load(conn, app_id)
    if a["version"] != body.expected_version:
        raise HTTPException(409, "The application changed since you opened it. Reload it and try again.")
    if a["status"] != "DOCS_VERIFIED":
        raise HTTPException(409, f"An assessment runs only on a documents-verified application (status is {a['status']})")
    if _pending(conn, app_id):
        raise HTTPException(409, "An assessment is already queued or running")
    with staff_tx(conn, staff.user_id, staff.role):
        jobs.enqueue(conn, "assess_application", {"application_id": str(app_id), "requested_by": str(staff.user_id)}, app_id, f"assess:{app_id}:{uuid4()}")
    if deps.kick:
        background.add_task(jobs.drain, deps, 3)
    return {"queued": True}


@router.get("/policy/sources")
def policy_sources(staff: Staff = Depends(require_permission("policy.view"))):
    from app.config import settings
    from app.lakehouse import _run

    rows = _run(f"SELECT source_id, ANY_VALUE(publisher) AS publisher, ANY_VALUE(title) AS title, ANY_VALUE(url) AS url, ANY_VALUE(version) AS version, "
                f"ANY_VALUE(fetched_at) AS fetched_at, ANY_VALUE(extraction_method) AS extraction_method, COUNT(*) AS chunks "
                f"FROM `{settings.gcp_project}.lh_curated.policy_chunks` GROUP BY source_id ORDER BY source_id", {})
    return {"sources": [{**r, "fetched_at": str(r["fetched_at"])} for r in rows],
            "note": "Official public sources. Retrieved text is evidence for the reviewer; rules change only through a human-approved rule set."}


@router.get("/policy/search")
def policy_search(q: str = Query(min_length=3, max_length=200), k: int = Query(default=5, ge=1, le=10),
                  staff: Staff = Depends(require_permission("policy.view")), kb=Depends(policy_kb.get_kb)):
    return {"query": q, "results": [policy_kb.cite(h) for h in kb.index().search(q, k)]}
