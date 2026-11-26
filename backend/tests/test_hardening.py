"""Phase 14: response hardening. Security headers on every response, API docs off in production, HSTS in production, CORS only for the
configured frontend origin."""

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]


def test_every_response_carries_the_security_headers():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers  # development serves plain http locally


def test_production_turns_the_api_docs_off_and_hsts_on():
    code = ("from fastapi.testclient import TestClient; import app.main as m; c = TestClient(m.app); "
            "print(c.get('/docs').status_code, c.get('/openapi.json').status_code, c.get('/health').headers.get('strict-transport-security'))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env={**os.environ, "ENVIRONMENT": "production"}, capture_output=True, text=True, timeout=120)
    assert out.stdout.split() == ["404", "404", "max-age=31536000;", "includeSubDomains"], out.stderr[-500:]


def test_cors_answers_only_the_configured_frontend():
    c = TestClient(app)
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = c.options("/me", headers={"Origin": settings.frontend_origin, **pre})
    assert ok.headers.get("access-control-allow-origin") == settings.frontend_origin and "access-control-allow-credentials" not in ok.headers
    bad = c.options("/me", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers
