from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import Staff, current_staff
from app.config import settings

app = FastAPI(title="Baroda Lending Desk API (demo)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(staff: Staff = Depends(current_staff)) -> dict:
    """The signed-in member of staff, with the role and permissions the database holds for them."""
    return {"user_id": str(staff.user_id), "full_name": staff.full_name, "email": staff.email, "role": staff.role, "permissions": sorted(staff.permissions)}
