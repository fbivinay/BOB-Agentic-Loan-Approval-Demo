"""Phase 12 against real Postgres with the real migrations: the monitor run through the internal endpoint, change reports, proposals,
drafting a candidate rule set with the golden cases, activation by a human, and the health view. Fetching, BigQuery and Gemini are fakes."""

import hashlib

import httpx
import pytest

from app import jobs, monitor
from app.config import settings
from app.main import app
from test_documents_flow import Storage
from test_enquiries_api import api, env  # noqa: F401  (fixtures)

LO, AM, BM = "LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"
TOKEN = "monitor-test-token"


def page(title, body, container="main"):
    inner = f"<h2>{title}</h2>{body}<h2>Section two</h2><p>General terms apply to all borrowers.</p><h2>Section three</h2><p>Contact your branch.</p>"
    wrap = f"<main>{inner}</main>" if container == "main" else f'<table class="tablebg"><tr><td>{inner}</td></tr></table>'
    return f"<html><body><nav>menu</nav>{wrap}<footer>f</footer></body></html>".encode()


BOB_V1 = page("Baroda Personal Loan : Features",
              "<p>Loan amount: Minimum Rs. 50,000. Maximum Rs.20.00 lakhs, linked with occupation and account relationship.</p>"
              "<h3>Repayment Period</h3><table><tr><td>Govt employees with salary account</td><td>84 Months</td></tr><tr><td>Others</td><td>48 to 60 Months</td></tr></table>"
              "<h3>Interest rates and charges</h3><p>Processing charges: 2% of loan amount, minimum Rs. 1000, maximum Rs. 10,000 plus GST.</p>")
