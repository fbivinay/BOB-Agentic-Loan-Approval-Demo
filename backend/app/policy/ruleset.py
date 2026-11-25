"""Load and validate a scheme's rule parameters. A rule set that breaks any invariant here cannot be loaded, so it can never be activated.

Invariants (the database repeats the first one as a CHECK constraint):
  * no value may be labelled UNVERIFIED_CANDIDATE; only DEMO_ASSUMPTION or VERIFIED
  * all six relationship x history segments exist, every parameter has a value and a label
  * an exclusion rule can fail an application only if it is VERIFIED; otherwise it can only refer it
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

SCHEMES = Path(__file__).parent / "schemes"
SEGMENTS = ("ETB_EST", "ETB_THIN", "ETB_NTC", "NTB_EST", "NTB_THIN", "NTB_NTC")
LABELS = ("DEMO_ASSUMPTION", "VERIFIED")
REQUIRED = [
    "age.min_years", "age.max_at_maturity.salaried", "age.max_at_maturity.non_salaried", "employment.min_current_tenure_months",
    "loan.min_amount", "loan.max_amount", "loan.amount_step", "loan.max_income_multiple", "tenure.min_months", "tenure.max_months", "tenure.step_months",
    "income.min_net_monthly", "foir.max_percent", "income.min_evidence_months", "income.evidence_tolerance_percent",
    "bureau.min_score", "repayment.fail_dpd_days", "repayment.review_dpd_days", "repayment.review_late_months_24m", "conduct.review_returned_debits_6m",
    "kyc.min_name_match_percent", "pricing.score_bands", "pricing.no_score_rate_bp", "pricing.ntb_premium_bp", "pricing.min_rate_bp", "pricing.max_rate_bp",
    "fees.processing_bp", "fees.processing_min", "fees.processing_max", "fees.gst_percent", "fees.penal_interest_percent_pa", "fees.prepayment_charge_percent",
    "kfs.cooling_off_days", "exclusion.staff", "exclusion.nri", "scope.pensioner_other_scheme", "documents.required",
]
DOC_APPLIES = {"ALL", "SALARIED", "SELF_EMPLOYED", "PENSIONER"}
DOC_WAIVERS = {"ETB_KYC_CURRENT", "ETB_INCOME_AT_BOB"}


class RuleSetError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    value: object
    confidence: str
    note: str


@dataclass(frozen=True)
class RuleSet:
    content: dict

    @property
    def scheme(self) -> str:
        return self.content["scheme"]

    @property
    def version(self) -> int:
        return self.content["version"]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def identity(self) -> str:
        return f"{self.scheme} v{self.version} ({self.sha256[:12]})"

    def param(self, name: str, segment: str) -> Param:
        if segment not in SEGMENTS:
            raise KeyError(f"unknown segment {segment}")
        raw = self.content.get("segment_overrides", {}).get(segment, {}).get(name) or self.content["parameters"][name]
        return Param(name, raw["value"], raw["confidence"], raw.get("note", ""))

    def raw(self, name: str) -> dict:
        return self.content["parameters"][name]


def validate(content: dict) -> RuleSet:
    if "UNVERIFIED_CANDIDATE" in json.dumps(content):
        raise RuleSetError("a rule set may not contain UNVERIFIED_CANDIDATE values")
    if tuple(content.get("segments", ())) != SEGMENTS:
        raise RuleSetError(f"segments must be exactly {list(SEGMENTS)}")
    params = content.get("parameters")
    if not isinstance(params, dict):
        raise RuleSetError("parameters missing")
    missing = [n for n in REQUIRED if n not in params]
    if missing:
        raise RuleSetError(f"missing parameters: {missing}")
    if not isinstance(content.get("version"), int) or content["version"] < 1 or not content.get("scheme"):
        raise RuleSetError("scheme and a positive integer version are required")

    def check(name: str, p: object) -> None:
        if not isinstance(p, dict) or "value" not in p:
            raise RuleSetError(f"{name}: needs a value")
        if p.get("confidence") not in LABELS:
            raise RuleSetError(f"{name}: confidence must be one of {LABELS}, got {p.get('confidence')!r}")
        if p.get("kind") == "EXCLUSION" and p.get("on_fail") == "FAIL" and p["confidence"] != "VERIFIED":
            raise RuleSetError(f"{name}: an exclusion can fail an application only when VERIFIED")
        if p.get("kind") == "EXCLUSION" and p.get("on_fail") not in ("FAIL", "NEEDS_REVIEW"):
            raise RuleSetError(f"{name}: an exclusion must declare on_fail FAIL or NEEDS_REVIEW")

    for name, p in params.items():
        check(name, p)
    for seg, over in content.get("segment_overrides", {}).items():
        if seg not in SEGMENTS:
            raise RuleSetError(f"override for unknown segment {seg}")
        for name, p in over.items():
            if name not in params:
                raise RuleSetError(f"override of unknown parameter {name}")
            check(f"{seg}/{name}", p)
    bands = params["pricing.score_bands"]["value"]
    if [b["min_score"] for b in bands] != sorted((b["min_score"] for b in bands), reverse=True) or bands[-1]["min_score"] != 0:
        raise RuleSetError("pricing.score_bands must run from the highest min_score down to 0")
    docs = params["documents.required"]["value"]
    codes = [d.get("code") for d in docs]
    if len(set(codes)) != len(codes) or not all(codes):
        raise RuleSetError("documents.required: codes must be present and unique")
    for d in docs:
        if not d.get("label") or not set(d.get("applies") or []) <= DOC_APPLIES or not d.get("applies") or not set(d.get("waived_if") or []) <= DOC_WAIVERS:
            raise RuleSetError(f"documents.required: bad entry {d.get('code')}")
    if params["tenure.min_months"]["value"] > params["tenure.max_months"]["value"] or params["loan.min_amount"]["value"] > params["loan.max_amount"]["value"]:
        raise RuleSetError("minimum exceeds maximum")
    return RuleSet(content)


def load(scheme: str = "baroda_personal_loan") -> RuleSet:
    return validate(yaml.safe_load((SCHEMES / f"{scheme}.yaml").read_text(encoding="utf-8")))
