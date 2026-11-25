"""Authentication and authorisation for the staff API.

* Tokens are Supabase Auth JWTs, verified here against the project's public signing keys (JWKS). No secret is needed.
* Only asymmetric algorithms are accepted (so an HS256 token signed with a public key cannot pass), and exp, iat, sub, aud and iss are required.
* The browser's anon key is also a JWT: it has no `sub` and role `anon`, so it is rejected.
* The staff role is read from the staff_profiles table on every request, never from token claims or user-editable metadata.
* Every endpoint declares what it needs with require_roles(...) or require_permission(...). The frontend hides things; this enforces.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings
from app.db import load_staff

ROLES = ("LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER")
ALLOWED_ALGORITHMS = ["ES256", "RS256"]
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Staff:
    user_id: UUID
    full_name: str
    email: str
    role: str
    permissions: frozenset[str]


def _unauthorised(detail: str = "Invalid or missing credentials") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    if not settings.supabase_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is not configured")
    return PyJWKClient(f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=600)


def verify_token(token: str, jwks_client=None) -> dict:
    """Return the verified claims or raise 401. Error text is deliberately generic."""
    client = jwks_client or get_jwks_client()
    try:
        key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=ALLOWED_ALGORITHMS,
            audience="authenticated",
            issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            leeway=30,
        )
    except HTTPException:
        raise
    except Exception:  # expired, bad signature, wrong audience or issuer, unknown key, malformed: all the same to the caller
        raise _unauthorised() from None
    if claims.get("role") != "authenticated":
        raise _unauthorised()
    try:
        UUID(str(claims["sub"]))
    except ValueError:
        raise _unauthorised() from None
    return claims


def get_staff_loader() -> Callable[[UUID], dict | None]:
    return load_staff


def current_staff(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), loader=Depends(get_staff_loader)) -> Staff:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorised()
    claims = verify_token(creds.credentials)
    row = loader(UUID(claims["sub"]))
    if row is None or not row["is_active"] or row["role"] not in ROLES:
        # authenticated with Supabase, but not an active member of staff
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No active staff profile for this account")
    return Staff(UUID(str(row["user_id"])), row["full_name"], row["email"], row["role"], frozenset(row["permissions"]))


def require_roles(*roles: str):
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"unknown roles {unknown}")

    def dep(staff: Staff = Depends(current_staff)) -> Staff:
        if staff.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role may not do this")
        return staff

    return dep


def require_permission(permission: str):
    def dep(staff: Staff = Depends(current_staff)) -> Staff:
        if permission not in staff.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role may not do this")
        return staff

    return dep


def require_any_permission(*permissions: str):
    def dep(staff: Staff = Depends(current_staff)) -> Staff:
        if not set(permissions) & staff.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role may not do this")
        return staff

    return dep
