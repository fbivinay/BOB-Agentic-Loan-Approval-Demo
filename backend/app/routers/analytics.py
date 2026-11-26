"""Analytics for Assistant and Branch Managers (analytics.view): KPIs from the BigQuery gold views, read as the app identity.
History is the generator's synthetic record; live is what people did in this demo. All data is synthetic."""

from fastapi import APIRouter, Depends, HTTPException

from app import analytics
from app.auth import Staff, require_permission

router = APIRouter(tags=["analytics"])


def get_kpis():
    return analytics.overview


@router.get("/analytics/overview")
def overview(staff: Staff = Depends(require_permission("analytics.view")), kpis=Depends(get_kpis)):
    try:
        return kpis()
    except Exception as e:  # noqa: BLE001  the lakehouse is unreachable: say so plainly, without internal detail
        raise HTTPException(503, f"Analytics are unavailable right now ({type(e).__name__}); try again shortly") from None
