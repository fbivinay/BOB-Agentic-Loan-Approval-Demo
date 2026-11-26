"""Phase 14: the demo-data reset clears the workflow and keeps staff, rules and policy history, and the append-only guards are back on
afterwards. (Named to run after the other flow tests: it clears the shared test database's workflow tables.)"""

import pytest

import reset_demo
from test_disbursement_flow import ALL, sanctioned
from test_applications_api import PNG, new_app, upload  # noqa: F401
from test_assessment_flow import aw, verified_etb  # noqa: F401  (fixtures)
from test_documents_flow import run_all, world  # noqa: F401  (fixtures)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)
from test_review_flow import ver
from test_sanction_flow import sw  # noqa: F401  (fixture)


def test_reset_clears_the_workflow_keeps_rules_and_staff_and_restores_the_guards(api, sw):  # noqa: F811
    aid = sanctioned(api, sw)
    lo = api.as_("LOAN_OFFICER")
    lo.post(f"/applications/{aid}/disbursement/acceptance", json={"expected_version": ver(api, aid)})
    lo.post(f"/applications/{aid}/disbursement/checklist", json={"expected_version": ver(api, aid), "items": ALL})
    assert api.as_("ASSISTANT_MANAGER").post(f"/applications/{aid}/disbursement/release", json={"expected_version": ver(api, aid), "confirm": True}).status_code == 201
    adm = sw["admin"]
    kept = {t: adm.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("staff_profiles", "role_permissions", "policy_rule_sets", "policy_activation_log")}
    policy_audit = adm.execute("SELECT count(*) AS n FROM audit_events WHERE event_type = 'POLICY_ACTIVATED'").fetchone()["n"]
    before = reset_demo.reset_database(adm)
    assert before["applications"] >= 1 and before["decisions"] >= 1 and before["disbursements"] >= 1
    after = reset_demo.counts(adm)
    assert all(v == 0 for v in after.values()), after
    assert kept == {t: adm.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in kept}
    assert adm.execute("SELECT count(*) AS n FROM audit_events WHERE event_type = 'POLICY_ACTIVATED'").fetchone()["n"] == policy_audit
    reset = adm.execute("SELECT reason, before_state FROM audit_events WHERE event_type = 'DEMO_RESET'").fetchone()
    assert reset["reason"].startswith("Demo data reset") and reset["before_state"]["applications"] >= 1
    # the guards are back on: the audit trail is append-only again
    with pytest.raises(Exception):
        adm.execute("DELETE FROM audit_events WHERE event_type = 'DEMO_RESET'")
    # and numbering starts again
    aid2 = new_app(api, customer_id="BOB-C0027")
    assert adm.execute("SELECT reference FROM applications WHERE id = %s", (aid2,)).fetchone()["reference"].endswith("-000001")


def test_clear_bucket_walks_every_folder_and_removes_every_file():
    import httpx

    tree = {"": [{"name": "app1", "id": None}, {"name": "app2", "id": None}, {"name": "top.pdf", "id": "x"}],
            "app1": [{"name": "a.pdf", "id": "1"}, {"name": "b.png", "id": "2"}], "app2": [{"name": "c.pdf", "id": "3"}]}

    class Http:
        def __init__(self):
            self.deleted = []

        def post(self, url, json):
            return httpx.Response(200, json=tree[json["prefix"]], request=httpx.Request("POST", url))

        def request(self, method, url, json):
            self.deleted += json["prefixes"]
            return httpx.Response(200, json=[], request=httpx.Request(method, url))

    class S:
        base, bucket, http = "https://x/storage/v1", "loan-documents", Http()

    assert reset_demo.clear_bucket(S) == 4
    assert sorted(S.http.deleted) == ["app1/a.pdf", "app1/b.png", "app2/c.pdf", "top.pdf"]
