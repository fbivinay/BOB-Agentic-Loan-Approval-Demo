"""Phase 13 units: the KPI headline arithmetic and labels, the five-minute cache, and that the gold views only read the lakehouse's own
application layers (never ground truth)."""

from pathlib import Path

from app import analytics

ROWS = {
    "monthly": [{"source": "HISTORICAL", "decisions": 10, "approved": 7, "approved_amount": 3500000, "tat_days_total": 55.0},
                {"source": "HISTORICAL", "decisions": 10, "approved": 6, "approved_amount": 3000000, "tat_days_total": 60.0},
                {"source": "LIVE", "decisions": 4, "approved": 3, "approved_amount": None, "tat_days_total": 2.0}],
    "ai_vs_human": [{"ai_recommendation": "RECOMMENDED_FOR_APPROVAL", "outcome": "APPROVED", "is_override": False, "decisions": 3},
                    {"ai_recommendation": "RECOMMENDED_FOR_APPROVAL", "outcome": "DECLINED", "is_override": True, "decisions": 1}],
    "disbursements": [{"month": "2026-11-01", "disbursements": 2, "net_amount": 790560, "approved_amount": 800000}],
    "portfolio": [{"product_type": "PERSONAL", "status": "ACTIVE", "loans": 97, "outstanding": 6172501}, {"product_type": "PERSONAL", "status": "CLOSED", "loans": 18, "outstanding": 0}],
}


def test_headline_figures_are_computed_from_the_views():
    analytics._cache.clear()
    o = analytics.overview(run=lambda sql, p: next((ROWS.get(k, []) for k, q in analytics.QUERIES.items() if q.split("`")[2] == sql.split("`")[2]), []), now=1000)
    h = o["headline"]
    assert h["historical"]["decisions"] == 20 and h["historical"]["approval_rate"] == 0.65 and h["historical"]["avg_tat_days"] == 5.75
    assert h["historical"]["approved_amount"] == 6500000
    assert h["live"]["decisions"] == 4 and h["live"]["approval_rate"] == 0.75 and h["live"]["override_rate"] == 0.25 and h["live"]["disbursed_net"] == 790560
    assert h["portfolio"] == {"loans": 115, "outstanding": 6172501}
    assert "synthetic" in o["labels"]["historical"].lower() and o["labels"]["data"] == "All data is synthetic."


def test_results_are_cached_for_five_minutes():
    analytics._cache.clear()
    calls = []

    def run(sql, p):
        calls.append(sql)
        return []

    analytics.overview(run=run, now=0)
    n = len(calls)
    analytics.overview(run=run, now=analytics.CACHE_SECONDS - 1)
    assert len(calls) == n
    analytics.overview(run=run, now=analytics.CACHE_SECONDS + 1)
    assert len(calls) == 2 * n
    analytics._cache.clear()


def test_the_kpi_views_never_read_ground_truth():
    sql = (Path(__file__).resolve().parents[2] / "bigquery" / "gold" / "03_analytics.sql").read_text(encoding="utf-8")
    assert "lh_truth" not in sql and "historical_applications" in sql and "lh_raw.events" in sql
