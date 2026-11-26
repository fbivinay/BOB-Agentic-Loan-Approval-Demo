"""System status for Assistant and Branch Managers (system.view): policy monitoring health, the job queue, the OCR and manual-review
queue, email delivery, daily budgets, and the analytics outbox. Read-only."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import analytics
from app.auth import Staff, require_permission
from app.config import settings
from app.db import get_conn
from app.routers.policy import _stale

router = APIRouter(tags=["system"])


@router.get("/system/health")
def health(staff: Staff = Depends(require_permission("system.view")), conn=Depends(get_conn)):
    q = lambda sql, *p: conn.execute(sql, p).fetchall()  # noqa: E731
    last_run = conn.execute("SELECT id, status, trigger, started_at, finished_at, reports, proposals, errors, actions_run_url FROM monitor_runs "
                            "ORDER BY started_at DESC LIMIT 1").fetchone()
    monitor = _stale(conn) | {"last_run": None if last_run is None else {
        "id": str(last_run["id"]), "status": last_run["status"], "trigger": last_run["trigger"], "started_at": last_run["started_at"].isoformat(),
        "finished_at": last_run["finished_at"].isoformat() if last_run["finished_at"] else None, "reports": last_run["reports"],
        "proposals": last_run["proposals"], "errors": last_run["errors"], "actions_run_url": last_run["actions_run_url"]}}
    jobs = q("SELECT kind, status, count(*) AS n, min(run_after) FILTER (WHERE status = 'QUEUED') AS oldest_queued FROM jobs GROUP BY kind, status ORDER BY kind, status")
    failed = q("SELECT kind, last_error, updated_at, application_id FROM jobs WHERE status = 'FAILED' ORDER BY updated_at DESC LIMIT 10")
    docs = q("SELECT status, count(*) AS n FROM documents WHERE status IN ('UPLOADED', 'AWAITING_OCR', 'NEEDS_MANUAL_REVIEW', 'EXTRACTED') GROUP BY status")
    emails = q("SELECT status, count(*) AS n FROM email_outbox GROUP BY status")
    usage = q("SELECT service, used FROM usage_counters WHERE day = (now() AT TIME ZONE 'Asia/Kolkata')::date")
    analytics = q("SELECT status, count(*) AS n FROM analytics_outbox GROUP BY status")
    policy = conn.execute("SELECT (SELECT count(*) FROM policy_change_reports WHERE status = 'NEW') AS new_reports, "
                          "(SELECT count(*) FROM policy_change_proposals WHERE status = 'OPEN') AS open_proposals").fetchone()
    caps = {"gemini": settings.gemini_daily_cap, "resend": settings.resend_daily_cap, "upload": settings.upload_daily_cap}
    alerts = []
    if monitor["stale"]:
        alerts.append("Policy monitoring is stale: no successful run within twice the daily interval. Check the GitHub Actions workflow "
                      "(scheduled workflows are disabled after 60 days without repository activity; re-enable with `gh workflow enable policy-monitor.yml`).")
    if failed:
        alerts.append(f"{len(failed)} job(s) stopped after retries; see failed_jobs.")
    if any(e["status"] in ("BOUNCED", "FAILED", "COMPLAINED") for e in emails):
        alerts.append("Some emails bounced, failed or drew a complaint; the Loan Officer can correct and resend from the application.")
    for u in usage:
        if u["service"] in caps and u["used"] >= caps[u["service"]]:
            alerts.append(f"The daily {u['service']} budget ({caps[u['service']]}) is used up; work waits until tomorrow (IST).")
    return {
        "alerts": alerts, "monitor": monitor,
        "jobs": [{"kind": j["kind"], "status": j["status"], "count": j["n"], "oldest_queued": j["oldest_queued"].isoformat() if j["oldest_queued"] else None} for j in jobs],
        "failed_jobs": [{"kind": f["kind"], "error": f["last_error"], "at": f["updated_at"].isoformat(), "application_id": str(f["application_id"]) if f["application_id"] else None}
                        for f in failed],
        "documents": {d["status"]: d["n"] for d in docs}, "emails": {e["status"]: e["n"] for e in emails},
        "budgets_today": {s: {"used": next((u["used"] for u in usage if u["service"] == s), 0), "cap": c} for s, c in caps.items()},
        "analytics_outbox": {a["status"]: a["n"] for a in analytics},
        "policy": {"new_reports": policy["new_reports"], "open_proposals": policy["open_proposals"]},
    }


class Retry(BaseModel):
    event_ids: list[UUID] | None = Field(default=None, max_length=500, description="omit: every FAILED event")


@router.get("/system/analytics")
def analytics_sync_status(staff: Staff = Depends(require_permission("system.view")), conn=Depends(get_conn)):
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, count(*) AS n FROM analytics_outbox GROUP BY status").fetchall()}
    failed = conn.execute("SELECT event_id, event_type, application_id, attempts, last_error, occurred_at FROM analytics_outbox WHERE status = 'FAILED' "
                          "ORDER BY occurred_at DESC LIMIT 100").fetchall()
    oldest = conn.execute("SELECT min(occurred_at) AS t FROM analytics_outbox WHERE status = 'PENDING'").fetchone()["t"]
    return {"counts": counts, "oldest_pending": oldest.isoformat() if oldest else None,
            "failed": [{"event_id": str(f["event_id"]), "event_type": f["event_type"], "application_id": str(f["application_id"]) if f["application_id"] else None,
                        "attempts": f["attempts"], "error": f["last_error"], "occurred_at": f["occurred_at"].isoformat()} for f in failed]}


@router.post("/system/analytics/retry")
def analytics_retry(body: Retry, staff: Staff = Depends(require_permission("system.view")), conn=Depends(get_conn)):
    return {"requeued": analytics.retry_failed(conn, staff, body.event_ids)}
