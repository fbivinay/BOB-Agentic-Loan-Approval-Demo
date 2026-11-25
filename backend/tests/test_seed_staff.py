"""The seeding script must never hand a staff role to an account it did not create."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import seed_staff as s  # noqa: E402

E = "branch.manager@demo.example.com"


def test_a_new_email_is_created():
    assert s.decide({}, E) == "create"


def test_an_account_this_script_created_is_adopted():
    assert s.decide({E: {"id": "1", "app_metadata": {"seeded_by": "seed_staff"}}}, E) == "adopt"


def test_a_pre_registered_account_is_refused():
    for meta in (None, {}, {"provider": "email"}, {"seeded_by": "someone-else"}):
        assert s.decide({E: {"id": "1", "app_metadata": meta}}, E) == "refuse"


def test_all_staff_emails_are_demo_addresses_with_the_three_roles():
    assert {r for _, _, r in s.STAFF} == {"LOAN_OFFICER", "ASSISTANT_MANAGER", "BRANCH_MANAGER"}
    assert all(e.endswith("@demo.example.com") for e, _, _ in s.STAFF)
