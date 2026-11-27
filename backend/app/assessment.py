"""Assessment graph (LangGraph): turns a documents-verified application into an AI recommendation for a human reviewer.

    load_application -> load_credit_record -> gather_evidence -> evaluate_policy -> retrieve_policy -> write_summary

  load_application    the application, its human-verified documents, open discrepancies, and the rule set frozen at intake
                      (an in-flight application keeps the version it started under)
  load_credit_record  customer 360 from lh_gold as the app identity: bank-held data, the synthetic bureau profile and the mock KYC checks.
                      Existing customers by customer ID; a new-to-bank applicant by PAN with a date-of-birth match, as a bureau pull would be.
  gather_evidence     income evidence and obligations, each with its source (bank credits, or values a Loan Officer verified on documents)
  evaluate_policy     the deterministic engine: rule-by-rule PASS / FAIL / SKIPPED / NEEDS_REVIEW and the recommendation
  retrieve_policy     passages from the policy knowledge base for every failed or referred rule, with citations
  write_summary       Gemini explains the result in plain words. It sees no name, PAN or ID. Its text is rejected (and a fixed
                      template used instead) if it contains any number not in the engine's output or speaks as if it decided

The recommendation comes from the engine alone. Nothing here approves, declines, changes a rule, a calculation or approved terms, or moves
the application except through app_record_assessment, which only records a recommendation that matches a finished run.
"""

import json
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app import docproc, policy_kb
from app.db import staff_tx
from app.policy import engine, ruleset
from app.policy.cases import case_from_customer_360

D = Decimal
LABEL = {"RECOMMENDED_FOR_APPROVAL": "Recommended for approval", "REFER_FOR_FURTHER_REVIEW": "Refer for further review",
         "POTENTIALLY_INELIGIBLE": "Potentially ineligible"}
HUMAN_DECIDES = "AI recommendation, human decides."
DECISION_WORDS = re.compile(r"(?i)\b(approved|approve|rejected|reject|declined|decline|sanctioned|sanction|disburse\w*)\b")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Summary(BaseModel):
    summary: str = Field(max_length=900, description="two to four plain sentences for the reviewer")
    points: list[str] = Field(max_length=6, description="the main reasons, one short sentence each")


class State(TypedDict, total=False):
    application_id: str
    today: date
    app: dict
    documents: list[dict]
    open_discrepancies: int
    rule_set: Any
    rule_set_id: str
    credit: dict | None
    credit_lookup: dict
    credit_profile: dict | None
    evidence: dict
    result: dict
    citations: dict
    summary: dict
    steps: list[dict]


def _step(state: State, name: str, **used) -> list[dict]:
    return state.get("steps", []) + [{"step": name, "at": datetime.now(timezone.utc).isoformat(), **used}]


def _plain(x):
    if isinstance(x, (D, date)):
        return str(x)
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


