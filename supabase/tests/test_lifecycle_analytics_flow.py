"""(Named to run after test_applications_api: these flows leave the shared synthetic customer BOB-C0027 past intake.)
Phase 13 against real Postgres with the real migrations: lifecycle events written with each change, the idempotent batch sync to
BigQuery (a fake sink), backoff, FAILED rows and the manual retry, the outbox guard, and the analytics and system endpoints."""

import json

import pytest

from app import analytics, jobs
from app.main import app
from app.routers.analytics import get_kpis
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_disbursement_flow import ALL, sanctioned
from test_documents_flow import run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)
from test_review_flow import ver
from test_sanction_flow import sw  # noqa: F401  (fixture)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"


class FakeSink:
    def __init__(self):
        self.rows, self.fail = [], None

    def existing(self, ids):
        if self.fail:
            raise self.fail
        return {r["event_id"] for r in self.rows} & set(ids)

    def append(self, rows):
        self.rows += rows


@pytest.fixture
def an(sw):  # noqa: F811
    sw["admin"].execute("UPDATE analytics_outbox SET status = 'SENT' WHERE status <> 'SENT'")  # the outbox is shared: start with nothing due
    return sw | {"sink": FakeSink()}


def disbursed(api, w):  # noqa: F811
    aid = sanctioned(api, w)
    lo = api.as_(LO)
    lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)})
    lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL})
    assert api.as_(AM).post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 201
    return aid


def events(w, aid):
    return w["admin"].execute("SELECT event_type, payload FROM analytics_outbox WHERE application_id = %s ORDER BY id", (aid,)).fetchall()


def test_every_change_writes_its_event_with_workflow_facts_and_no_personal_data(api, an):  # noqa: F811
    aid = disbursed(api, an)
    ev = events(an, aid)
    status = [(e["payload"]["from"], e["payload"]["to"]) for e in ev if e["event_type"] == "STATUS_CHANGED"]
    assert status == [("INTAKE", "DOCS_PENDING"), ("DOCS_PENDING", "DOCS_VERIFIED"), ("DOCS_VERIFIED", "AI_ASSESSED"), ("AI_ASSESSED", "FORWARDED"),
                      ("FORWARDED", "UNDER_REVIEW"), ("UNDER_REVIEW", "APPROVED"), ("APPROVED", "SANCTIONED"), ("SANCTIONED", "DISBURSED")]
    kinds = [e["event_type"] for e in ev]
    assert kinds.count("DECISION") == 1 and kinds.count("DISBURSEMENT_RELEASED") == 1
    d = next(e["payload"] for e in ev if e["event_type"] == "DISBURSEMENT_RELEASED")
    assert float(d["net_amount"]) > 0 and d["simulated"] is True and d["released_by_role"] == AM
    s = next(e["payload"] for e in ev if e["event_type"] == "STATUS_CHANGED" and e["payload"]["to"] == "AI_ASSESSED")
    assert s["actor_type"] == "SYSTEM" and s["bank_relationship"] == "ETB" and s["ai_recommendation"] == "RECOMMENDED_FOR_APPROVAL"
    person = an["admin"].execute("SELECT a.* FROM applicants a JOIN applications ap ON ap.applicant_id = a.id WHERE ap.id = %s", (aid,)).fetchone()
    blob = json.dumps([e["payload"] for e in ev], default=str)
    for value in (person["full_name"], person["pan"], person["mobile"], person["email"], person["address_line"]):
        assert value and value not in blob  # analytics never carries who the customer is


def test_sync_loads_each_event_once_marks_it_sent_and_skips_what_bigquery_already_has(api, an):  # noqa: F811
    aid = disbursed(api, an)
    conn = an["deps"].connect()
    first = analytics.sync(conn, an["sink"])
    n = len(events(an, aid))
    assert first["loaded"] >= n and first["failed"] == 0
    assert {r["event_id"] for r in an["sink"].rows if r["application_id"] == aid} == {str(x["event_id"]) for x in an["admin"].execute(
        "SELECT event_id FROM analytics_outbox WHERE application_id = %s", (aid,)).fetchall()}
    assert an["admin"].execute("SELECT count(*) AS n FROM analytics_outbox WHERE application_id = %s AND status = 'SENT' AND sent_at IS NOT NULL", (aid,)).fetchone()["n"] == n
    assert analytics.sync(conn, an["sink"])["loaded"] == 0  # nothing due twice
    # a load that reached BigQuery but was never marked SENT (e.g. the backend died) is skipped, not duplicated
    an["admin"].execute("UPDATE analytics_outbox SET status = 'PENDING' WHERE application_id = %s", (aid,))
    again = analytics.sync(conn, an["sink"])
    assert again["skipped"] == n and again["loaded"] == 0 and len([r for r in an["sink"].rows if r["application_id"] == aid]) == n
    row = an["sink"].rows[0]
    assert set(row) == {"event_id", "event_type", "application_id", "occurred_at", "payload", "loaded_at"} and json.loads(row["payload"])
    conn.close()


