"""Sanction pack: sanction letter, Key Facts Statement and EMI schedule, as PDFs built from the human-approved terms.

Built only after a human APPROVED decision (the database refuses the documents otherwise), from the approved terms row and the rule set the
application was assessed under. Every figure comes from app/policy/finance.py, so the KFS, the schedule and the letter agree to the paisa.

The KFS follows the structure of the RBI (Commercial Banks - Responsible Business Conduct) Directions, 2025, section B (paragraph 348):
a unique proposal number, a validity period of at least three working days, an APR computation sheet (IRR on the net disbursed amount,
reducing balance), the amortisation schedule, all fees, and penal charges disclosed upfront as charges, never as interest, never capitalised.

Every page says it is a demonstration: synthetic data, demo parameters (DEMO_ASSUMPTION), not issued by Bank of Baroda and not a loan offer.
No PAN, mobile number or other identifier beyond name, postal address and email is printed, because the pack is emailed through an external
service; a check refuses to render if a synthetic PAN appears anywhere in the data.
"""

import hashlib
import io
import json
from datetime import date, timedelta
from decimal import Decimal

from app.docproc import PAN_RE
from app.policy import finance, ruleset

D = Decimal
BRAND = "#C2410C"
WORDMARK = "Baroda Lending Desk (Demo)"
DEMO_FOOTER = ("Demonstration document generated from synthetic data and demo parameters (DEMO_ASSUMPTION). "
               "Not issued by Bank of Baroda and not a loan offer.")
KINDS = ("SANCTION_LETTER", "KFS", "EMI_SCHEDULE")
FILE_NAMES = {"SANCTION_LETTER": "Sanction-Letter", "KFS": "Key-Facts-Statement", "EMI_SCHEDULE": "EMI-Schedule"}
KFS_VALIDITY_WORKING_DAYS = 3  # RBI minimum for loans with a tenor of seven days or more; weekends skipped, bank holidays not modelled


def inr(x) -> str:
    """Indian digit grouping: 1,23,45,678.90"""
    q = D(str(x)).quantize(D("0.01"))
    sign, (whole, frac) = ("-" if q < 0 else ""), f"{abs(q):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return f"{sign}Rs. {','.join(([head] if head else []) + groups + [tail])}.{frac}"


def working_days_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


# ---------------------------------------------------------------- data
def pack_data(conn, app_id, today: date) -> dict:
    a = conn.execute("SELECT ap.*, a.full_name, a.address_line, a.city, a.state, a.pincode, a.email, a.employment_type FROM applications ap "
                     "JOIN applicants a ON a.id = ap.applicant_id WHERE ap.id = %s", (app_id,)).fetchone()
    terms = conn.execute("SELECT * FROM application_terms WHERE application_id = %s AND kind = 'APPROVED' ORDER BY created_at DESC LIMIT 1", (app_id,)).fetchone()
    decision = conn.execute("SELECT d.*, s.full_name AS approver FROM decisions d JOIN staff_profiles s ON s.user_id = d.decided_by "
                            "WHERE d.application_id = %s AND d.outcome = 'APPROVED' ORDER BY decided_at DESC LIMIT 1", (app_id,)).fetchone()
    if a is None or terms is None or decision is None:
        raise ValueError("a sanction pack needs an application with a human APPROVED decision and approved terms")
    rs_row = conn.execute("SELECT content FROM policy_rule_sets WHERE id = %s", (a["policy_rule_set_id"],)).fetchone()
    rs = ruleset.validate(rs_row["content"])
    run = conn.execute("SELECT output FROM agent_runs WHERE id = %s", (a["ai_recommendation_run_id"],)).fetchone()
    segment = run["output"]["segment"]
    p = lambda name: rs.param(name, segment)  # noqa: E731
    first_due = finance.add_months(today, 1)  # illustrative: disbursement assumed on the sanction date; fixed at disbursement
    fig = finance.kfs_figures(terms["amount"], terms["rate_bp"], terms["tenure_months"], first_due, fee_percent_bp=p("fees.processing_bp").value,
                              fee_min=p("fees.processing_min").value, fee_max=p("fees.processing_max").value, gst_percent=p("fees.gst_percent").value)
    sched = finance.schedule(terms["amount"], terms["rate_bp"], terms["tenure_months"], first_due)
    d = {
        "reference": a["reference"], "sanction_no": f"SL-{a['reference'][4:]}", "proposal_no": f"KFS-{a['reference'][4:]}", "date": today,
        "kfs_valid_until": working_days_after(today, KFS_VALIDITY_WORKING_DAYS),
        "borrower": {"name": a["full_name"], "address": [a["address_line"], f"{a['city']}, {a['state']} {a['pincode']}"], "email": a["email"]},
        "approver": {"name": decision["approver"], "role": decision["decided_by_role"].replace("_", " ").title(), "decided_at": decision["decided_at"].date()},
        "terms": {"amount": D(terms["amount"]), "rate_bp": terms["rate_bp"], "tenure_months": terms["tenure_months"]},
        "figures": fig, "schedule": sched, "policy": rs.identity,
        "charges": {"processing_bp": p("fees.processing_bp").value, "processing_min": p("fees.processing_min").value, "processing_max": p("fees.processing_max").value,
                    "gst_percent": p("fees.gst_percent").value, "penal_percent_pa": p("fees.penal_interest_percent_pa").value,
                    "prepayment_percent": p("fees.prepayment_charge_percent").value, "cooling_off_days": p("kfs.cooling_off_days").value},
        "terms_id": terms["id"], "policy_rule_set_id": a["policy_rule_set_id"],
    }
    return d


