-- Phase 11: simulated disbursement. No real banking transaction ever happens (is_simulated is forced true by a CHECK since 0001).
--
-- Tightened:
--  * The pre-disbursement checklist is complete only when the customer's acceptance is recorded, all four items are confirmed, and a
--    person with disbursement.track completed it (recorded by name and time).
--  * Maker-checker: the person who releases cannot be the one who recorded the acceptance or completed the checklist.
--  * The release is tied to the human-approved terms (the database picks the latest APPROVED terms; the caller cannot choose) and records
--    the net amount credited, which can never exceed the approved amount.
--  * A disbursement confirmation email exists only for a released disbursement.

ALTER TABLE disbursements
  ADD COLUMN checklist_completed_by uuid REFERENCES staff_profiles(user_id),
  ADD COLUMN checklist_completed_at timestamptz,
  ADD COLUMN late_acceptance_reason text,
  ADD COLUMN terms_id uuid REFERENCES application_terms(id),
  ADD COLUMN net_amount numeric(14, 2) CHECK (net_amount IS NULL OR net_amount > 0);

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
    NEW.checklist_completed_at := now();
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
    IF NEW.released_by IN (NEW.acceptance_recorded_by, NEW.checklist_completed_by) THEN
      RAISE EXCEPTION 'maker-checker: the person releasing cannot be the one who recorded the acceptance or completed the checklist' USING ERRCODE = 'insufficient_privilege';
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

CREATE OR REPLACE FUNCTION email_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'outbox rows are kept as the record of what was sent' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF TG_OP = 'UPDATE' AND (NEW.application_id IS DISTINCT FROM OLD.application_id OR NEW.kind IS DISTINCT FROM OLD.kind
                           OR NEW.attachment_ids IS DISTINCT FROM OLD.attachment_ids OR NEW.subject IS DISTINCT FROM OLD.subject) THEN
    RAISE EXCEPTION 'an email''s application, kind, subject and attachments are fixed once queued' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.kind = 'APPROVAL' AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.application_id = NEW.application_id AND d.outcome = 'APPROVED') THEN
    RAISE EXCEPTION 'an approval email needs a human APPROVED decision' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.kind = 'DISBURSEMENT_CONFIRMATION' AND NOT EXISTS (SELECT 1 FROM disbursements x WHERE x.application_id = NEW.application_id AND x.status = 'RELEASED') THEN
    RAISE EXCEPTION 'a disbursement confirmation needs a released disbursement' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM unnest(NEW.attachment_ids) x WHERE NOT EXISTS (SELECT 1 FROM generated_documents g WHERE g.id = x AND g.application_id = NEW.application_id)) THEN
    RAISE EXCEPTION 'an email may attach only this application''s own generated documents' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION disbursements_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION email_outbox_guard() FROM PUBLIC, anon, authenticated;
