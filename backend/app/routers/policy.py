"""Policy monitoring: runs, change reports, proposals, drafting a candidate rule set, golden cases, activation.

  POST /internal/monitor/run                    GitHub Actions (X-Internal-Token): run the monitor; returns the result and a markdown summary
  GET  /policy/monitor/runs                     recent runs (every role: policy.view)
  GET  /policy/reports, /policy/reports/{id}    change reports with the three layers
  POST /policy/reports/{id}/review              Branch Manager (policy.activate): the compliance checklist step, with a note
  GET  /policy/proposals                        open and closed proposals with their evidence
  POST /policy/proposals/{id}/reject            Branch Manager: with a reason
  POST /policy/rule-sets/draft                  Branch Manager: a candidate rule set = the ACTIVE one + chosen parameter changes, citing proposals;
                                                validated and run through the golden cases at once; stored as DRAFT with the result
  GET  /policy/rule-sets                        versions with status, test result and the differences from the active one
  POST /policy/rule-sets/{id}/activate          Branch Manager: with a reason; the database refuses unless the golden cases passed.
                                                In-flight applications keep the version frozen at their intake.

Policy activation by the Branch Manager is a configurable demo permission, not Bank of Baroda's authorization structure. Nothing here
changes a rule without these human steps, and the monitor itself can only write reports and proposals.
"""

import copy
import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import jobs, monitor
from app.auth import Staff, require_permission
from app.config import settings
from app.db import get_conn, staff_tx
from app.policy import golden, ruleset

router = APIRouter(tags=["policy"])
SCHEME = "baroda_personal_loan"
PERMISSION_LABEL = "Policy activation by the Branch Manager is a configurable demo permission, not Bank of Baroda's authorization structure."


class Review(BaseModel):
    note: str = Field(min_length=10, max_length=1000)


class ParamChange(BaseModel):
    value: object
    confidence: Literal["DEMO_ASSUMPTION", "VERIFIED"]
    segment: Literal["ETB_EST", "ETB_THIN", "ETB_NTC", "NTB_EST", "NTB_THIN", "NTB_NTC"] | None = Field(default=None, description="omit: the common value")
    proposal_id: UUID | None = Field(default=None, description="the proposal whose official evidence this change relies on (required for VERIFIED)")


class Draft(BaseModel):
    changes: dict[str, ParamChange] = Field(min_length=1, max_length=20)
    note: str = Field(min_length=10, max_length=1000)


