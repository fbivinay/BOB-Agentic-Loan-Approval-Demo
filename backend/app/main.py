import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.auth import Staff, current_staff
from app.config import settings
from app.routers import analytics, applications, assessment, disbursement, documents, enquiries, policy, review, sanction, system

PRODUCTION = settings.environment == "production"
app = FastAPI(title="Baroda Lending Desk API (demo)", docs_url=None if PRODUCTION else "/docs", redoc_url=None,
              openapi_url=None if PRODUCTION else "/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,  # the browser sends a bearer token, never cookies
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Every response: no sniffing, no framing, no referrer, and API data never cached by a browser or proxy."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    if PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(enquiries.router)
app.include_router(applications.router)
app.include_router(documents.router)
app.include_router(assessment.router)
app.include_router(review.router)
app.include_router(sanction.router)
app.include_router(disbursement.router)
app.include_router(policy.router)
app.include_router(system.router)
app.include_router(analytics.router)

# Database guards explain themselves (who may do what, stale versions). Map them to HTTP; never leak anything else.
_DB_STATUS = {"40001": 409, "42501": 403, "23514": 422, "23505": 409, "23502": 422, "22P02": 422, "P0001": 422}


@app.exception_handler(psycopg.Error)
def database_error(request: Request, exc: psycopg.Error):
    code = _DB_STATUS.get(exc.sqlstate or "")
    if code is None:
        return JSONResponse({"detail": "Database error"}, status_code=500)
    return JSONResponse({"detail": exc.diag.message_primary or "Rejected by the database"}, status_code=code)


@app.api_route("/health", methods=["GET", "HEAD"])  # uptime monitors send HEAD
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(staff: Staff = Depends(current_staff)) -> dict:
    """The signed-in member of staff, with the role and permissions the database holds for them."""
    return {"user_id": str(staff.user_id), "full_name": staff.full_name, "email": staff.email, "role": staff.role, "permissions": sorted(staff.permissions)}