# ---------------------------------------------------------------- rendering
def _styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    ss = getSampleStyleSheet()
    return {"h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=6), "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=4),
            "p": ParagraphStyle("p", parent=ss["BodyText"], fontSize=9.5, leading=13), "small": ParagraphStyle("small", parent=ss["BodyText"], fontSize=8, leading=10),
            "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontSize=8.5, leading=10.5)}


def _page(title: str):
    from reportlab.lib.colors import HexColor, Color

    def draw(c, doc):
        w, h = doc.pagesize
        c.saveState()
        c.setFillColor(HexColor(BRAND))
        c.rect(0, h - 38, w, 38, stroke=0, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 13)
        c.drawString(36, h - 25, WORDMARK)
        c.setFont("Helvetica", 9)
        c.drawRightString(w - 36, h - 25, title)
        c.setFillColor(Color(0.75, 0.75, 0.75, alpha=0.25))
        c.setFont("Helvetica-Bold", 46)
        c.translate(w / 2, h / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "DEMO - NOT A LOAN OFFER")
        c.restoreState()
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillColor(HexColor("#555555"))
        c.drawString(36, 22, DEMO_FOOTER)
        c.drawRightString(w - 36, 12, f"Page {doc.page}")
        c.restoreState()

    return draw


def _build(title: str, story: list) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=56, bottomMargin=40, title=title, author=WORDMARK,
                            invariant=1)  # same inputs, same bytes: a retried job stores an identical file
    doc.build(story, onFirstPage=_page(title), onLaterPages=_page(title))
    return buf.getvalue()


