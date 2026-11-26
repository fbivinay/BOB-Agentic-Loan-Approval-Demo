"""Analytics: the Supabase analytics_outbox is loaded into BigQuery lh_raw.events, and the gold KPI views read it beside the synthetic history.

Sync (plan section 12):
  * events are written to the outbox in the same transaction as the change (decisions, status changes, simulated disbursements);
  * a batch takes due PENDING rows (FOR UPDATE SKIP LOCKED, so two drains never load the same rows), skips event ids BigQuery already has,
    appends the rest with one load job (the sandbox has no DML or streaming), and marks them SENT;
  * a failed batch backs off (1 min, 5 min, 30 min, 2 h, 6 h); after 6 attempts the rows are FAILED, shown in the system view with a retry.
  The gold views also de-duplicate on event_id, so even a load that succeeded but was not marked SENT is never counted twice.

KPIs (read-only, Assistant and Branch Manager): decisions by month, channel and role; approval rate and turnaround; AI recommendation against
the human decision (overrides); the live funnel; simulated disbursements; and the existing loan book by product with days-past-due buckets.
History is the generator's synthetic record (2024-2026); live is what people did in this demo. Both are labelled. All data is synthetic.
"""

import json
import time
from datetime import datetime, timezone

from app.db import staff_tx

BACKOFF_SECONDS = (60, 300, 1800, 7200, 21600)
MAX_ATTEMPTS = 6
BATCH = 500


class EventSink:
    """lh_raw.events in BigQuery, as the app identity (append-only grant on this one table)."""

    def existing(self, event_ids: list[str]) -> set[str]:
        from google.cloud import bigquery

        from app.config import settings
        from app.lakehouse import CAP_BYTES, _client

        if not event_ids:
            return set()
        cfg = bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", event_ids)], maximum_bytes_billed=CAP_BYTES)
        sql = f"SELECT event_id FROM `{settings.gcp_project}.lh_raw.events` WHERE event_id IN UNNEST(@ids)"
        return {r["event_id"] for r in _client().query(sql, job_config=cfg).result()}

    def append(self, rows: list[dict]) -> None:
        from google.cloud import bigquery

        from app.config import settings
        from app.lakehouse import _client

        if not rows:
            return
        ref = f"{settings.gcp_project}.lh_raw.events"
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", create_disposition="CREATE_NEVER", source_format="NEWLINE_DELIMITED_JSON",
                                     schema=_client().get_table(ref).schema)
        _client().load_table_from_json(rows, ref, job_config=cfg).result()


def sync(conn, sink, limit: int = BATCH) -> dict:
    """Load one batch of due outbox rows. Never raises: a failure is recorded on the rows and retried later."""
    with conn.transaction():
        rows = conn.execute("SELECT id, event_id, application_id, event_type, payload, occurred_at, attempts FROM analytics_outbox "
                            "WHERE status = 'PENDING' AND next_attempt_at <= now() ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s", (limit,)).fetchall()
        if not rows:
            return {"loaded": 0, "skipped": 0, "failed": 0}
        ids = [str(r["event_id"]) for r in rows]
        try:
            have = sink.existing(ids)
            now = datetime.now(timezone.utc).isoformat()
            sink.append([{"event_id": str(r["event_id"]), "event_type": r["event_type"], "application_id": str(r["application_id"]) if r["application_id"] else None,
                          "occurred_at": r["occurred_at"].isoformat(), "payload": json.dumps(r["payload"], default=str), "loaded_at": now}
                         for r in rows if str(r["event_id"]) not in have])
        except Exception as e:  # noqa: BLE001  recorded on every row of the batch, retried with backoff
            note = f"{type(e).__name__}: {e}"[:300]
            for r in rows:
                n = r["attempts"] + 1
                delay = BACKOFF_SECONDS[min(n, len(BACKOFF_SECONDS)) - 1]
                conn.execute("UPDATE analytics_outbox SET attempts = %s, last_error = %s, status = %s, next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s",
                             (n, note, "FAILED" if n >= MAX_ATTEMPTS else "PENDING", delay, r["id"]))
            return {"loaded": 0, "skipped": 0, "failed": len(rows), "error": type(e).__name__}
        conn.execute("UPDATE analytics_outbox SET status = 'SENT', sent_at = now(), attempts = attempts + 1, last_error = NULL WHERE id = ANY(%s)",
                     ([r["id"] for r in rows],))
        return {"loaded": len(rows) - len(have), "skipped": len(have), "failed": 0}


