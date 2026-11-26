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
    def __init__(self, url: str, service_key: str, bucket: str = BUCKET, max_bytes: int = MAX_BYTES, mime_types: tuple[str, ...] | None = None):
        if not url or not service_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are needed for document storage")
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket, self.max_bytes = bucket, max_bytes
        self.mime_types = mime_types or tuple(sorted({m for m, _ in SIGNATURES.values()}))
        self.http = httpx.Client(headers={"Authorization": f"Bearer {service_key}", "apikey": service_key}, timeout=60)

    def ensure_bucket(self) -> str:
        """Create the private bucket if it is missing; refuse to use it if it exists but is public."""
        r = self.http.get(f"{self.base}/bucket/{self.bucket}")
        if r.status_code == 200:
            if r.json().get("public"):
                raise RuntimeError(f"bucket {self.bucket} exists but is PUBLIC; make it private in the Supabase dashboard")
            return "exists (private)"
        r = self.http.post(f"{self.base}/bucket", json={"id": self.bucket, "name": self.bucket, "public": False, "file_size_limit": self.max_bytes,
                                                        "allowed_mime_types": list(self.mime_types)})
        r.raise_for_status()
        return "created (private)"

    def put(self, path: str, data: bytes, mime: str) -> None:
        r = self.http.post(f"{self.base}/object/{self.bucket}/{path}", content=data, headers={"Content-Type": mime, "x-upsert": "false"})
        r.raise_for_status()

    def get(self, path: str) -> bytes:
        r = self.http.get(f"{self.base}/object/{self.bucket}/{path}")
        r.raise_for_status()
        return r.content

    def remove(self, path: str) -> None:
        self.http.request("DELETE", f"{self.base}/object/{self.bucket}", json={"prefixes": [path]})

    def signed_url(self, path: str) -> str:
        r = self.http.post(f"{self.base}/object/sign/{self.bucket}/{path}", json={"expiresIn": SIGNED_URL_SECONDS})
        r.raise_for_status()
        return f"{self.base}{r.json()['signedURL']}"


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return Storage(settings.supabase_url, settings.supabase_service_role_key)
