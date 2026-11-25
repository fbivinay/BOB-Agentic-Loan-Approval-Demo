"""Proves ground-truth isolation in BigQuery by acting as each identity (service-account impersonation, no keys).

    backend/.venv/Scripts/python scripts/test_truth_isolation.py

Exit code 0 only if: runtime identities CAN read lh_raw (positive control), CANNOT read, list or describe lh_truth in any
way tried, hold no project role beyond bigquery.jobUser, and the evaluation identity CAN read lh_truth.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

P, LOC = settings.gcp_project, settings.bq_location
SA = {n: f"{n}@{P}.iam.gserviceaccount.com" for n in ("sa-app-runtime", "sa-agent-runtime", "sa-truth-eval")}
BASE = f"https://bigquery.googleapis.com/bigquery/v2/projects/{P}"
results: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def token(sa: str) -> str:
    r = subprocess.run([shutil.which("gcloud"), "auth", "print-access-token", f"--impersonate-service-account={sa}"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"cannot impersonate {sa}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def query(tok: str, sql: str) -> httpx.Response:
    return httpx.post(f"{BASE}/queries", headers={"Authorization": f"Bearer {tok}"}, json={"query": sql, "useLegacySql": False, "location": LOC, "maximumBytesBilled": "20971520"}, timeout=60)


def get(tok: str, path: str) -> httpx.Response:
    return httpx.get(f"https://bigquery.googleapis.com/bigquery/v2/projects/{P}/{path}", headers={"Authorization": f"Bearer {tok}"}, timeout=60)


def denied(r: httpx.Response) -> bool:
    return r.status_code in (403, 404) and r.status_code != 200


def main() -> int:
    for name in ("sa-app-runtime", "sa-agent-runtime"):
        tok = token(SA[name])
        r = query(tok, f"SELECT note FROM `{P}.lh_raw.isolation_probe`")
        rec(f"{name}: CAN read lh_raw (positive control)", r.status_code == 200 and r.json().get("rows"), f"HTTP {r.status_code}")
        r = query(tok, f"SELECT note FROM `{P}.lh_truth.isolation_probe`")
        rec(f"{name}: CANNOT query lh_truth tables", denied(r), f"HTTP {r.status_code}")
        r = query(tok, f"SELECT * FROM `{P}.lh_truth.INFORMATION_SCHEMA.TABLES`")
        rec(f"{name}: CANNOT list lh_truth via INFORMATION_SCHEMA", denied(r), f"HTTP {r.status_code}")
        r = query(tok, f"SELECT a.note, b.note FROM `{P}.lh_raw.isolation_probe` a CROSS JOIN `{P}.lh_truth.isolation_probe` b")
        rec(f"{name}: CANNOT reach lh_truth through a join with lh_raw", denied(r), f"HTTP {r.status_code}")
        r = get(tok, "datasets/lh_truth/tables")
        rec(f"{name}: CANNOT list lh_truth tables through the API", denied(r), f"HTTP {r.status_code}")
        r = get(tok, "datasets/lh_truth/tables/isolation_probe/data")
        rec(f"{name}: CANNOT read lh_truth rows through tabledata API", denied(r), f"HTTP {r.status_code}")
        r = get(tok, "datasets/lh_truth")
        rec(f"{name}: CANNOT read lh_truth dataset metadata or its access list", denied(r), f"HTTP {r.status_code}")

    ev = token(SA["sa-truth-eval"])
    r = query(ev, f"SELECT note FROM `{P}.lh_truth.isolation_probe`")
    rec("sa-truth-eval: CAN read lh_truth (controlled evaluation identity)", r.status_code == 200 and r.json().get("rows"), f"HTTP {r.status_code}")

    pol = subprocess.run([shutil.which("gcloud"), "projects", "get-iam-policy", P, "--format=json"], capture_output=True, text=True, timeout=120)
    roles: dict[str, set] = {}
    for b in json.loads(pol.stdout).get("bindings", []):
        for m in b["members"]:
            roles.setdefault(m, set()).add(b["role"])
    for name in ("sa-app-runtime", "sa-agent-runtime"):
        got = roles.get(f"serviceAccount:{SA[name]}", set())
        rec(f"{name}: holds no project role beyond bigquery.jobUser", got == {"roles/bigquery.jobUser"}, str(sorted(got)))

    info = json.loads(subprocess.run([shutil.which("bq"), f"--project_id={P}", "show", "--format=json", f"{P}:lh_truth"], capture_output=True, text=True, timeout=120).stdout)
    listed = {a.get("userByEmail") for a in info.get("access", [])}
    rec("lh_truth access list names neither runtime identity", not (listed & {SA["sa-app-runtime"], SA["sa-agent-runtime"]}), str(sorted(x for x in listed if x)))
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} isolation checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