class Activate(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


def _internal(token: str) -> None:
    if not settings.internal_api_token:
        raise HTTPException(503, "Internal endpoints are not configured")
    if not hmac.compare_digest(token.encode(), settings.internal_api_token.encode()):
        raise HTTPException(401, "Invalid internal token")


def _report_view(r: dict, full: bool = False) -> dict:
    base = {k: (str(r[k]) if k in ("id", "run_id", "reviewed_by") and r[k] else r[k].isoformat() if k in ("created_at", "reviewed_at") and r[k] else r[k])
            for k in ("id", "run_id", "source_id", "publisher", "title", "url", "publication_date", "from_version", "to_version", "from_sha256", "to_sha256",
                      "classification", "status", "reviewed_by", "reviewed_at", "review_note", "created_at")}
    items = r["layer_c"] or []
    base["counts"] = {k: sum(1 for i in items if i["kind"] == k) for k in ("RULE_IMPACT", "VERIFICATION_CANDIDATE", "COMPLIANCE_ITEM", "CONFIRMED", "NO_RULE")}
    if full:
        base |= {"layer_a": r["layer_a"], "layer_b": r["layer_b"], "layer_c": items}
    return base


# ---------------------------------------------------------------- the run (GitHub Actions)
@router.post("/internal/monitor/run")
def monitor_run(x_internal_token: str = Header(default=""), x_actions_run_url: str | None = Header(default=None), trigger: Literal["SCHEDULE", "MANUAL"] = "SCHEDULE",
                conn=Depends(get_conn), deps=Depends(jobs.get_job_deps)):
    _internal(x_internal_token)
    url = x_actions_run_url if x_actions_run_url and x_actions_run_url.startswith("https://github.com/") else None
    result = monitor.run(conn, deps, trigger, url)
    reports = conn.execute("SELECT * FROM policy_change_reports WHERE run_id = %s ORDER BY created_at", (result["run_id"],)).fetchall()
    stale = _stale(conn)
    return result | {"stale": stale, "markdown": monitor.summary_markdown(result, reports)}


def _stale(conn, interval_hours: int = 24) -> dict:
    """'Stale monitoring' when no run succeeded within twice the interval (plan section 8)."""
    last = conn.execute("SELECT max(finished_at) AS at FROM monitor_runs WHERE status IN ('SUCCEEDED', 'PARTIAL')").fetchone()["at"]
    hours = conn.execute("SELECT extract(epoch FROM now() - %s) / 3600 AS h", (last,)).fetchone()["h"] if last else None
    return {"last_success": last.isoformat() if last else None, "hours_since": round(float(hours), 1) if hours is not None else None,
            "stale": last is None or float(hours) > 2 * interval_hours, "interval_hours": interval_hours}


@router.get("/policy/monitor/runs")
def runs(limit: int = Query(default=20, ge=1, le=100), staff: Staff = Depends(require_permission("policy.view")), conn=Depends(get_conn)):
    rows = conn.execute("SELECT * FROM monitor_runs ORDER BY started_at DESC LIMIT %s", (limit,)).fetchall()
    return {"runs": [{"id": str(r["id"]), "trigger": r["trigger"], "status": r["status"], "started_at": r["started_at"].isoformat(),
                      "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None, "reports": r["reports"], "proposals": r["proposals"],
                      "sources": r["sources"], "errors": r["errors"], "actions_run_url": r["actions_run_url"]} for r in rows], "health": _stale(conn)}


# ---------------------------------------------------------------- reports
@router.get("/policy/reports")
def reports(status: Literal["NEW", "REVIEWED"] | None = None, classification: str | None = None, limit: int = Query(default=50, ge=1, le=200),
            staff: Staff = Depends(require_permission("policy.view")), conn=Depends(get_conn)):
    sql, params = "SELECT * FROM policy_change_reports WHERE true", []
    if status:
        sql, params = sql + " AND status = %s", params + [status]
    if classification:
        sql, params = sql + " AND classification = %s", params + [classification]
    rows = conn.execute(sql + " ORDER BY created_at DESC LIMIT %s", (*params, limit)).fetchall()
    return {"reports": [_report_view(r) for r in rows]}


@router.get("/policy/reports/{report_id}")
def report(report_id: UUID, staff: Staff = Depends(require_permission("policy.view")), conn=Depends(get_conn)):
    r = conn.execute("SELECT * FROM policy_change_reports WHERE id = %s", (report_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "Report not found")
    props = conn.execute("SELECT id, kind, parameter, status FROM policy_change_proposals WHERE report_id = %s ORDER BY parameter", (report_id,)).fetchall()
    return _report_view(r, full=True) | {"proposals": [{**p, "id": str(p["id"])} for p in props]}


@router.post("/policy/reports/{report_id}/review")
def review(report_id: UUID, body: Review, staff: Staff = Depends(require_permission("policy.activate")), conn=Depends(get_conn)):
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("UPDATE policy_change_reports SET status = 'REVIEWED', reviewed_by = %s, reviewed_at = now(), review_note = %s WHERE id = %s AND status = 'NEW' "
                         "RETURNING id", (staff.user_id, body.note.strip(), report_id)).fetchone()
    if r is None:
        raise HTTPException(409, "Report not found or already reviewed")
    return {"status": "REVIEWED"}


# ---------------------------------------------------------------- proposals
@router.get("/policy/proposals")
def proposals(status: Literal["OPEN", "DRAFTED", "ACTIVATED", "REJECTED"] | None = None, staff: Staff = Depends(require_permission("policy.view")),
              conn=Depends(get_conn)):
    rows = conn.execute("SELECT p.*, r.classification, r.title, r.url FROM policy_change_proposals p JOIN policy_change_reports r ON r.id = p.report_id "
                        + ("WHERE p.status = %s " if status else "") + "ORDER BY p.created_at DESC", (status,) if status else ()).fetchall()
    return {"proposals": [{"id": str(p["id"]), "kind": p["kind"], "parameter": p["parameter"], "current_value": p["current_value"],
                           "current_confidence": p["current_confidence"], "official_value": p["official_value"], "evidence": p["evidence"], "status": p["status"],
                           "report": {"id": str(p["report_id"]), "classification": p["classification"], "title": p["title"], "url": p["url"]},
                           "drafted_rule_set_id": str(p["drafted_rule_set_id"]) if p["drafted_rule_set_id"] else None, "decision_note": p["decision_note"],
                           "created_at": p["created_at"].isoformat()} for p in rows],
            "label": "Proposals come from official sources; a person decides whether and how any rule changes. " + PERMISSION_LABEL}


@router.post("/policy/proposals/{proposal_id}/reject")
def reject(proposal_id: UUID, body: Review, staff: Staff = Depends(require_permission("policy.activate")), conn=Depends(get_conn)):
    with staff_tx(conn, staff.user_id, staff.role):
        r = conn.execute("UPDATE policy_change_proposals SET status = 'REJECTED', decided_by = %s, decision_note = %s WHERE id = %s AND status IN ('OPEN', 'DRAFTED') "
                         "RETURNING id", (staff.user_id, body.note.strip(), proposal_id)).fetchone()
    if r is None:
        raise HTTPException(409, "Proposal not found or already closed")
    return {"status": "REJECTED"}


# ---------------------------------------------------------------- rule sets
def _active(conn) -> dict:
    row = conn.execute("SELECT * FROM policy_rule_sets WHERE scheme = %s AND status = 'ACTIVE'", (SCHEME,)).fetchone()
    if row is None:
        raise HTTPException(503, "No active rule set")
    return row


def _differences(old: dict, new: dict) -> list[dict]:
    out = []
    for name, p in new["parameters"].items():
        o = old["parameters"].get(name)
        if o != p:
            out.append({"parameter": name, "segment": None, "from": o, "to": p})
    for seg, over in new.get("segment_overrides", {}).items():
        for name, p in over.items():
            o = old.get("segment_overrides", {}).get(seg, {}).get(name)
            if o != p:
                out.append({"parameter": name, "segment": seg, "from": o, "to": p})
    return out


@router.post("/policy/rule-sets/draft", status_code=201)
def draft(body: Draft, staff: Staff = Depends(require_permission("policy.activate")), conn=Depends(get_conn)):
    active = _active(conn)
    content = copy.deepcopy(active["content"])
    used = {}
    for name, ch in body.changes.items():
        if name not in content["parameters"]:
            raise HTTPException(422, f"Unknown parameter {name}")
        prop = None
        if ch.proposal_id:
            prop = conn.execute("SELECT * FROM policy_change_proposals WHERE id = %s AND status = 'OPEN' AND parameter = %s", (ch.proposal_id, name)).fetchone()
            if prop is None:
                raise HTTPException(422, f"{name}: proposal {ch.proposal_id} is not an open proposal for this parameter")
            used[str(prop["id"])] = prop
        base = dict(content["parameters"][name])
        new = {**base, "value": ch.value, "confidence": ch.confidence}
        if ch.confidence == "VERIFIED":
            if prop is None:
                raise HTTPException(422, f"{name}: VERIFIED needs the proposal whose official source confirms the value")
            ev = prop["evidence"]
            try:
                matches = Decimal(str(ch.value)) == Decimal(str(prop["official_value"]["number"]))
            except (InvalidOperation, ValueError):
                matches = False
            if not matches:
                raise HTTPException(422, f"{name}: a VERIFIED value must be the official figure {prop['official_value']['number']} from the cited source")
            new["source"] = {"url": ev["url"], "version": ev["version"], "quote": ev["quote"][:500], "condition": ev.get("condition"), "proposal_id": str(prop["id"])}
        else:
            new.pop("source", None)
        new["note"] = (base.get("note", "") + f" [changed in v{active['version'] + 1}: {body.note.strip()[:200]}]").strip()
        if ch.segment:
            content.setdefault("segment_overrides", {}).setdefault(ch.segment, {})[name] = new
        else:
            content["parameters"][name] = new
    version = conn.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM policy_rule_sets WHERE scheme = %s", (SCHEME,)).fetchone()["v"]
    content["version"] = version
    result = golden.run(content)  # schema, invariants and golden cases, against exactly this candidate
    if result.get("schema", "").startswith("invalid"):
        raise HTTPException(422, f"The candidate rule set is invalid: {result['schema'][9:]}")
    sha = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    refs = [{"proposal_id": pid, "url": p["evidence"]["url"], "version": p["evidence"]["version"]} for pid, p in used.items()]
    with staff_tx(conn, staff.user_id, staff.role):
        rs_id = conn.execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256, source_references, test_result, created_by) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                             (SCHEME, version, json.dumps(content), sha, json.dumps(refs + [{"note": body.note.strip()}]), json.dumps(result), staff.user_id)).fetchone()["id"]
        for pid in used:
            conn.execute("UPDATE policy_change_proposals SET status = 'DRAFTED', drafted_rule_set_id = %s, decided_by = %s, decision_note = %s WHERE id = %s",
                         (rs_id, staff.user_id, body.note.strip(), pid))
    return {"id": str(rs_id), "version": version, "status": "DRAFT", "tests": result, "differences": _differences(active["content"], content),
            "label": PERMISSION_LABEL}


