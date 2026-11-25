"""Document text and AI extraction output in the BigQuery lakehouse (lh_raw), as the plan places them.

Tables (created by scripts/setup_ocr_tables.py; the app identity may append to the first two and only read the third):
  ocr_pages            one row per page of text: source file hash, document, page, text, extraction_method, engine. Imported Kaggle
                       batches of the official Unlimited-OCR (PRECOMPUTED_UNLIMITED_OCR) arrive here keyed by file hash, with no document yet.
  extraction_outputs   the raw validated AI output per document, keyed by file hash, model and prompt version (also the extraction cache)
  prepared_demo_values generated demonstration values for degraded demo documents (PREPARED_DEMO_TEXT), loaded by the owner, keyed by file hash

The sandbox has no DML or streaming, so rows are appended with load jobs.
"""

import json
from datetime import datetime, timezone

from app.config import settings
from app.lakehouse import _client, _run

TABLES = ("ocr_pages", "extraction_outputs", "prepared_demo_values")


def _t(name: str) -> str:
    return f"`{settings.gcp_project}.lh_raw.{name}`"


class OcrStore:
    def append(self, table: str, rows: list[dict]) -> None:
        from google.cloud import bigquery

        assert table in ("ocr_pages", "extraction_outputs")
        now = datetime.now(timezone.utc).isoformat()
        ref = f"{settings.gcp_project}.lh_raw.{table}"
        # append only, to the existing table with its own schema: the app identity may not create tables
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", create_disposition="CREATE_NEVER", source_format="NEWLINE_DELIMITED_JSON",
                                     schema=_client().get_table(ref).schema)
        _client().load_table_from_json([{"created_at": now, **r} for r in rows], ref, job_config=cfg).result()

    def precomputed_pages(self, sha256: str) -> list[dict]:
        """Real Unlimited-OCR output imported from a Kaggle batch for this exact file, newest import only."""
        return _run(f"SELECT page_no, text, extraction_method, engine, engine_version FROM {_t('ocr_pages')} "
                    "WHERE source_sha256 = @s AND extraction_method = 'PRECOMPUTED_UNLIMITED_OCR' AND document_id IS NULL "
                    "QUALIFY created_at = MAX(created_at) OVER () ORDER BY page_no", {"s": sha256})

    def document_pages(self, document_id: str) -> list[dict]:
        return _run(f"SELECT page_no, text, extraction_method, engine, engine_version FROM {_t('ocr_pages')} WHERE document_id = @d "
                    "QUALIFY created_at = MAX(created_at) OVER () ORDER BY page_no", {"d": document_id})

    def prepared_values(self, sha256: str) -> dict | None:
        rows = _run(f"SELECT fields_json FROM {_t('prepared_demo_values')} WHERE source_sha256 = @s ORDER BY created_at DESC LIMIT 1", {"s": sha256})
        return json.loads(rows[0]["fields_json"]) if rows else None

    def cached_extraction(self, sha256: str, model: str, prompt_version: str) -> dict | None:
        rows = _run(f"SELECT output_json FROM {_t('extraction_outputs')} WHERE source_sha256 = @s AND model = @m AND prompt_version = @p "
                    "ORDER BY created_at DESC LIMIT 1", {"s": sha256, "m": model, "p": prompt_version})
        return json.loads(rows[0]["output_json"]) if rows else None


def get_ocr_store() -> OcrStore:
    return OcrStore()
