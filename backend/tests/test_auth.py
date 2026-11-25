"""Token verification and role guards, with a locally generated ES256 key standing in for the Supabase signing key."""

import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.auth import Staff, current_staff, get_staff_loader, require_permission, require_roles
from app.config import settings

URL = "https://example-project.supabase.co"
ISS = f"{URL}/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
PERMS = {
    "LOAN_OFFICER": ["enquiry.manage", "application.intake", "document.verify", "application.forward", "disbursement.track"],
    "ASSISTANT_MANAGER": ["decision.make", "disbursement.release"],
    "BRANCH_MANAGER": ["decision.make", "application.takeover", "application.refer", "terms.adjust", "policy.activate", "disbursement.release"],
}
PEOPLE: dict[uuid.UUID, dict] = {}


def person(role: str, active: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    PEOPLE[uid] = {"user_id": uid, "full_name": f"Test {role.title()}", "email": f"{role.lower()}@example.invalid", "role": role, "is_active": active, "permissions": PERMS[role]}
    return uid


def token(sub, *, key=KEY, alg="ES256", omit=(), **over) -> str:
    now = int(time.time())
    claims = {"sub": str(sub), "aud": "authenticated", "iss": ISS, "iat": now, "exp": now + 600, "role": "authenticated"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None and k not in omit}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "test-key"})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", URL)
    pub = KEY.public_key()
    monkeypatch.setattr(auth, "get_jwks_client", lambda: SimpleNamespace(get_signing_key_from_jwt=lambda t: SimpleNamespace(key=pub)))
    app = FastAPI()
    app.dependency_overrides[get_staff_loader] = lambda: (lambda uid: PEOPLE.get(uid))

    @app.get("/me")
    def me(s: Staff = Depends(current_staff)):
        return {"role": s.role}

    @app.get("/manager-only", dependencies=[Depends(require_roles("ASSISTANT_MANAGER", "BRANCH_MANAGER"))])
    def mgr():
        return "ok"

    @app.get("/bm-only", dependencies=[Depends(require_roles("BRANCH_MANAGER"))])
    def bm():
        return "ok"

    @app.get("/decide", dependencies=[Depends(require_permission("decision.make"))])
    def decide():
        return "ok"

    @app.get("/activate-policy", dependencies=[Depends(require_permission("policy.activate"))])
    def policy():
        return "ok"

    return TestClient(app)


def get(client, path, tok=None):
    return client.get(path, headers={"Authorization": f"Bearer {tok}"} if tok else {})


# ---------------------------------------------------------------- token verification
def test_valid_token_returns_the_role_from_the_database(client):
    uid = person("LOAN_OFFICER")
    assert get(client, "/me", token(uid)).json() == {"role": "LOAN_OFFICER"}


def test_role_claims_in_the_token_are_ignored(client):
    uid = person("LOAN_OFFICER")
    t = token(uid, app_metadata={"role": "BRANCH_MANAGER"}, user_metadata={"role": "BRANCH_MANAGER", "staff_role": "BRANCH_MANAGER"})
    assert get(client, "/me", t).json() == {"role": "LOAN_OFFICER"}
    assert get(client, "/bm-only", t).status_code == 403
    assert get(client, "/decide", t).status_code == 403


@pytest.mark.parametrize("name,tok", [
    ("no header", None),
    ("garbage", "not-a-jwt"),
    ("expired", lambda u: token(u, exp=int(time.time()) - 3600)),
    ("wrong audience", lambda u: token(u, aud="anon")),
    ("wrong issuer", lambda u: token(u, iss="https://evil.example/auth/v1")),
    ("signed by another key", lambda u: token(u, key=OTHER_KEY)),
    ("missing exp", lambda u: token(u, omit=("exp",))),
    ("missing sub", lambda u: token(u, omit=("sub",))),
    ("sub is not a uuid", lambda u: token("staff-1")),
    ("anon role", lambda u: token(u, role="anon")),
    ("service role", lambda u: token(u, role="service_role")),
    ("alg none", lambda u: jwt.encode({"sub": str(u), "aud": "authenticated", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 600, "role": "authenticated"}, None, algorithm="none")),
])
def test_bad_tokens_are_rejected_with_401(client, name, tok):
    uid = person("BRANCH_MANAGER")
    t = tok(uid) if callable(tok) else tok
    r = get(client, "/me", t)
    assert r.status_code == 401, name
    assert "signature" not in r.text.lower() and "expired" not in r.text.lower()  # no detail leaks to the caller