# ---------------------------------------------------------------- evidence
def income_evidence(app: dict, credit: dict | None, documents: list[dict]) -> dict:
    """Monthly income evidence and months covered, with where it came from. Bank-held credits first (existing customers), then values a
    Loan Officer verified on a bank statement, salary slips or an ITR. Prepared demo data can never be verified, so it never appears here."""
    salaried = (app["employment_type"] or "").startswith("SALARIED")
    if credit and app["bank_relationship"] == "ETB":
        avg, months = ((credit.get("avg_salary_credit_6m"), credit.get("salary_credit_months_6m")) if salaried or app["employment_type"] == "PENSIONER"
                       else (credit.get("avg_business_receipts_6m"), credit.get("business_receipt_months_6m")))
        if avg is not None and months:
            return {"monthly": D(str(avg)), "months": int(months), "source": "Bank of Baroda account credits, last 6 months (bank-held data)"}
    by_type: dict[str, list[dict]] = {}
    for d in documents:
        by_type.setdefault(d["doc_type"], []).append(d["fields"])
    num = lambda f, k: D(f[k]) if f.get(k) not in (None, "") else None  # noqa: E731
    # per calendar month of each statement's period, never per credit; statements of the same account add up their months
    accounts: dict = {}
    assumed = False
    for f in by_type.get("BANK_STATEMENT", []):
        total, months = num(f, "income_credit_total"), docproc.statement_months(f.get("period"))
        if not total:
            continue
        assumed |= months is None
        t, m = accounts.get(f.get("account_id"), (D(0), 0))
        accounts[f.get("account_id")] = (t + total, m + (months or docproc.ASSUMED_STATEMENT_MONTHS))
    if accounts:
        return {"monthly": sum(t / m for t, m in accounts.values()).quantize(D("0.01")), "months": max(m for _, m in accounts.values()),
                "source": "Bank statement income credits verified by the Loan Officer"
                          + (f" (period not readable: {docproc.ASSUMED_STATEMENT_MONTHS} months assumed)" if assumed else "")}
    slips = {f.get("month") or i: num(f, "net") for i, f in enumerate(by_type.get("SALARY_SLIP", []))}
    slips = {m: v for m, v in slips.items() if v is not None}
    if slips:
        return {"monthly": (sum(slips.values()) / len(slips)).quantize(D("0.01")), "months": len(slips), "source": "Net pay on salary slips verified by the Loan Officer"}
    itr = [num(f, "gross_total_income") for f in by_type.get("ITR", [])]
    itr = [v for v in itr if v is not None]
    if itr:
        return {"monthly": (max(itr) / 12).quantize(D("0.01")), "months": 12, "source": "Gross total income on the ITR verified by the Loan Officer, divided by 12"}
    return {"monthly": None, "months": None, "source": "No income evidence on file"}


def obligations(app: dict, credit: dict | None) -> dict:
    """The higher of bureau-reported EMIs and the EMIs the applicant declared: the conservative choice."""
    bureau = D(str(credit["total_monthly_obligations"])) if credit and credit.get("total_monthly_obligations") is not None else None
    declared = D(str(app["existing_monthly_emi"] or 0))
    used = max(bureau or D(0), declared)
    return {"monthly": used, "bureau": bureau, "declared": declared,
            "source": "bureau-reported EMIs" if bureau is not None and bureau >= declared else "EMIs declared by the applicant"}


# ---------------------------------------------------------------- summary checks
def _numbers(s: str) -> set[str]:
    out = set()
    for t in NUMBER.findall(s):
        t = t.replace(",", "")
        out.add(t)
        if "." in t:
            out.add(t.rstrip("0").rstrip("."))
    return out


def check_summary(s: Summary, facts_text: str) -> str | None:
    """Why the model's text cannot be used, or None. Every number must be one the engine produced; no decision language."""
    text = " ".join([s.summary, *s.points])
    extra = sorted(_numbers(text) - _numbers(facts_text))
    if extra:
        return f"it contains numbers that are not in the assessment ({', '.join(extra[:5])})"
    if DECISION_WORDS.search(text):
        return "it uses decision language; the AI recommends and a human decides"
    return None


def template_summary(result: dict) -> dict:
    return {"text": f"{LABEL[result['recommendation']]} on the evidence available. {HUMAN_DECIDES}", "points": result["reasons"][:6]}


def summary_prompt(facts: dict) -> str:
    return ("You explain a deterministic loan eligibility assessment to a bank reviewer in plain English. Use only the facts below.\n"
            "Rules: do not add, change, round or calculate any number; any number you write must appear in the facts exactly as written. "
            "Do not say or imply that the loan is approved, rejected, declined or sanctioned: the result is a recommendation and a human decides. "
            "Do not give advice beyond the facts. Explain the main reasons, most important first, and what evidence would resolve any referral.\n"
            f"Facts (JSON):\n{json.dumps(facts, ensure_ascii=False)}")


