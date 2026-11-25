import json
import uuid
from contextlib import contextmanager

import psycopg

INSUFFICIENT_PRIVILEGE = "42501"
CHECK_VIOLATION = "23514"
SERIALIZATION_FAILURE = "40001"


@contextmanager
def expect_error(conn, sqlstate: str | None = None, match: str | None = None):
    """The block must fail inside the database. A savepoint keeps the surrounding test transaction usable."""
    try:
        with conn.transaction():
            yield
    except psycopg.Error as e:
        if sqlstate:
            assert e.sqlstate == sqlstate, f"expected SQLSTATE {sqlstate}, got {e.sqlstate}: {e}"
        if match:
            assert match.lower() in str(e).lower(), f"expected '{match}' in: {e}"
        return
    raise AssertionError("expected the database to reject this, but it was accepted")


def staff(conn, role: str, active: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    conn.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{uid}@example.invalid"))
    conn.execute("INSERT INTO staff_profiles (user_id, full_name, email, role, is_active) VALUES (%s, %s, %s, %s, %s)",
                 (uid, f"Test {role.title()}", f"{uid}@example.invalid", role, active))
    return uid


def applicant(conn, by) -> uuid.UUID:
    return conn.execute("INSERT INTO applicants (bank_relationship, full_name, date_of_birth, mobile, pan, declared_monthly_net) "
                        "VALUES ('ETB', 'Asha Verma', '1990-04-12', '9876543210', 'SYNP0001234K', 55000) RETURNING id").fetchone()["id"]


def application(conn, by) -> uuid.UUID:
    return conn.execute("INSERT INTO applications (applicant_id, requested_amount, requested_tenure_months, created_by) VALUES (%s, 500000, 36, %s) RETURNING id",
                        (applicant(conn, by), by)).fetchone()["id"]


def version(conn, app) -> int:
    return conn.execute("SELECT version FROM applications WHERE id = %s", (app,)).fetchone()["version"]


def status(conn, app) -> str:
    return conn.execute("SELECT status FROM applications WHERE id = %s", (app,)).fetchone()["status"]


def rule_set(conn, by, content=None, version_no=None) -> uuid.UUID:
    return conn.execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256, created_by) VALUES (%s, %s, %s, 'x', %s) RETURNING id",
                        (f"scheme-{uuid.uuid4().hex[:6]}", version_no or 1, json.dumps(content or {"rules": []}), by)).fetchone()["id"]


def assessed_run(conn, app, recommendation: str) -> uuid.UUID:
    return conn.execute("INSERT INTO agent_runs (application_id, graph, status, recommendation, finished_at) VALUES (%s, 'ASSESSMENT', 'DONE', %s, now()) RETURNING id",
                        (app, recommendation)).fetchone()["id"]


def force(conn, app, new_status: str, recommendation: str | None = None, assigned=None) -> None:
    """Test set-up only: put an application into a state without walking the workflow (it bypasses the guard on purpose)."""
    run = assessed_run(conn, app, recommendation) if recommendation else None
    conn.execute("SELECT set_config('app.in_workflow_fn', 'on', true)")
    conn.execute("UPDATE applications SET status = %s, version = version + 1, ai_recommendation = %s, ai_recommendation_run_id = %s, assigned_to = %s WHERE id = %s",
                 (new_status, recommendation, run, assigned, app))
    conn.execute("SELECT set_config('app.in_workflow_fn', 'off', true)")


def under_review(conn, recommendation: str = "REFER_FOR_FURTHER_REVIEW", assigned=None):
    lo = staff(conn, "LOAN_OFFICER")
    app = application(conn, lo)
    force(conn, app, "UNDER_REVIEW", recommendation, assigned)
    return app


def decide(conn, app, who, outcome: str, reason: str | None = None, at_version: int | None = None):
    return conn.execute(
        "INSERT INTO decisions (application_id, application_version, outcome, decided_by, decided_by_role, reason, ai_recommendation_snapshot, is_override) "
        "VALUES (%s, %s, %s, %s, 'ASSISTANT_MANAGER', %s, '{}', false) RETURNING *",
        (app, at_version or version(conn, app), outcome, who, reason)).fetchone()


def audit(conn, app, event: str | None = None) -> list[dict]:
    q = "SELECT * FROM audit_events WHERE application_id = %s" + (" AND event_type = %s" if event else "") + " ORDER BY id"
    return conn.execute(q, (app, event) if event else (app,)).fetchall()
