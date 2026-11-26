"""Phase 14: the daily upload cap (the demo logins are public and the free storage is 1 GB)."""

from app.config import settings
from test_applications_api import PNG, new_app, storage, upload  # noqa: F401  (storage is a fixture)
from test_enquiries_api import api, env  # noqa: F401  (fixtures)


def test_uploads_stop_at_the_daily_cap(api, env, storage, monkeypatch):  # noqa: F811
    env["admin"].execute("DELETE FROM usage_counters WHERE service = 'upload'")
    aid = new_app(api, customer_id="BOB-C0118")
    lo = api.as_("LOAN_OFFICER")
    monkeypatch.setattr(settings, "upload_daily_cap", 1)
    assert upload(lo, aid, "APPLICATION_FORM", "APPLICATION_FORM").status_code == 201  # inside the cap
    r = upload(lo, aid, "PHOTOGRAPH", "PHOTOGRAPH", data=PNG, name="photo.png", mime="image/png")
    assert r.status_code == 429 and "daily upload limit" in r.json()["detail"] and len(storage.objects) == 1  # nothing stored over the cap