BOB_V2 = BOB_V1.replace(b"Maximum Rs.20.00 lakhs", b"Maximum Rs.25.00 lakhs")
FEED = b"""<?xml version="1.0"?><rss><channel>
<item><title>Reserve Bank of India (Commercial Banks - Responsible Business Conduct) Amendment Directions, 2026</title>
<link>https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13760&amp;Mode=0</link><pubDate>Wed, 25 Nov 2026 17:00:00</pubDate></item>
<item><title>Reserve Bank of India (Non-Banking Financial Companies - Responsible Business Conduct) Amendment Directions, 2026</title>
<link>https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13761&amp;Mode=0</link><pubDate>Wed, 25 Nov 2026 17:00:00</pubDate></item>
<item><title>Implementation of Section 51A of UAPA, 1967</title>
<link>https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13762&amp;Mode=0</link><pubDate>Thu, 26 Nov 2026 11:15:00</pubDate></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self):
        self.pages, self.calls, self.redirects = {}, [], {}
        for s in monitor.yaml.safe_load(monitor.policy_kb.SOURCES_FILE.read_text(encoding="utf-8"))["sources"]:
            self.pages[s["url"]] = page(s["title"], "<p>Unrelated text that has no figures.</p>", "main" if s["container"]["tag"] == "main" else "table")
        self.pages["https://bankofbaroda.bank.in/loans/personal-loan/baroda-personal-loan"] = BOB_V1
        self.pages["https://www.rbi.org.in/notifications_rss.xml"] = FEED
        self.pages["https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13760&Mode=0"] = page("Amendment", "<p>Paragraph 348 is amended.</p>", "table")

    def get(self, url, headers=None, follow_redirects=True):
        self.calls.append(url)
        if url in self.redirects:
            return httpx.Response(302, headers={"location": self.redirects[url]}, request=httpx.Request("GET", url))
        body = self.pages.get(url)
        if body is None:
            return httpx.Response(404, content=b"not found", request=httpx.Request("GET", url))
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if (headers or {}).get("If-None-Match") == etag:
            return httpx.Response(304, request=httpx.Request("GET", url))
        ctype = "text/xml" if url.endswith(".xml") else "text/html; charset=utf-8"
        return httpx.Response(200, content=body, headers={"content-type": ctype, "etag": etag}, request=httpx.Request("GET", url))


class FakePolicyStore:
    def __init__(self):
        self.rows = {"policy_sources": [], "policy_chunks": [], "policy_facts": []}

    def latest(self, sid):
        rows = [r for r in self.rows["policy_sources"] if r["source_id"] == sid]
        return max(rows, key=lambda r: r["version"]) if rows else None

    def chunks(self, sid, v):
        return [r for r in self.rows["policy_chunks"] if r["source_id"] == sid and r["version"] == v]

    def facts(self, sid, v, prompt_version=monitor.PROMPT_VERSION):
        rows = [r for r in self.rows["policy_facts"] if r["source_id"] == sid and r["version"] == v and r["prompt_version"] == prompt_version]
        if not rows:
            return None
        return [{"key": r["fact_key"], "value": r["value"], "condition": r["condition"], "quote": r["quote"], "numbers": monitor.json.loads(r["numbers"])}
                for r in rows if r["fact_key"] != "_extracted"]

    def known_sources(self, prefix):
        return {r["source_id"] for r in self.rows["policy_sources"] if r["source_id"].startswith(prefix)}

    def append(self, table, rows):
        self.rows[table] += rows


class FactsGemini:
    """Answers like the model would: facts with quotes, plus one value it made up (which the quote check must drop)."""

    def __init__(self):
        self.calls = []

    def generate(self, text, schema):
        self.calls.append(text)
        if "Baroda Personal Loan" not in text:
            return monitor.PolicyFacts(facts=[])
        mx = "Rs.25.00 lakhs" if "25.00" in text else "Rs.20.00 lakhs"
        F = monitor.PolicyFact
        return monitor.PolicyFacts(facts=[
            F(key="loan_amount_min", value="Rs. 50,000", quote="Minimum Rs. 50,000"),
            F(key="loan_amount_max", value=mx, condition="linked with occupation and account relationship", quote=f"Maximum {mx}"),
            F(key="tenure_max_months", value="84 Months", condition="Govt employees with salary account", quote="Govt employees with salary account | 84 Months"),
            F(key="processing_fee_percent", value="2%", quote="Processing charges: 2% of loan amount"),
            F(key="processing_fee_max", value="Rs. 15,000", quote="maximum Rs. 15,000 plus GST")])  # not in the page: dropped


@pytest.fixture
def mw(api, env, monkeypatch):  # noqa: F811
    env["admin"].execute("DELETE FROM usage_counters")
    http, store, gem = FakeHttp(), FakePolicyStore(), FactsGemini()
    deps = jobs.Deps(connect=None, storage=None, store=None, gemini=gem, model="gemini-test", gemini_cap=100, kick=False, http=http, policy_store=store,
                     policy_storage=Storage())
    app.dependency_overrides[jobs.get_job_deps] = lambda: deps
    monkeypatch.setattr(settings, "internal_api_token", TOKEN)
    original = env["admin"].execute("SELECT id, content FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()
    yield {"http": http, "store": store, "gemini": gem, "admin": env["admin"], "env": env}
    # put the original figures back as the active rule set, so other test modules see the same rules
    now = env["admin"].execute("SELECT id, content FROM policy_rule_sets WHERE status = 'ACTIVE' AND scheme = 'baroda_personal_loan'").fetchone()
    if now["id"] != original["id"]:
        v = env["admin"].execute("SELECT max(version) + 1 AS v FROM policy_rule_sets WHERE scheme = 'baroda_personal_loan'").fetchone()["v"]
        c = original["content"] | {"version": v}
        rid = env["admin"].execute("INSERT INTO policy_rule_sets (scheme, version, content, content_sha256) VALUES ('baroda_personal_loan', %s, %s, 'restore') RETURNING id",
                                   (v, monitor.json.dumps(c))).fetchone()["id"]
        env["admin"].execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = 'test teardown restore', test_result = '{\"passed\": true}' "
                             "WHERE id = %s", (env[BM].user_id, rid))


def run(api, **params):  # noqa: F811
    r = api.post("/internal/monitor/run", headers={"X-Internal-Token": TOKEN, "X-Actions-Run-Url": "https://github.com/o/r/actions/runs/1"}, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_internal_run_needs_the_token(api, mw):  # noqa: F811
    assert api.post("/internal/monitor/run").status_code == 401
    assert api.post("/internal/monitor/run", headers={"X-Internal-Token": "wrong"}).status_code == 401


def test_first_run_baselines_every_source_reports_impacts_and_opens_proposals_but_changes_no_rule(api, mw):  # noqa: F811
    before = mw["admin"].execute("SELECT id, content_sha256 FROM policy_rule_sets WHERE status = 'ACTIVE'").fetchone()
    res = run(api)
    assert res["status"] == "SUCCEEDED" and res["stale"]["stale"] is False and "Nothing is activated automatically" in res["markdown"]
    by = {s["source"]: s for s in res["sources"]}
    assert by["bob_personal_loan"]["status"] == "baseline" and by["rbi_notifications_13760"]["status"] == "baseline"
    assert "rbi_notifications_13761" not in by and "rbi_notifications_13762" not in by  # another kind of lender, and not lending-related
    bob = api.get(f"/policy/reports/{by['bob_personal_loan']['report_id']}").json()
    assert bob["classification"] == "RULE_IMPACT" and bob["layer_b"]["baseline"] is True
    kinds = {(i["kind"], i.get("parameter")) for i in bob["layer_c"]}
    assert ("RULE_IMPACT", "tenure.max_months") in kinds and ("RULE_IMPACT", "fees.processing_bp") in kinds
    assert ("VERIFICATION_CANDIDATE", "loan.max_amount") in kinds and ("VERIFICATION_CANDIDATE", "loan.min_amount") in kinds
    assert not any(f["key"] == "processing_fee_max" for f in bob["layer_b"]["current"])  # the made-up value was dropped
    notif = api.get(f"/policy/reports/{by['rbi_notifications_13760']['report_id']}").json()
    assert notif["classification"] == "COMPLIANCE_ITEM" and notif["publication_date"] == "2026-11-25"
    props = api.get("/policy/proposals", params={"status": "OPEN"}).json()["proposals"]
    assert {(p["kind"], p["parameter"]) for p in props} >= {("RULE_CHANGE", "tenure.max_months"), ("VERIFY_VALUE", "loan.max_amount")}
    tenure = next(p for p in props if p["parameter"] == "tenure.max_months")
    assert tenure["official_value"]["number"] == "84" and tenure["evidence"]["quote"].startswith("Govt employees") and tenure["current_value"] == 60
    after = mw["admin"].execute("SELECT id, content_sha256 FROM policy_rule_sets WHERE status = 'ACTIVE'").fetchone()
    assert after == before  # the monitor wrote reports and proposals, never a rule
    sent = " ".join(mw["gemini"].calls)  # only sources configured for facts, and only their matching passages, are sent
    assert 1 <= len(mw["gemini"].calls) <= 4 and "Know Your Customer" not in sent and "Credit Risk" not in sent


def test_an_unchanged_source_is_not_downloaded_again_or_sent_to_gemini_and_a_changed_one_is_reported(api, mw):  # noqa: F811
    run(api)
    calls = len(mw["gemini"].calls)
    res = run(api)
    assert all(s["status"] == "unchanged" for s in res["sources"]) and res["reports"] == 0 and len(mw["gemini"].calls) == calls
    mw["http"].pages["https://bankofbaroda.bank.in/loans/personal-loan/baroda-personal-loan"] = BOB_V2
    res = run(api)
    s = next(x for x in res["sources"] if x["source"] == "bob_personal_loan")
    assert s["status"] == "changed" and s["version"] == 2
    r = api.get(f"/policy/reports/{s['report_id']}").json()
    assert r["from_version"] == 1 and r["layer_a"]["changed"] and any("25.00" in x["text"] for x in r["layer_a"]["samples"])
    assert r["layer_b"]["changed"][0]["key"] == "loan_amount_max" and r["layer_b"]["changed"][0]["new"] == "Rs.25.00 lakhs"
    assert r["classification"] == "RULE_IMPACT" and [i["parameter"] for i in r["layer_c"] if i["kind"] == "RULE_IMPACT"] == ["loan.max_amount"]
    assert len(api.get("/policy/proposals", params={"status": "OPEN"}).json()["proposals"]) >= 5  # the same evidence never opens a proposal twice


def test_a_feed_with_a_doctype_is_refused(api, mw):  # noqa: F811
    mw["http"].pages["https://www.rbi.org.in/notifications_rss.xml"] = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><rss><channel></channel></rss>'
    res = run(api)
    feed = next(s for s in res["sources"] if s["source"] == "rbi_notifications")
    assert feed["status"] == "error" and "refused" in feed["note"] and res["status"] == "PARTIAL"


def test_a_person_drafts_from_proposals_the_golden_cases_run_and_only_then_activation(api, mw):  # noqa: F811
    run(api)
    props = {p["parameter"]: p for p in api.get("/policy/proposals", params={"status": "OPEN"}).json()["proposals"]}
    verify = props["loan.max_amount"]
    body = {"changes": {"loan.max_amount": {"value": 2000000, "confidence": "VERIFIED", "proposal_id": verify["id"]}},
            "note": "Bank of Baroda product page states Rs 20 lakhs maximum"}
    assert api.as_(AM).post("/policy/rule-sets/draft", json=body).status_code == 403  # a demo permission held by the Branch Manager
    bm = api.as_(BM)
    assert bm.post("/policy/rule-sets/draft", json=body | {"changes": {"loan.max_amount": {"value": 2000000, "confidence": "VERIFIED"}}}).status_code == 422
    assert "official figure" in bm.post("/policy/rule-sets/draft", json=body | {"changes": {"loan.max_amount": {"value": 2500000, "confidence": "VERIFIED",
                                                                                                              "proposal_id": verify["id"]}}}).json()["detail"]
    r = bm.post("/policy/rule-sets/draft", json=body)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "DRAFT" and d["tests"]["passed"] and d["tests"]["suite"] == "golden-v1" and d["differences"][0]["to"]["source"]["url"].startswith("https://")
    assert api.get("/policy/proposals", params={"status": "DRAFTED"}).json()["proposals"][0]["drafted_rule_set_id"] == d["id"]
    bad = bm.post("/policy/rule-sets/draft", json={"changes": {"pricing.min_rate_bp": {"value": 2000, "confidence": "DEMO_ASSUMPTION"}},
                                                   "note": "Testing a floor above the ceiling"}).json()
    assert bad["tests"]["passed"] is False
    assert bm.post(f"/policy/rule-sets/{bad['id']}/activate", json={"reason": "should never be allowed"}).status_code == 422
    with pytest.raises(Exception, match="golden-case tests must pass"):  # the database refuses it too
        mw["admin"].execute("UPDATE policy_rule_sets SET status = 'ACTIVE', approved_by = %s, activation_reason = 'bypass attempt' WHERE id = %s",
                            (mw["env"][BM].user_id, bad["id"]))
    assert bm.post(f"/policy/rule-sets/{d['id']}/activate", json={"reason": "short"}).status_code == 422
    r = bm.post(f"/policy/rule-sets/{d['id']}/activate", json={"reason": "Maximum amount confirmed on the official product page"})
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    sets = {x["version"]: x for x in api.get("/policy/rule-sets").json()["rule_sets"]}
    assert sets[d["version"]]["status"] == "ACTIVE" and sets[d["version"]]["approved_by"] == "Test BRANCH_MANAGER"
    assert api.get("/policy/proposals", params={"status": "ACTIVATED"}).json()["proposals"][0]["parameter"] == "loan.max_amount"
    active = mw["admin"].execute("SELECT content FROM policy_rule_sets WHERE status = 'ACTIVE'").fetchone()["content"]
    assert active["parameters"]["loan.max_amount"]["confidence"] == "VERIFIED" and active["parameters"]["tenure.max_months"]["value"] == 60  # nothing else changed


def test_reports_are_reviewed_by_the_branch_manager_and_otherwise_immutable(api, mw):  # noqa: F811
    res = run(api)
    rid = next(s["report_id"] for s in res["sources"] if s["source"] == "rbi_notifications_13760")
    assert api.as_(AM).post(f"/policy/reports/{rid}/review", json={"note": "Read and filed in the compliance checklist"}).status_code == 403
    assert api.as_(BM).post(f"/policy/reports/{rid}/review", json={"note": "ok"}).status_code == 422
    assert api.as_(BM).post(f"/policy/reports/{rid}/review", json={"note": "Read; KFS paragraph 348 amendment noted for the next KFS review"}).status_code == 200
    assert api.as_(BM).post(f"/policy/reports/{rid}/review", json={"note": "Read; KFS paragraph 348 amendment noted again"}).status_code == 409
    with pytest.raises(Exception, match="only its review"):
        mw["admin"].execute("UPDATE policy_change_reports SET classification = 'DOC_ONLY' WHERE id = %s", (rid,))
    with pytest.raises(Exception, match="kept"):
        mw["admin"].execute("DELETE FROM policy_change_reports WHERE id = %s", (rid,))
    pid = api.get("/policy/proposals").json()["proposals"][0]["id"]
    with pytest.raises(Exception, match="evidence is fixed"):
        mw["admin"].execute("UPDATE policy_change_proposals SET official_value = '{}' WHERE id = %s", (pid,))
    with pytest.raises(Exception, match="rule set has been activated"):
        mw["admin"].execute("UPDATE policy_change_proposals SET status = 'ACTIVATED', decided_by = %s WHERE id = %s", (mw["env"][BM].user_id, pid))
    assert api.as_(BM).post(f"/policy/proposals/{pid}/reject", json={"note": "Category-specific; the demo keeps one value"}).status_code == 200


def test_the_health_view_shows_monitoring_queues_budgets_and_alerts(api, mw):  # noqa: F811
    assert api.as_(LO).get("/system/health").status_code == 403
    mw["admin"].execute("UPDATE monitor_runs SET status = 'FAILED' WHERE status IN ('SUCCEEDED', 'PARTIAL')")  # as if no run had succeeded
    h = api.as_(AM).get("/system/health").json()
    assert h["monitor"]["stale"] is True and any("stale" in a for a in h["alerts"]) and "budgets_today" in h and "jobs" in h
    run(api)
    h = api.as_(BM).get("/system/health").json()
    assert h["monitor"]["stale"] is False and h["monitor"]["last_run"]["actions_run_url"].startswith("https://github.com/") and h["policy"]["open_proposals"] >= 1


def test_feed_links_and_redirects_cannot_point_the_backend_elsewhere_and_errors_stay_private(api, mw):  # noqa: F811
    evil = FEED.replace(b"https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13760&amp;Mode=0", b"https://169.254.169.254/scripts/NotificationUser.aspx?Id=13760")
    mw["http"].pages["https://www.rbi.org.in/notifications_rss.xml"] = evil
    mw["http"].redirects["https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13141"] = "http://10.0.0.5/admin"  # a redirect to an internal address
    res = run(api)
    assert not any("169.254" in c or "10.0.0.5" in c for c in mw["http"].calls)  # neither was ever requested
    assert "rbi_notifications_13760" not in {s["source"] for s in res["sources"]}  # a feed item on another host is ignored
    kyc = next(s for s in res["sources"] if s["source"] == "rbi_cb_kyc")
    assert kyc["status"] == "error" and kyc["note"] == "refused: not an official source URL" and "10.0.0.5" not in res["markdown"]
    private = mw["admin"].execute("SELECT errors FROM monitor_runs WHERE id = %s", (res["run_id"],)).fetchone()["errors"]
    assert any("10.0.0.5" in (e["note"] or "") for e in private)  # the detail is kept, privately


def test_an_unexpected_error_names_only_its_kind_in_public(api, mw):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("password=hunter2 at db.internal:5432")

    mw["store"].latest = boom
    res = run(api)
    assert all("hunter2" not in (s["note"] or "") and "db.internal" not in (s["note"] or "") for s in res["sources"])
    assert "hunter2" not in res["markdown"] and "internal error (RuntimeError)" in res["markdown"]
