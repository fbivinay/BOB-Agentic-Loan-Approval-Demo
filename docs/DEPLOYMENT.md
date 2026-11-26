# Deployment (free tiers only)

Everything here is free. No step needs a billing account, and nothing is bought. Secrets go into each host's secret settings, never into this repository.

| Part | Host | Free-tier limits that matter |
|---|---|---|
| Backend API (FastAPI) | Render free web service (`render.yaml`) | Sleeps after 15 minutes idle (about 1 minute to wake). 750 instance hours a month. No background workers or cron. |
| Database, auth, files | Supabase Free (already live) | Pauses after a week without activity. The hourly drain workflow keeps it awake. |
| Lakehouse | BigQuery sandbox (already live) | No DML or streaming (load jobs and views only). Tables expire after 60 days. |
| Scheduler | GitHub Actions (`policy-monitor.yml`, `jobs-drain.yml`) | Runs can start late. Scheduled workflows are disabled after 60 days without repository activity. |
| Frontend (Vite static build) | Vercel Hobby | Static files only; every path is rewritten to `index.html` (`frontend/vercel.json`). |

## 1. Backend on Render

1. **Create a Render account** with GitHub sign-in. The free web service needs no card.
2. **Create the service:** New > Blueprint, then choose this repository. Render reads `render.yaml`, which sets:
   - Python 3.13.1, the region (Singapore), and the build from `backend/requirements.lock.txt`;
   - the start command, and the `/health` check;
   - production mode: API docs off, HSTS on.
3. **Fill in every secret** the blueprint lists as "sync: false" (Environment tab). Use the same values as your local `.env`, except:
   - `FRONTEND_ORIGIN`: the Vercel URL once the frontend exists; until then, `http://localhost:3000`.
   - `INTERNAL_API_TOKEN`: a new random value, for example from `python -c "import secrets; print(secrets.token_urlsafe(32))"`. The same value goes into GitHub in step 3.
   - `BQ_SERVICE_ACCOUNT_JSON`: see step 2. It stays empty until then, and BigQuery calls fail until it is set.
4. **Deploy.** Then open `https://<service>.onrender.com/health`. It should return `{"status":"ok"}`.

## 1A. Frontend on Vercel

1. **Import the repository** in Vercel (Add New > Project), with:
   - Root Directory: `frontend`;
   - Framework Preset: Vite (build `npm run build`, output `dist`).
2. **Environment variables** (Production):
   - `VITE_API_URL`: `https://<service>.onrender.com`
   - `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`: the same values as the backend's `SUPABASE_URL` and `SUPABASE_ANON_KEY`.
   - `VITE_DEMO_PASSWORD`: the demo staff password (`DEMO_STAFF_PASSWORD`).

   All four end up in the public bundle. The anon key is meant to be public; the demo password is public by design (section 6).
3. **Deploy**, then set Render's `FRONTEND_ORIGIN` to the production URL exactly (`https://<project>.vercel.app`, no trailing slash). The backend allows that one origin, so preview deployments cannot call it.
4. **Check:** open the site, sign in with each demo role, and open a public page such as `/faq` by its direct URL.

## 2. BigQuery credentials for the deployed backend

Locally, the backend impersonates `sa-app-runtime` with your own Google sign-in. Render cannot do that, so the deployed backend needs that one service account's key.
- It is the app identity: it reads the lakehouse and appends to the six tables granted to it.
- It cannot read `lh_truth` (the ground-truth isolation suite proves this).
- It holds no project-wide data rights.

Create the key yourself. It is a credential, and it never goes into the repository or a chat:

```bash
gcloud iam service-accounts keys create sa-app-runtime.json --iam-account=sa-app-runtime@<GCP_PROJECT>.iam.gserviceaccount.com
```

Then:
1. Paste the file's whole JSON into Render's `BQ_SERVICE_ACCOUNT_JSON`.
2. Delete the local file.
3. To rotate: create a new key, update Render, then delete the old key with `gcloud iam service-accounts keys delete`.
4. Re-run `backend/.venv/Scripts/python scripts/test_truth_isolation.py` afterwards. It must still pass 18 of 18.

## 3. GitHub Actions secrets

Under Settings > Secrets and variables > Actions > Secrets:
- `MONITOR_BACKEND_URL`: `https://<service>.onrender.com`
- `INTERNAL_API_TOKEN`: the same value as on Render.

After that:
- `jobs-drain.yml` runs hourly. It is the backend's background worker, flushes analytics to BigQuery, and keeps Supabase awake.
- `policy-monitor.yml` runs daily at 08:00 IST.
- Both can be started by hand (Actions > the workflow > Run workflow).
- Without the secrets they only print a warning.

**Optional: commit a run log.** The policy monitor can commit a compact run log so that GitHub does not disable the schedule after 60 days. It stays off unless `vars.MONITOR_COMMIT_RUN_LOG` is set to `true`, together with `vars.MONITOR_COMMIT_NAME` and `vars.MONITOR_COMMIT_EMAIL`, the commit identity, which is the owner's decision. The alternative is to re-enable the workflow with `gh workflow enable policy-monitor.yml` when GitHub disables it.

## 4. Resend delivery status (optional)

1. In Resend, open Webhooks and add the endpoint `https://<service>.onrender.com/webhooks/resend`. Select the sent, delivered, delivery_delayed, bounced, complained and failed events.
2. Copy the signing secret (`whsec_...`) into Render's `RESEND_WEBHOOK_SECRET`.

Without the webhook, the email status stays at SENT. The current key is send-only, so the backend cannot poll for status either.

## 5. Before a demo

- Open `/health` a minute ahead. A sleeping instance takes about a minute to wake.
- **Optionally reset the demo data.** The public demo logins let anyone change the synthetic state.
  1. Preview it: `backend/.venv/Scripts/python scripts/reset_demo.py --dry-run`.
  2. Then reset: `backend/.venv/Scripts/python scripts/reset_demo.py --confirm <supabase-project-ref>`.

  The reset clears the workflow data, the two private document buckets and the BigQuery events table. It keeps staff, rule sets, policy history and the synthetic bank dataset.
- Check System status (`GET /system/health`): no stale monitoring, no failed jobs, and budgets not used up.

## 6. Before sharing the deployment publicly

- **Reset the Supabase database password** (Supabase > Project Settings > Database). It was exposed during set-up and is close to the demo password. Update `SUPABASE_DB_URL` locally and on Render.
- **Keep Supabase public sign-up turned off.** Only the seeded staff accounts can sign in.
- The one-click demo passwords will be public in the frontend bundle. That is acceptable only because everything is synthetic, budgets cap Gemini, Resend and uploads, all mail goes to a test inbox, and disbursement is simulated (plan section 14A).