@router.get("/policy/rule-sets")
def rule_sets(staff: Staff = Depends(require_permission("policy.view")), conn=Depends(get_conn)):
    rows = conn.execute("SELECT r.*, s.full_name AS approver FROM policy_rule_sets r LEFT JOIN staff_profiles s ON s.user_id = r.approved_by WHERE scheme = %s "
                        "ORDER BY version DESC", (SCHEME,)).fetchall()
    active = next((r for r in rows if r["status"] == "ACTIVE"), None)
    return {"rule_sets": [{"id": str(r["id"]), "version": r["version"], "status": r["status"], "sha256": r["content_sha256"][:12],
                           "tests": {k: (r["test_result"] or {}).get(k) for k in ("passed", "suite", "counts", "summary", "ran_at")},
                           "approved_by": r["approver"], "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
                           "activation_reason": r["activation_reason"], "source_references": r["source_references"],
                           "differences_from_active": _differences(active["content"], r["content"]) if active and r["status"] == "DRAFT" else None}
                          for r in rows], "label": PERMISSION_LABEL}


@router.post("/policy/rule-sets/{rule_set_id}/activate")
def activate(rule_set_id: UUID, body: Activate, staff: Staff = Depends(require_permission("policy.activate")), conn=Depends(get_conn)):
    r = conn.execute("SELECT * FROM policy_rule_sets WHERE id = %s", (rule_set_id,)).fetchone()
    if r is None or r["status"] != "DRAFT":
        raise HTTPException(409, "Only a DRAFT rule set can be activated")
    if not (r["test_result"] or {}).get("passed"):
        raise HTTPException(422, "The golden cases did not pass for this candidate; it cannot be activated")
    with staff_tx(conn, staff.user_id, staff.role):
        conn.execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = %s WHERE id = %s",
                     (staff.user_id, body.reason.strip(), rule_set_id))
        conn.execute("UPDATE policy_change_proposals SET status = 'ACTIVATED', decided_by = %s WHERE drafted_rule_set_id = %s AND status = 'DRAFTED'",
                     (staff.user_id, rule_set_id))
    return {"status": "ACTIVE", "version": r["version"], "note": "New applications use this version from their intake; in-flight applications keep theirs."}
