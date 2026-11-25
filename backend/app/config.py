"""Settings come from environment variables (a local .env at the repo root in development,
deployment secrets in production). Nothing here is ever logged or returned by an endpoint."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Supabase (SUPABASE_URL and the anon key are public by design; the service role key is backend only)
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""  # Postgres connection string from the Supabase project settings; backend only

    # Google Cloud / BigQuery sandbox (no billing). Credentials: service account JSON in this variable.
    gcp_project: str = ""
    bq_location: str = "US"
    bq_service_account_json: str = ""
    bq_impersonate_sa: str = ""  # local runs: impersonate this identity (default sa-app-runtime) with application-default credentials

    # Gemini Developer API (free tier)
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_daily_cap: int = 200  # stop calling Gemini for the day at this many calls (free-tier guard)

    # Resend (free plan)
    resend_api_key: str = ""
    email_from: str = "onboarding@resend.dev"
    email_redirect_to: str = ""

    # CORS for the Next.js app
    frontend_origin: str = "http://localhost:3000"
    internal_api_token: str = ""  # shared secret for /internal endpoints (GitHub Actions cron); unset = those endpoints are off


settings = Settings()