def test_tampered_payload_is_rejected(client):
    uid = person("LOAN_OFFICER")
    head, body, sig = token(uid).split(".")
    forged = jwt.encode({"sub": str(person("BRANCH_MANAGER")), "aud": "authenticated", "iss": ISS, "iat": 1, "exp": int(time.time()) + 600, "role": "authenticated"}, OTHER_KEY, algorithm="ES256")
    assert get(client, "/me", f"{head}.{forged.split('.')[1]}.{sig}").status_code == 401


def test_hs256_key_confusion_is_rejected(client):
    uid = person("BRANCH_MANAGER")
    pem = KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    # PyJWT refuses to sign HS256 with a PEM public key, so craft the token by hand: header alg=HS256, signature = HMAC(public key PEM)
    import base64
    import hashlib
    import hmac
    import json

    def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "test-key"}).encode())
    p = b64(json.dumps({"sub": str(uid), "aud": "authenticated", "iss": ISS, "iat": now, "exp": now + 600, "role": "authenticated"}).encode())
    sig = b64(hmac.new(pem, f"{h}.{p}".encode(), hashlib.sha256).digest())
    assert get(client, "/me", f"{h}.{p}.{sig}").status_code == 401


def test_authenticated_user_without_a_staff_profile_gets_403(client):
    assert get(client, "/me", token(uuid.uuid4())).status_code == 403


def test_inactive_staff_get_403(client):
    assert get(client, "/me", token(person("BRANCH_MANAGER", active=False))).status_code == 403


def test_non_bearer_scheme_is_rejected(client):
    r = client.get("/me", headers={"Authorization": f"Basic {token(person('LOAN_OFFICER'))}"})
    assert r.status_code == 401


# ---------------------------------------------------------------- role and permission guards
@pytest.mark.parametrize("role,path,expected", [
    ("LOAN_OFFICER", "/manager-only", 403), ("ASSISTANT_MANAGER", "/manager-only", 200), ("BRANCH_MANAGER", "/manager-only", 200),
    ("LOAN_OFFICER", "/bm-only", 403), ("ASSISTANT_MANAGER", "/bm-only", 403), ("BRANCH_MANAGER", "/bm-only", 200),
    ("LOAN_OFFICER", "/decide", 403), ("ASSISTANT_MANAGER", "/decide", 200), ("BRANCH_MANAGER", "/decide", 200),
    ("LOAN_OFFICER", "/activate-policy", 403), ("ASSISTANT_MANAGER", "/activate-policy", 403), ("BRANCH_MANAGER", "/activate-policy", 200),
])
def test_role_matrix(client, role, path, expected):
    assert get(client, path, token(person(role))).status_code == expected


def test_guards_return_401_before_403_when_unauthenticated(client):
    for path in ("/manager-only", "/bm-only", "/decide", "/activate-policy"):
        assert get(client, path).status_code == 401


def test_unknown_role_in_a_guard_is_a_programming_error():
    with pytest.raises(ValueError):
        require_roles("OWNER")


def test_anon_key_style_token_cannot_reach_any_endpoint(client):
    anon = jwt.encode({"iss": ISS, "role": "anon", "iat": int(time.time()), "exp": int(time.time()) + 3600}, KEY, algorithm="ES256")
    for path in ("/me", "/manager-only", "/decide"):
        assert get(client, path, anon).status_code == 401
