"""Private file storage for uploaded loan documents (Supabase Storage, free tier). The backend is the only client: it uses the service-role key,
which never reaches the browser, and hands out short-lived signed URLs only after the caller's role has been checked.

Object paths are generated here (`{application_id}/{document_id}.{ext}`), never taken from the user.
"""

from functools import lru_cache

import httpx

from app.config import settings

BUCKET = "loan-documents"
MAX_BYTES = 10 * 1024 * 1024
SIGNED_URL_SECONDS = 60
# magic bytes decide the type; the browser's Content-Type and the file name are only cross-checked against them
SIGNATURES = {b"%PDF-": ("application/pdf", "pdf"), b"\x89PNG\r\n\x1a\n": ("image/png", "png"), b"\xff\xd8\xff": ("image/jpeg", "jpg")}


def sniff(data: bytes) -> tuple[str, str] | None:
    """(mime type, extension) from the file's first bytes, or None for anything that is not a PDF, PNG or JPEG."""
    return next((v for sig, v in SIGNATURES.items() if data.startswith(sig)), None)


class Storage:
    def __init__(self, url: str, service_key: str):
        if not url or not service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are needed for document storage")
        self.base = url.rstrip("/") + "/storage/v1"
        self.http = httpx.Client(headers={"Authorization": f"Bearer {service_key}", "apikey": service_key}, timeout=60)

    def ensure_bucket(self) -> str:
        """Create the private bucket if it is missing; refuse to use it if it exists but is public."""
        r = self.http.get(f"{self.base}/bucket/{BUCKET}")
        if r.status_code == 200:
            if r.json().get("public"):
                raise RuntimeError(f"bucket {BUCKET} exists but is PUBLIC; make it private in the Supabase dashboard")
            return "exists (private)"
        r = self.http.post(f"{self.base}/bucket", json={"id": BUCKET, "name": BUCKET, "public": False, "file_size_limit": MAX_BYTES,
                                                        "allowed_mime_types": sorted({m for m, _ in SIGNATURES.values()})})
        r.raise_for_status()
        return "created (private)"

    def put(self, path: str, data: bytes, mime: str) -> None:
        r = self.http.post(f"{self.base}/object/{BUCKET}/{path}", content=data, headers={"Content-Type": mime, "x-upsert": "false"})
        r.raise_for_status()

    def remove(self, path: str) -> None:
        self.http.request("DELETE", f"{self.base}/object/{BUCKET}", json={"prefixes": [path]})

    def signed_url(self, path: str) -> str:
        r = self.http.post(f"{self.base}/object/sign/{BUCKET}/{path}", json={"expiresIn": SIGNED_URL_SECONDS})
        r.raise_for_status()
        return f"{self.base}{r.json()['signedURL']}"


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return Storage(settings.supabase_url, settings.supabase_service_role_key)
