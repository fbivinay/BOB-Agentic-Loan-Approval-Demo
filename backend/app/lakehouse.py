"""Read access to the BigQuery lakehouse, as the application identity (sa-app-runtime), which cannot read ground truth.

Credentials: BQ_SERVICE_ACCOUNT_JSON when set (deployment); otherwise the developer's application-default credentials impersonating
sa-app-runtime, so local runs use exactly the permissions the deployed app has. Every query is parameterised and carries a
bytes-billed cap. The enquiry stage reads INTERNAL bank data only: no bureau or external KYC columns are selected here.
"""

import json
import re
from functools import lru_cache

from app.config import settings

CAP_BYTES = 200 * 1024 * 1024
# Columns the draft stage may use: the bank's own data about an existing customer. Bureau and external KYC results are excluded.
INTERNAL_COLUMNS = [
    "customer_id", "full_name", "date_of_birth", "gender", "mobile", "email", "pan", "address_line", "city", "state", "pincode", "residency_status", "is_staff",
    "bank_relationship", "customer_since", "home_branch_id", "home_branch_name", "home_branch_tier", "kyc_status", "kyc_last_verified_on",
    "employment_type", "employer_name", "employer_category", "designation", "employment_start_date", "current_job_tenure_months", "declared_net_income",
    "has_bob_salary_account", "bob_account_age_months", "avg_balance_6m", "avg_salary_credit_6m", "salary_credit_months_6m", "salary_regularity",
    "avg_business_receipts_6m", "business_receipt_months_6m", "income_credit_vs_declared_pct", "bob_emi_total", "bob_active_loans", "returned_debits_6m",
]
SEARCH_COLUMNS = ["customer_id", "full_name", "mobile", "pan", "city", "home_branch_name", "employment_type", "customer_since", "kyc_status"]


def classify(q: str) -> tuple[str, str]:
    """What kind of identifier the Loan Officer typed."""
    s = q.strip()
    if re.fullmatch(r"(?i)BOB-C\d{4}", s):
        return "customer_id", s.upper()
    if re.fullmatch(r"(?:\+?91[- ]?)?[6-9]\d{9}", s.replace(" ", "")):
        return "mobile", re.sub(r"\D", "", s)[-10:]
    if re.fullmatch(r"(?i)SYNP\d{7}[A-Z]", s):
        return "pan", s.upper()
    if re.fullmatch(r"(?i)ACC-\d{6}", s):
        return "account_id", s.upper()
    if len(s) >= 3 and re.fullmatch(r"[A-Za-z .'-]+", s):
        return "name", s
    raise ValueError("Search by customer ID (BOB-C0001), 10-digit mobile, PAN, account number (ACC-000001) or at least 3 letters of the name")


def search_sql(q: str, project: str) -> tuple[str, dict]:
    kind, value = classify(q)
    cols = ", ".join(f"c.{c}" for c in SEARCH_COLUMNS)
    base = f"SELECT {cols} FROM `{project}.lh_gold.customer_360` c"
    where = "c.bank_relationship = 'ETB'"  # new-to-bank people are not bank customers and are not found, as at a real branch
    if kind == "account_id":
        sql = f"{base} JOIN `{project}.lh_raw.account_holders` h ON h.customer_id = c.customer_id WHERE {where} AND h.account_id = @v"
    elif kind == "name":
        sql = f"{base} WHERE {where} AND LOWER(c.full_name) LIKE CONCAT('%', LOWER(@v), '%')"
    else:
        sql = f"{base} WHERE {where} AND c.{kind} = @v"
    return sql + " ORDER BY c.customer_id LIMIT 10", {"v": value}


@lru_cache(maxsize=1)
def _client():
    from google.cloud import bigquery

    if settings.bq_service_account_json:
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(json.loads(settings.bq_service_account_json),
                                                                       scopes=["https://www.googleapis.com/auth/bigquery"])
    else:
        import google.auth
        from google.auth import impersonated_credentials

        source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        target = settings.bq_impersonate_sa or f"sa-app-runtime@{settings.gcp_project}.iam.gserviceaccount.com"
        creds = impersonated_credentials.Credentials(source_credentials=source, target_principal=target,
                                                     target_scopes=["https://www.googleapis.com/auth/bigquery"], lifetime=3600)
    return bigquery.Client(project=settings.gcp_project, credentials=creds, location=settings.bq_location)


def _run(sql: str, params: dict) -> list[dict]:
    from google.cloud import bigquery

    cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in params.items()], maximum_bytes_billed=CAP_BYTES)
    return [dict(r.items()) for r in _client().query(sql, job_config=cfg).result()]


class Lakehouse:
    def search(self, q: str) -> list[dict]:
        sql, params = search_sql(q, settings.gcp_project)
        return _run(sql, params)

    def customer(self, customer_id: str) -> dict | None:
        cols = ", ".join(INTERNAL_COLUMNS)
        rows = _run(f"SELECT {cols} FROM `{settings.gcp_project}.lh_gold.customer_360` WHERE customer_id = @v AND bank_relationship = 'ETB'", {"v": customer_id.upper()})
        return rows[0] if rows else None


def get_lakehouse() -> Lakehouse:
    return Lakehouse()