def _table(rows, widths, header=True, align_right_from: int | None = None):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Table, TableStyle

    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, HexColor("#BBBBBB")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), HexColor("#FDEBDF")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 1 if header else 0), (-1, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _pct(bp) -> str:
    return f"{D(bp) / 100:.2f}% per annum"


def _charges_text(d) -> dict:
    c, f = d["charges"], d["figures"]
    return {
        "processing": f"{D(c['processing_bp']) / 100:.2f}% of the loan amount (minimum {inr(c['processing_min'])}, maximum {inr(c['processing_max'])}), plus GST at "
                      f"{c['gst_percent']}%: {inr(f['processing_fee'])} + {inr(f['gst_on_fee'])} GST = {inr(f['upfront_charges'])}, deducted at disbursement",
        "penal": f"{c['penal_percent_pa']}% per annum on the overdue instalment amount only, for the days it remains overdue. Levied as penal charges, not as "
                 "interest added to the rate; never capitalised (no interest on penal charges) and no further penal charges on unpaid penal charges",
        "prepayment": "Nil" if not c["prepayment_percent"] else f"{c['prepayment_percent']}% of the principal prepaid",
        "cooling_off": f"{c['cooling_off_days']} days from disbursement: the borrower may exit by repaying the principal and the proportionate APR, "
                       "without any penalty",
    }


def render_sanction_letter(d: dict) -> bytes:
    from reportlab.platypus import Paragraph, Spacer

    s, f, t, ch = _styles(), d["figures"], d["terms"], _charges_text(d)
    b = d["borrower"]
    story = [Paragraph(f"Sanction Letter No. {d['sanction_no']}", s["h1"]),
             Paragraph(f"Date: {d['date']:%d %b %Y}<br/>Application: {d['reference']}", s["p"]), Spacer(1, 8),
             Paragraph(f"To<br/>{b['name']}<br/>{'<br/>'.join(x for x in b['address'] if x)}", s["p"]), Spacer(1, 8),
             Paragraph("Subject: Sanction of Personal Loan (standard scheme, demo parameters)", s["h2"]),
             Paragraph(f"Dear {b['name']},", s["p"]),
             Paragraph("We are pleased to inform you that your application for a personal loan has been sanctioned on the terms below. The attached Key Facts "
                       "Statement (KFS) and EMI schedule form part of this letter. Please read them before you accept.", s["p"]), Spacer(1, 6)]
    rows = [["Term", "Details"],
            ["Sanctioned amount", inr(t["amount"])], ["Rate of interest", f"{_pct(t['rate_bp'])}, fixed for the tenure"],
            ["Tenure", f"{t['tenure_months']} months"], ["EMI", f"{inr(f['emi'])} per month ({f['tenure_months']} instalments; last instalment {inr(f['final_instalment'])})"],
            ["Processing fee", Paragraph(ch["processing"], s["cell"])], ["Net amount to be disbursed", inr(f["net_disbursed"])],
            ["Annual Percentage Rate (APR)", f"{f['apr_percent']}% (all charges included; see the KFS)"],
            ["Penal charges", Paragraph(ch["penal"], s["cell"])], ["Prepayment / foreclosure", ch["prepayment"]],
            ["Cooling-off period", Paragraph(ch["cooling_off"], s["cell"])]]
    story += [_table(rows, [150, 373]), Spacer(1, 8), Paragraph("Conditions before disbursement", s["h2"])]
    for i, cond in enumerate(["Your acceptance of this sanction and of the KFS within the KFS validity period "
                              f"(until {d['kfs_valid_until']:%d %b %Y}).",
                              "Execution of the loan agreement, with the KFS as its summary box.",
                              "A repayment mandate (NACH / standing instruction) for the EMI from your bank account.",
                              "Verification of original KYC documents at the branch, if not already done."], 1):
        story.append(Paragraph(f"{i}. {cond}", s["p"]))
    story += [Spacer(1, 6), Paragraph("Any fee or charge not stated in the KFS will not be charged without your explicit consent. If the rate or charges change "
                                      "for reasons permitted in the loan agreement, you will be told before they apply.", s["p"]), Spacer(1, 14),
              Paragraph(f"For {WORDMARK}<br/><br/>Authorised signatory: {d['approver']['name']}, {d['approver']['role']}<br/>"
                        f"Approved on {d['approver']['decided_at']:%d %b %Y} (human decision recorded in the audit trail)", s["p"]), Spacer(1, 14),
              Paragraph("Acceptance: I accept the sanction on the terms above and in the KFS.<br/><br/>Borrower's signature: ____________________   Date: __________",
                        s["p"]), Spacer(1, 10),
              Paragraph(f"Parameters: {d['policy']}. All rates, fees, limits and periods are demo assumptions, not Bank of Baroda's actual terms.", s["small"])]
    return _build(f"Sanction letter {d['sanction_no']}", story)


def _schedule_rows(d) -> list:
    rows = [["No.", "Due date", "Opening principal", "Instalment", "Principal", "Interest", "Closing principal"]]
    rows += [[str(r.number), f"{r.due_date:%d %b %Y}", inr(r.opening), inr(r.payment), inr(r.principal), inr(r.interest), inr(r.closing)] for r in d["schedule"]]
    tot = [sum((r.payment for r in d["schedule"]), D(0)), sum((r.principal for r in d["schedule"]), D(0)), sum((r.interest for r in d["schedule"]), D(0))]
    rows.append(["", "Total", "", inr(tot[0]), inr(tot[1]), inr(tot[2]), ""])
    return rows


def render_kfs(d: dict) -> bytes:
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    s, f, t, ch = _styles(), d["figures"], d["terms"], _charges_text(d)
    cell = lambda x: Paragraph(x, s["cell"])  # noqa: E731
    story = [Paragraph("Key Facts Statement", s["h1"]),
             Paragraph(f"Proposal number: {d['proposal_no']}  |  Date: {d['date']:%d %b %Y}  |  Valid until: {d['kfs_valid_until']:%d %b %Y} "
                       f"({KFS_VALIDITY_WORKING_DAYS} working days)", s["p"]),
             Paragraph(f"Borrower: {d['borrower']['name']}  |  Application: {d['reference']}", s["p"]),
             Paragraph("The terms in this KFS bind the lender if you agree to them within the validity period.", s["small"]),
             Paragraph("Part 1 - Interest rate and fees / charges", s["h2"])]
    rows = [["Sl.", "Item", "Details"],
            ["1", "Loan proposal / account number", d["proposal_no"]], ["2", "Type of loan", "Personal loan (standard scheme), unsecured"],
            ["3", "Sanctioned loan amount", inr(t["amount"])], ["4", "Disbursal schedule", "Single disbursement in full after the conditions are met"],
            ["5", "Loan term", f"{t['tenure_months']} months"],
            ["6", "Instalment details", cell(f"Equated monthly instalments (EMI): {f['tenure_months']} of {inr(f['emi'])} (last instalment {inr(f['final_instalment'])}); "
                                             f"repayment starts one month after disbursement (illustrated from {f['first_instalment_date']:%d %b %Y})")],
            ["7", "Interest rate and type", f"{_pct(t['rate_bp'])}, fixed"],
            ["8", "Fees / charges payable", cell(f"A. To the lender: processing fee {inr(f['processing_fee'])} + GST {inr(f['gst_on_fee'])} = {inr(f['upfront_charges'])} "
                                                 "(one time, deducted at disbursement). B. To third parties through the lender: none")],
            ["9", "Annual Percentage Rate (APR)", f"{f['apr_percent']}%"],
            ["10", "Contingent charges", cell(f"Penal charges: {ch['penal']}. Prepayment / foreclosure: {ch['prepayment']}.")]]
    story += [_table(rows, [24, 150, 349]), Paragraph("Part 2 - Other qualitative information", s["h2"])]
    rows = [["Sl.", "Item", "Details"],
            ["1", "Cooling-off / look-up period", cell(ch["cooling_off"])],
            ["2", "Recovery", cell("Overdue amounts are followed up by the branch. Any recovery agent engaged will carry an authorisation letter and follow the "
                                   "lender's code of conduct; no harassment or intimidation of any kind.")],
            ["3", "Grievance redressal", cell("Branch Manager of the sanctioning branch, then the lender's nodal grievance officer (demo: contact details not "
                                              "applicable). If unresolved within 30 days, the RBI Integrated Ombudsman Scheme (cms.rbi.org.in).")],
            ["4", "Changes to charges", cell("No fee or charge not stated in this KFS will be levied without your explicit consent.")]]
    story += [_table(rows, [24, 150, 349]), Spacer(1, 8),
              Paragraph("Acknowledgement: the contents of this KFS have been explained to me and I have understood them.<br/><br/>"
                        "Borrower's signature: ____________________   Date: __________", s["p"]), PageBreak(),
              Paragraph("Annex A - Computation of the Annual Percentage Rate", s["h2"])]
    rows = [["Sr.", "Parameter", "Details"],
            ["1", "Sanctioned loan amount", inr(t["amount"])], ["2", "Loan term", f"{t['tenure_months']} months, {f['tenure_months']} monthly EMIs of {inr(f['emi'])}"],
            ["3", "Interest rate type", "Fixed"], ["4", "Rate of interest", _pct(t["rate_bp"])],
            ["5", "Total interest over the tenor", inr(f["total_interest"])], ["6", "Fees / charges payable", inr(f["upfront_charges"])],
            ["6A", "Payable to the lender", inr(f["upfront_charges"])], ["6B", "Payable to third parties through the lender", inr(0)],
            ["7", "Net disbursed amount (1 - 6)", inr(f["net_disbursed"])], ["8", "Total amount to be paid by the borrower (1 + 5)", inr(t["amount"] + f["total_interest"])],
            ["9", "Annual Percentage Rate", cell(f"{f['apr_percent']}% - computed on the net disbursed amount using the IRR approach and the reducing balance method")],
            ["10", "Schedule of disbursement", "Single disbursement"], ["11", "Due date of instalments", f"Monthly, from {f['first_instalment_date']:%d %b %Y} (illustrative)"]]
    story += [_table(rows, [28, 230, 265]), Paragraph("Annex B - Repayment schedule", s["h2"]),
              Paragraph("Dates assume disbursement on the KFS date; the actual dates are fixed at disbursement and confirmed to you then.", s["small"]), Spacer(1, 4),
              _table(_schedule_rows(d), [26, 62, 85, 80, 80, 75, 85], align_right_from=2),
              Spacer(1, 8), Paragraph(f"Parameters: {d['policy']}. Demo assumptions, not Bank of Baroda's actual terms.", s["small"])]
    return _build(f"Key Facts Statement {d['proposal_no']}", story)


def render_emi_schedule(d: dict) -> bytes:
    from reportlab.platypus import Paragraph, Spacer

    s, f, t = _styles(), d["figures"], d["terms"]
    story = [Paragraph("EMI Schedule", s["h1"]),
             Paragraph(f"Borrower: {d['borrower']['name']}  |  Sanction: {d['sanction_no']}  |  Application: {d['reference']}", s["p"]),
             Paragraph(f"Loan {inr(t['amount'])} at {_pct(t['rate_bp'])} (fixed) for {t['tenure_months']} months. EMI {inr(f['emi'])}; "
                       f"total interest {inr(f['total_interest'])}; APR {f['apr_percent']}%.", s["p"]),
             Paragraph("Reducing-balance schedule. Dates assume disbursement on the sanction date and are fixed at disbursement.", s["small"]), Spacer(1, 6),
             _table(_schedule_rows(d), [26, 62, 85, 80, 80, 75, 85], align_right_from=2)]
    return _build(f"EMI schedule {d['sanction_no']}", story)


RENDER = {"SANCTION_LETTER": render_sanction_letter, "KFS": render_kfs, "EMI_SCHEDULE": render_emi_schedule}


def markup_safe(d: dict) -> dict:
    """ReportLab's Paragraph parses its text as markup (<img src=...>, <a href=...>, <font>), so every value that came from people or the
    database is escaped before it reaches a renderer: an address line can never fetch a URL, read a local file or restyle the letter.
    Figures, dates and the schedule are produced by this code and are left as they are."""
    from xml.sax.saxutils import escape

    def esc(v):
        if isinstance(v, str):
            return escape(v)
        if isinstance(v, list):
            return [esc(x) for x in v]
        if isinstance(v, dict):
            return {k: esc(x) for k, x in v.items()}
        return v

    return {**d, **{k: esc(d[k]) for k in ("reference", "sanction_no", "proposal_no", "policy", "borrower", "approver") if k in d}}


def render_pack(d: dict) -> dict[str, tuple[bytes, str]]:
    """kind -> (pdf bytes, sha256). Refuses to render if a PAN-like value is anywhere in the data: the pack is emailed externally."""
    if PAN_RE.search(json.dumps(d, default=str)):
        raise ValueError("refusing to render: a PAN-like value is in the sanction pack data")
    safe = markup_safe(d)
    out = {}
    for kind in KINDS:
        pdf = RENDER[kind](safe)
        out[kind] = (pdf, hashlib.sha256(pdf).hexdigest())
    return out


# ---------------------------------------------------------------- job: approved -> documents -> SANCTIONED -> approval email queued
def _store(storage, path: str, pdf: bytes, sha: str) -> None:
    """Upload once; a retried job finds its own identical file already there (rendering is deterministic)."""
    import httpx

    try:
        storage.put(path, pdf, "application/pdf")
    except httpx.HTTPStatusError:
        if hashlib.sha256(storage.get(path)).hexdigest() != sha:
            raise


def sanction_job(conn, job: dict, deps) -> None:
    from app import outbox
    from app.db import staff_tx

    app_id = job["payload"]["application_id"]
    a = conn.execute("SELECT status, version, reference FROM applications WHERE id = %s", (app_id,)).fetchone()
    if a is None or a["status"] != "APPROVED":
        return  # already sanctioned, or no longer approved: nothing to do
    d = pack_data(conn, app_id, deps.today())
    pack = render_pack(d)
    for kind, (pdf, sha) in pack.items():
        _store(deps.generated, f"{app_id}/{kind}-{sha[:16]}.pdf", pdf, sha)
    with staff_tx(conn, actor_type="SYSTEM"):
        ids = [conn.execute("INSERT INTO generated_documents (application_id, kind, storage_path, sha256, terms_id, policy_rule_set_id) VALUES (%s, %s, %s, %s, %s, %s) "
                            "RETURNING id", (app_id, kind, f"{app_id}/{kind}-{sha[:16]}.pdf", sha, d["terms_id"], d["policy_rule_set_id"])).fetchone()["id"]
               for kind, (pdf, sha) in pack.items()]
        conn.execute("SELECT app_transition(%s, 'SANCTIONED', %s, 'SYSTEM', NULL, %s)",
                     (app_id, a["version"], f"Sanction pack generated from the approved terms ({d['sanction_no']}, {d['proposal_no']})"))
        outbox.queue(conn, app_id, "APPROVAL", f"Your personal loan is sanctioned - {a['reference']} (demo)", ids, deps.email_redirect_to)