def retry_failed(conn, staff, event_ids: list | None) -> int:
    """A person puts FAILED events back in the queue (all, or the ones named). Recorded in the audit trail."""
    with staff_tx(conn, staff.user_id, staff.role):
        sql = "UPDATE analytics_outbox SET status = 'PENDING', attempts = 0, next_attempt_at = now() WHERE status = 'FAILED'"
        n = conn.execute(sql + (" AND event_id = ANY(%s)" if event_ids else ""), ((event_ids,) if event_ids else ())).rowcount
        conn.execute("SELECT audit_write('STAFF', %s, %s, 'ANALYTICS_RETRY', 'analytics_outbox', NULL, NULL, NULL, %s, NULL)",
                     (staff.user_id, staff.role, json.dumps({"requeued": n, "event_ids": [str(e) for e in event_ids or []]})))
    return n


# ---------------------------------------------------------------- KPIs (gold views, as the app identity)
QUERIES = {
    "monthly": "SELECT * FROM `{P}.lh_gold.kpi_decisions_monthly` ORDER BY month, source",
    "by_channel": "SELECT source, channel, SUM(decisions) AS decisions, SUM(approved) AS approved, SAFE_DIVIDE(SUM(approved), SUM(decisions)) AS approval_rate, "
                  "SAFE_DIVIDE(SUM(tat_days_total), SUM(decisions)) AS avg_tat_days FROM `{P}.lh_gold.kpi_decisions_monthly` GROUP BY source, channel ORDER BY source, channel",
    "by_role": "SELECT source, role, SUM(decisions) AS decisions, SUM(approved) AS approved, SAFE_DIVIDE(SUM(approved), SUM(decisions)) AS approval_rate "
               "FROM `{P}.lh_gold.kpi_decisions_monthly` GROUP BY source, role ORDER BY source, role",
    "ai_vs_human": "SELECT * FROM `{P}.lh_gold.kpi_ai_vs_human` ORDER BY ai_recommendation, outcome",
    "funnel": "SELECT * FROM `{P}.lh_gold.kpi_funnel_live` ORDER BY step",
    "disbursements": "SELECT * FROM `{P}.lh_gold.kpi_disbursements_live` ORDER BY month",
    "portfolio": "SELECT * FROM `{P}.lh_gold.kpi_portfolio` ORDER BY product_type, status",
    "dpd": "SELECT * FROM `{P}.lh_gold.kpi_portfolio_dpd` ORDER BY bucket_order",
}
_cache: dict = {}
CACHE_SECONDS = 300  # dashboards re-read at most every five minutes: a handful of tiny queries, far inside the free monthly allowance


def _plain(v):
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def overview(run=None, now: float | None = None) -> dict:
    from app.config import settings
    from app.lakehouse import _run

    run = run or _run
    now = time.time() if now is None else now
    if "at" in _cache and now - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    data = {k: [{c: _plain(v) for c, v in r.items()} for r in run(sql.replace("{P}", settings.gcp_project), {})] for k, sql in QUERIES.items()}
    hist = [r for r in data["monthly"] if r["source"] == "HISTORICAL"]
    live = [r for r in data["monthly"] if r["source"] == "LIVE"]
    decided = lambda rs: sum(r["decisions"] for r in rs)  # noqa: E731
    approved = lambda rs: sum(r["approved"] for r in rs)  # noqa: E731
    ai = data["ai_vs_human"]
    headline = {
        "historical": {"decisions": decided(hist), "approval_rate": approved(hist) / decided(hist) if decided(hist) else None,
                       "avg_tat_days": sum(r["tat_days_total"] for r in hist) / decided(hist) if decided(hist) else None,
                       "approved_amount": sum(r["approved_amount"] or 0 for r in hist)},
        "live": {"decisions": decided(live), "approval_rate": approved(live) / decided(live) if decided(live) else None,
                 "override_rate": sum(r["decisions"] for r in ai if r["is_override"]) / sum(r["decisions"] for r in ai) if ai else None,
                 "disbursed_net": sum(r["net_amount"] or 0 for r in data["disbursements"])},
        "portfolio": {"loans": sum(r["loans"] for r in data["portfolio"]), "outstanding": sum(r["outstanding"] or 0 for r in data["portfolio"] if r["status"] != "CLOSED")},
    }
    result = {"headline": headline, **data, "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "labels": {"historical": "Synthetic history from the data generator (2024-2026), not real Bank of Baroda data",
                         "live": "Decisions and simulated disbursements made in this demo", "data": "All data is synthetic."}}
    _cache.update(at=now, data=result)
    return result
