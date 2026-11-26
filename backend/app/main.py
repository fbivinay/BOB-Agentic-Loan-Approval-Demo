import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.auth import Staff, current_staff
from app.config import settings
from app.routers import applications, assessment, disbursement, documents, enquiries, review, sanction

app = FastAPI(title="Baroda Lending Desk API (demo)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(enquiries.router)
app.include_router(applications.router)
app.include_router(documents.router)
app.include_router(assessment.router)
app.include_router(review.router)
app.include_router(sanction.router)
app.include_router(disbursement.router)

# Database guards explain themselves (who may do what, stale versions). Map them to HTTP; never leak anything else.
_DB_STATUS = {"40001": 409, "42501": 403, "23514": 422, "23505": 409, "23502": 422, "22P02": 422, "P0001": 422}


@app.exception_handler(psycopg.Error)
def database_error(request: Request, exc: psycopg.Error):
    code = _DB_STATUS.get(exc.sqlstate or "")
    if code is None:
        return JSONResponse({"detail": "Database error"}, status_code=500)
    return JSONResponse({"detail": exc.diag.message_primary or "Rejected by the database"}, status_code=code)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(staff: Staff = Depends(current_staff)) -> dict:
    """The signed-in member of staff, with the role and permissions the database holds for them."""
    return {"user_id": str(staff.user_id), "full_name": staff.full_name, "email": staff.email, "role": staff.role, "permissions": sorted(staff.permissions)}
