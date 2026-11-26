-- Maker-checker hardening for the pre-disbursement checklist (security review of Phase 11).
--
-- Before: re-saving an already complete checklist replaced checklist_completed_by, so a person who completed it could have someone else
-- re-save it and then release the disbursement themselves; and someone who ticked most items but not the last was not excluded.
-- Now:
--  * checklist_contributors records everyone who changed the checklist; it only grows (the database merges, never drops, names);
--  * while the checklist stays complete, checklist_completed_by and checklist_completed_at keep their original values;
--  * the person releasing may not be among the contributors, the completer, or the person who recorded the acceptance.

ALTER TABLE disbursements ADD COLUMN checklist_contributors uuid[] NOT NULL DEFAULT '{}';

CREATE OR REPLACE FUNCTION disbursements_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_terms application_terms;
BEGIN
  SELECT status INTO v_status FROM applications WHERE id = NEW.application_id;
  IF TG_OP = 'INSERT' THEN
    IF v_status <> 'SANCTIONED' THEN RAISE EXCEPTION 'disbursement tracking starts only for a SANCTIONED application' USING ERRCODE = 'check_violation'; END IF;
    IF NEW.status <> 'PENDING' THEN RAISE EXCEPTION 'a new disbursement starts as PENDING' USING ERRCODE = 'check_violation'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'RELEASED' THEN RAISE EXCEPTION 'a released disbursement cannot change' USING ERRCODE = 'insufficient_privilege'; END IF;
  -- contributors only accumulate
  NEW.checklist_contributors := ARRAY(SELECT DISTINCT x FROM unnest(OLD.checklist_contributors || NEW.checklist_contributors) x WHERE x IS NOT NULL ORDER BY x);
  IF NEW.acceptance_recorded_at IS DISTINCT FROM OLD.acceptance_recorded_at AND OLD.acceptance_recorded_at IS NOT NULL THEN
    RAISE EXCEPTION 'the customer''s acceptance, once recorded, is not changed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.acceptance_recorded_at IS NOT NULL AND OLD.acceptance_recorded_at IS NULL THEN
    IF NEW.acceptance_recorded_by IS NULL OR NOT has_permission(NEW.acceptance_recorded_by, 'disbursement.track') THEN
      RAISE EXCEPTION 'customer acceptance is recorded by a human with the disbursement.track permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  IF NEW.status = 'CHECKLIST_COMPLETE' AND OLD.status <> 'CHECKLIST_COMPLETE' THEN
    IF NEW.acceptance_recorded_at IS NULL THEN RAISE EXCEPTION 'record the customer''s acceptance before completing the checklist' USING ERRCODE = 'check_violation'; END IF;
    IF EXISTS (SELECT 1 FROM unnest(ARRAY['kfs_acknowledged', 'loan_agreement_executed', 'repayment_mandate_registered', 'original_kyc_verified']) k
               WHERE coalesce((NEW.checklist ->> k)::boolean, false) IS NOT TRUE) THEN
      RAISE EXCEPTION 'every pre-disbursement checklist item must be confirmed' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.checklist_completed_by IS NULL OR NOT has_permission(NEW.checklist_completed_by, 'disbursement.track') THEN
      RAISE EXCEPTION 'the checklist is completed by a human with the disbursement.track permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.checklist_contributors := ARRAY(SELECT DISTINCT x FROM unnest(NEW.checklist_contributors || NEW.checklist_completed_by) x ORDER BY x);
    NEW.checklist_completed_at := now();
  ELSIF NEW.status = 'CHECKLIST_COMPLETE' AND OLD.status = 'CHECKLIST_COMPLETE' THEN
    NEW.checklist_completed_by := OLD.checklist_completed_by;  -- re-saving a complete checklist never changes who completed it
    NEW.checklist_completed_at := OLD.checklist_completed_at;
    IF EXISTS (SELECT 1 FROM unnest(ARRAY['kfs_acknowledged', 'loan_agreement_executed', 'repayment_mandate_registered', 'original_kyc_verified']) k
               WHERE coalesce((NEW.checklist ->> k)::boolean, false) IS NOT TRUE) THEN
      RAISE EXCEPTION 'an item was unticked: reopen the checklist (status PENDING) instead' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status = 'PENDING' AND OLD.status = 'CHECKLIST_COMPLETE' THEN
    NEW.checklist_completed_by := NULL;  -- reopened: a person must complete it again
    NEW.checklist_completed_at := NULL;
  END IF;
  IF NEW.status = 'RELEASED' THEN
    IF NEW.released_by IS NULL OR NOT has_permission(NEW.released_by, 'disbursement.release') THEN
      RAISE EXCEPTION 'release needs a human with the disbursement.release permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'CHECKLIST_COMPLETE' THEN RAISE EXCEPTION 'complete the pre-disbursement checklist before release' USING ERRCODE = 'check_violation'; END IF;
    IF v_status <> 'SANCTIONED' THEN RAISE EXCEPTION 'only a SANCTIONED application can be released' USING ERRCODE = 'check_violation'; END IF;
    IF NEW.released_by = NEW.acceptance_recorded_by OR NEW.released_by = NEW.checklist_completed_by OR NEW.released_by = ANY(NEW.checklist_contributors) THEN
      RAISE EXCEPTION 'maker-checker: the person releasing cannot be one who recorded the acceptance or worked on the checklist' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT * INTO v_terms FROM application_terms WHERE application_id = NEW.application_id AND kind = 'APPROVED' ORDER BY created_at DESC LIMIT 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'no human-approved terms to disburse against' USING ERRCODE = 'check_violation'; END IF;
    IF NEW.net_amount IS NULL OR NEW.net_amount > v_terms.amount THEN
      RAISE EXCEPTION 'the net amount credited must be recorded and cannot exceed the approved amount' USING ERRCODE = 'check_violation';
    END IF;
    NEW.terms_id := v_terms.id;
    NEW.released_by_role := staff_role(NEW.released_by);
    NEW.released_at := now();
  END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION disbursements_guard() FROM PUBLIC, anon, authenticated;