# ---------------------------------------------------------------- graph
def build_graph(conn, deps, start: str = "load_application"):
    """`start` lets a dry run (scripts/live_check_assessment.py) begin after load_application with a prepared application; it records nothing."""
    from langgraph.graph import END, START, StateGraph

    def load_application(state: State) -> dict:
        app = conn.execute(
            "SELECT ap.*, a.customer_id, a.bank_relationship, a.full_name, a.date_of_birth, a.pan, a.residency_status, a.is_staff, a.employment_type "
            "FROM applications ap JOIN applicants a ON a.id = ap.applicant_id WHERE ap.id = %s", (state["application_id"],)).fetchone()
        if app is None or app["status"] != "DOCS_VERIFIED":
            raise NotReady(f"application is {app['status'] if app else 'missing'}, not DOCS_VERIFIED")
        if app["declared_monthly_net"] is None:
            raise ValueError("the application has no declared monthly net income")
        rs_id = (app["documents_checklist"] or {}).get("rule_set_id")
        row = conn.execute("SELECT id, content FROM policy_rule_sets WHERE id = %s AND status IN ('ACTIVE', 'RETIRED')", (rs_id,)).fetchone() if rs_id else None
        if row is None:
            raise ValueError("the rule set frozen at intake was not found")
        docs = conn.execute("SELECT d.id, d.doc_type, d.requirement, coalesce(jsonb_object_agg(f.field, f.value) FILTER (WHERE f.field IS NOT NULL), '{}') AS fields "
                            "FROM documents d LEFT JOIN document_fields f ON f.document_id = d.id WHERE d.application_id = %s AND d.status = 'VERIFIED' "
                            "GROUP BY d.id, d.doc_type, d.requirement ORDER BY d.uploaded_at", (app["id"],)).fetchall()
        n_open = conn.execute("SELECT count(*) AS n FROM discrepancies WHERE application_id = %s AND status = 'OPEN'", (app["id"],)).fetchone()["n"]
        rs = ruleset.validate(row["content"])
        return {"app": app, "documents": docs, "open_discrepancies": n_open, "rule_set": rs, "rule_set_id": str(row["id"]),
                "steps": _step(state, "load_application", reference=app["reference"], status=app["status"], verified_documents=[d["doc_type"] for d in docs],
                               open_discrepancies=n_open, policy=rs.identity, rule_set_id=str(row["id"]))}

    def load_credit_record(state: State) -> dict:
        app = state["app"]
        if app["bank_relationship"] == "ETB":
            credit, matched_by = deps.lakehouse.credit_record(customer_id=app["customer_id"]), "customer ID"
        else:
            credit, matched_by = deps.lakehouse.credit_record(pan=app["pan"]) if app["pan"] else None, "PAN and date of birth"
        lookup = {"found": credit is not None, "matched_by": matched_by, "source": "lh_gold.customer_360 (synthetic bank, bureau and mock KYC data)"}
        if credit is not None and str(credit["date_of_birth"]) != str(app["date_of_birth"]):
            lookup.update(found=False, note="a record with this PAN exists but the date of birth differs; bureau data not used, identity referred")
            credit = {"pan_status": "DOB_MISMATCH"}
        elif credit is None and app["bank_relationship"] == "NTB":
            lookup["note"] = "no bureau or KYC record for this PAN: treated as new to credit, and the PAN as not verified"
            credit = {"pan_status": "NOT_FOUND"}
        # The credit profile used is this month's persisted DEMO / SIMULATED profile for the PAN: the same on every recheck and application.
        pan = app["pan"] or credit.get("pan")
        profile = None
        if pan and credit.get("pan_status") != "DOB_MISMATCH":  # someone else's PAN: no profile is read or created for it
            from app import credit_profile

            profile = credit_profile.get_or_create(conn, pan, state["today"], credit if lookup["found"] else None)
            credit = {**credit, **{k: v for k, v in profile["profile"].items()}}
            lookup["credit_profile"] = {k: profile[k] for k in ("source", "period_start", "valid_until", "generated_at", "label")}
        used = {k: _plain(credit.get(k)) for k in ("bureau_history_status", "bureau_score", "tradeline_count", "max_dpd_12m", "late_payment_months_24m",
                                                   "has_written_off", "has_settled", "total_monthly_obligations", "kyc_status", "pan_status", "name_match_score",
                                                   "aadhaar_check", "returned_debits_6m") if k in credit}
        return {"credit": credit, "credit_lookup": lookup, "credit_profile": profile, "steps": _step(state, "load_credit_record", **lookup, data_used=used)}

    def gather_evidence(state: State) -> dict:
        app, credit = state["app"], state["credit"] or {}
        inc = income_evidence(app, credit if credit.get("customer_id") else None, state["documents"])
        obl = obligations(app, credit if credit.get("customer_id") or state.get("credit_profile") else None)
        ev = {"income": inc, "obligations": obl}
        return {"evidence": ev, "steps": _step(state, "gather_evidence", **_plain(ev))}

    def evaluate_policy(state: State) -> dict:
        app, credit, ev = state["app"], dict(state["credit"] or {}), state["evidence"]
        found = bool(credit.get("customer_id"))
        row = {**credit, "bank_relationship": app["bank_relationship"], "date_of_birth": app["date_of_birth"], "employment_type": app["employment_type"],
               "declared_net_income": app["declared_monthly_net"], "is_staff": app["is_staff"], "residency_status": app["residency_status"],
               "total_monthly_obligations": ev["obligations"]["monthly"],
               "bureau_history_status": credit.get("bureau_history_status") if found or state.get("credit_profile") else "NO_HISTORY"}
        inc = ev["income"]
        case = case_from_customer_360(row, requested_amount=app["requested_amount"], requested_tenure_months=app["requested_tenure_months"], as_of=state["today"],
                                      income_evidence=(inc["monthly"], inc["months"]) if inc["monthly"] is not None else None,
                                      documents_verified=True, open_discrepancies=state["open_discrepancies"])
        if inc["monthly"] is None:  # the evidence gathered above is the only evidence: nothing else is assumed
            case = replace(case, income_evidence_monthly=None, income_evidence_months=None)
        result = engine.evaluate(state["rule_set"], case).to_dict()
        counts = {s: sum(1 for r in result["rules"] if r["status"] == s) for s in ("PASS", "FAIL", "NEEDS_REVIEW", "SKIPPED")}
        return {"result": result, "steps": _step(state, "evaluate_policy", segment=result["segment"], recommendation=result["recommendation"], rule_counts=counts,
                                                  case=_plain(asdict(case)))}

    def retrieve_policy(state: State) -> dict:
        citations = policy_kb.retrieve_for(deps.kb.index(), state["result"]["rules"])
        found = {rid: len(c) for rid, c in citations["by_rule"].items()}
        return {"citations": citations, "steps": _step(state, "retrieve_policy", passages_by_rule=found,
                                                       terms={t: len(c) for t, c in citations["terms"].items()}, method="BM25 over lh_curated.policy_chunks")}

    def write_summary(state: State) -> dict:
        result = state["result"]
        facts = {"recommendation": result["recommendation"], "segment": result["segment"], "reasons": result["reasons"],
                 "rules": [{"id": r["id"], "title": r["title"], "status": r["status"], "message": r["message"]} for r in result["rules"]],
                 "figures": {k: result["figures"][k] for k in ("recommended_amount", "recommended_tenure_months", "recommended_emi", "indicative_rate_bp",
                                                                 "foir_after_percent", "max_eligible_amount", "requested_amount", "requested_tenure_months")}}
        facts_text = docproc.redact([json.dumps(facts, ensure_ascii=False)])[0][0]  # defence in depth: no PAN can leave even if one slipped into a message
        out, note, model = template_summary(result), None, None
        if deps.gemini is None:
            note = "AI summary not configured (GEMINI_API_KEY); fixed template used"
        elif not conn.execute("SELECT take_budget('gemini', %s) AS ok", (deps.gemini_cap,)).fetchone()["ok"]:
            note = f"daily Gemini budget of {deps.gemini_cap} calls reached; fixed template used"
        else:
            try:
                s = deps.gemini.generate(summary_prompt(json.loads(facts_text)), Summary)
                problem = check_summary(s, facts_text)
                if problem:
                    note = f"AI summary discarded: {problem}; fixed template used"
                else:
                    out, model = {"text": s.summary, "points": s.points}, deps.model
            except Exception as e:  # noqa: BLE001  the explanation is optional; the recommendation never depends on it
                note = f"AI summary unavailable ({type(e).__name__}); fixed template used"
        summary = {**out, "source": "GEMINI" if model else "TEMPLATE", "model": model, "note": note, "label": HUMAN_DECIDES}
        return {"summary": summary, "steps": _step(state, "write_summary", source=summary["source"], model=model, note=note)}

    g = StateGraph(State)
    nodes = [load_application, load_credit_record, gather_evidence, evaluate_policy, retrieve_policy, write_summary]
    nodes = nodes[[n.__name__ for n in nodes].index(start):]
    for n in nodes:
        g.add_node(n.__name__, n)
    g.add_edge(START, nodes[0].__name__)
    for a, b in zip(nodes, nodes[1:]):
        g.add_edge(a.__name__, b.__name__)
    g.add_edge(nodes[-1].__name__, END)
    return g.compile()