def test_a_failing_load_backs_off_then_parks_the_rows_as_failed_and_a_manager_requeues_them(api, an):  # noqa: F811
    aid = disbursed(api, an)
    an["sink"].fail = RuntimeError("BigQuery quota exceeded")
    conn = an["deps"].connect()
    r = analytics.sync(conn, an["sink"])
    assert r["failed"] > 0 and r["error"] == "RuntimeError"
    row = an["admin"].execute("SELECT status, attempts, last_error, next_attempt_at > now() AS later FROM analytics_outbox WHERE application_id = %s LIMIT 1", (aid,)).fetchone()
    assert (row["status"], row["attempts"], row["later"]) == ("PENDING", 1, True) and "quota" in row["last_error"]
    for _ in range(analytics.MAX_ATTEMPTS - 1):
        an["admin"].execute("UPDATE analytics_outbox SET next_attempt_at = now() WHERE status = 'PENDING'")
        analytics.sync(conn, an["sink"])
    assert an["admin"].execute("SELECT count(*) AS n FROM analytics_outbox WHERE application_id = %s AND status = 'FAILED'", (aid,)).fetchone()["n"] == len(events(an, aid))
    s = api.as_(AM).get("/system/analytics").json()
    assert s["counts"]["FAILED"] >= 1 and s["failed"][0]["error"].startswith("RuntimeError")
    assert api.as_(LO).post("/system/analytics/retry", json={}).status_code == 403
    one = s["failed"][0]["event_id"]
    assert api.as_(BM).post("/system/analytics/retry", json={"event_ids": [one]}).json() == {"requeued": 1}
    assert api.as_(BM).post("/system/analytics/retry", json={}).json()["requeued"] >= 1
    an["sink"].fail = None
    assert analytics.sync(conn, an["sink"])["loaded"] >= len(events(an, aid))
    a = an["admin"].execute("SELECT after_state FROM audit_events WHERE event_type = 'ANALYTICS_RETRY' ORDER BY id DESC LIMIT 1").fetchone()
    assert a["after_state"]["requeued"] >= 1
    conn.close()


def test_the_drain_flushes_the_outbox_when_a_sink_is_configured(api, an):  # noqa: F811
    aid = disbursed(api, an)
    an["deps"].events = an["sink"]
    out = jobs.drain(an["deps"], limit=1)
    assert any(r["kind"] == "analytics_sync" and r["loaded"] >= 1 for r in out)
    assert any(r["application_id"] == aid for r in an["sink"].rows)


def test_events_are_a_record_content_fixed_never_deleted(api, an):  # noqa: F811
    aid = disbursed(api, an)
    with pytest.raises(Exception, match="content is fixed"):
        an["admin"].execute("UPDATE analytics_outbox SET payload = '{}' WHERE application_id = %s", (aid,))
    with pytest.raises(Exception, match="kept"):
        an["admin"].execute("DELETE FROM analytics_outbox WHERE application_id = %s", (aid,))


def test_kpis_are_for_managers_and_an_outage_is_reported_plainly(api, an):  # noqa: F811
    app.dependency_overrides[get_kpis] = lambda: (lambda: {"headline": {"historical": {"decisions": 1902}}, "labels": {"data": "All data is synthetic."}})
    assert api.as_(LO).get("/analytics/overview").status_code == 403
    for role in (AM, BM):
        r = api.as_(role).get("/analytics/overview")
        assert r.status_code == 200 and r.json()["headline"]["historical"]["decisions"] == 1902

    def down():
        raise ConnectionError("bq.internal:443 refused, token abc123")

    app.dependency_overrides[get_kpis] = lambda: down
    r = api.as_(AM).get("/analytics/overview")
    assert r.status_code == 503 and "abc123" not in r.text and "ConnectionError" in r.json()["detail"]
