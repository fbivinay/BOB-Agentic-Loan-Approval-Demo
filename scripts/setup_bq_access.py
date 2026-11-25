"""Create the BigQuery datasets and the runtime identities with ground-truth isolation. Idempotent, free, no billing.

    backend/.venv/Scripts/python scripts/setup_bq_access.py

Identities (service accounts, no keys are created here):
  sa-app-runtime   the backend API              : may read lh_raw, run query jobs, NO access to lh_truth
  sa-agent-runtime the LangGraph agents         : same as the app identity
  sa-truth-eval    controlled generation / evaluation processes : may read lh_truth
Isolation is enforced by BigQuery dataset-level access, not by naming or convention:
  * runtime identities hold only roles/bigquery.jobUser at project level (can run jobs, cannot read any data);
  * dataset access entries grant them READER on lh_raw only; lh_truth lists only the evaluation identity.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

P = settings.gcp_project
LOC = settings.bq_location
SA = {n: f"{n}@{P}.iam.gserviceaccount.com" for n in ("sa-app-runtime", "sa-agent-runtime", "sa-truth-eval")}


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    exe = shutil.which(args[0])
    r = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=240)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(args)}\n{(r.stdout + r.stderr).strip()[:600]}")
    return r


def main() -> None:
    assert P, "GCP_PROJECT not set"
    me = sh("gcloud", "config", "get-value", "account").stdout.strip()
    print("project:", P, "| acting as:", me)
    sh("gcloud", "services", "enable", "iam.googleapis.com", f"--project={P}")  # free API
    for name in SA:
        if sh("gcloud", "iam", "service-accounts", "describe", SA[name], f"--project={P}", check=False).returncode != 0:
            sh("gcloud", "iam", "service-accounts", "create", name, f"--project={P}", f"--display-name={name}")
            print("created", name)
        # runtime identities may only run query jobs at project level
        sh("gcloud", "projects", "add-iam-policy-binding", P, f"--member=serviceAccount:{SA[name]}", "--role=roles/bigquery.jobUser", "--condition=None", "--quiet")
        # the operator may impersonate them (used by the isolation test); no keys are created
        sh("gcloud", "iam", "service-accounts", "add-iam-policy-binding", SA[name], f"--project={P}", f"--member=user:{me}", "--role=roles/iam.serviceAccountTokenCreator", "--quiet")

    for ds, desc in (("lh_raw", "SYNTHETIC DATA ONLY. Application-facing raw layer."), ("lh_truth", "SYNTHETIC GROUND TRUTH. Not readable by application or agent identities.")):
        sh("bq", f"--project_id={P}", f"--location={LOC}", "mk", "--dataset", f"--description={desc}", f"{P}:{ds}", check=False)
        info = json.loads(sh("bq", f"--project_id={P}", "show", "--format=prettyjson", f"{P}:{ds}").stdout)
        access = [a for a in info.get("access", []) if a.get("userByEmail") not in SA.values()]  # reset our entries, keep the owner's
        who = ("sa-app-runtime", "sa-agent-runtime") if ds == "lh_raw" else ("sa-truth-eval",)
        access += [{"role": "READER", "userByEmail": SA[n]} for n in who]
        info["access"] = access
        tmp = Path(__file__).with_name(f"_acl_{ds}.json")
        tmp.write_text(json.dumps({"access": access}), encoding="utf-8")
        sh("bq", f"--project_id={P}", "update", "--source", str(tmp), f"{P}:{ds}")
        tmp.unlink()
        print(f"{ds}: READER = {', '.join(who)}")

    # tiny probe tables (DDL; no DML in the sandbox) used only by the isolation test
    for ds in ("lh_raw", "lh_truth"):
        sh("bq", f"--project_id={P}", f"--location={LOC}", "query", "--nouse_legacy_sql", f"CREATE OR REPLACE TABLE `{P}.{ds}.isolation_probe` AS SELECT '{ds}' AS dataset, 'probe row' AS note")
    print("done")


if __name__ == "__main__":
    main()
