"""Create the private Supabase Storage bucket for uploaded loan documents (idempotent). Refuses to continue if the bucket exists but is public.

    backend/.venv/Scripts/python scripts/setup_storage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.storage import BUCKET, get_storage  # noqa: E402

if __name__ == "__main__":
    print(f"bucket {BUCKET}: {get_storage().ensure_bucket()}")