class NotReady(Exception):
    """The application is not (or no longer) waiting for an assessment: nothing to do."""


# ---------------------------------------------------------------- job: run the graph, record the run, record the recommendation
def assess_job(conn, job: dict, deps) -> None:
    app_id = job["payload"]["application_id"]
    with staff_tx(conn, actor_type="AGENT"):
        run = conn.execute("INSERT INTO agent_runs (application_id, graph, steps) VALUES (%s, 'ASSESSMENT', '[]') RETURNING id", (app_id,)).fetchone()["id"]
    state: State = {"application_id": str(app_id), "today": deps.today(), "steps": []}
    try:
        state = build_graph(conn, deps).invoke(state)
    except NotReady as e:
        _finish(conn, run, "FAILED", state.get("steps", []), {"skipped": str(e)}, None, None, None, str(e))
        return
    except Exception as e:
        _finish(conn, run, "FAILED", state.get("steps", []), {"will_retry": job["attempts"] < job["max_attempts"]}, None, None, None, f"{type(e).__name__}: {e}"[:500])
        raise  # the job runner retries with backoff, then stops; the Loan Officer can queue it again
    result, app = state["result"], state["app"]
    from app.credit_profile import risk_category

    prof = state.get("credit_profile")
    risk = risk_category(prof["score"] if prof else None, prof["history_status"] if prof else "NO_HISTORY", result["figures"].get("foir_after_percent"),
                         result["recommendation"])
    output = _plain({"recommendation": result["recommendation"], "label": LABEL[result["recommendation"]], "human_decides": HUMAN_DECIDES,
                     "policy": result["policy"], "rule_set_id": state["rule_set_id"], "segment": result["segment"], "assessment": result,
                     "evidence": state["evidence"], "credit_lookup": state["credit_lookup"], "credit_profile": prof, "risk": risk,
                     "citations": state["citations"], "summary": state["summary"], "disclaimer": result["disclaimer"]})
    _finish(conn, run, "DONE", state["steps"], output, result["recommendation"], state["rule_set_id"], state["summary"]["model"], None)
    f = result["figures"]
    with staff_tx(conn, actor_type="SYSTEM"):
        if D(f["recommended_amount"]) > 0 and f["recommended_tenure_months"]:
            conn.execute("INSERT INTO application_terms (application_id, kind, seq, amount, rate_bp, tenure_months, policy_rule_set_id) "
                         "SELECT %s, 'ELIGIBLE', coalesce(max(seq), 0) + 1, %s, %s, %s, %s FROM application_terms WHERE application_id = %s AND kind = 'ELIGIBLE'",
                         (app_id, f["recommended_amount"], f["indicative_rate_bp"], f["recommended_tenure_months"], state["rule_set_id"], app_id))
        conn.execute("SELECT app_record_assessment(%s, %s, %s, %s, %s)", (app_id, run, result["recommendation"], state["rule_set_id"], app["version"]))


def _finish(conn, run, status, steps, output, recommendation, rule_set_id, model, error) -> None:
    with staff_tx(conn, actor_type="AGENT"):
        conn.execute("UPDATE agent_runs SET status = %s, steps = %s, output = %s, recommendation = %s, policy_rule_set_id = %s, model = %s, error = %s, "
                     "finished_at = now() WHERE id = %s",
                     (status, json.dumps(_plain(steps)), json.dumps(output), recommendation, rule_set_id, model, error, run))
