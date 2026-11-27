-- Roles, referral and four-eyes (owner request, 2026-11-27).
--
-- 1. Every role can take a customer from enquiry to a forwarded application: the Assistant Manager and Branch Manager gain
--    enquiry.manage, application.intake, document.verify and application.forward (the Loan Officer already had them).
--    Disbursement release stays with the Assistant Manager and Branch Manager only.
-- 2. Referral to the Branch Manager: an Assistant Manager reviewing a file can send it to the Branch Manager with a note
--    (for example a change of terms the customer asked for). The file goes back to FORWARDED, unassigned, marked for the Branch Manager:
--    only a Branch Manager can claim it. The Loan Officer who forwarded it stays on record as the forwarder.
-- 3. Four-eyes: whoever processed an application (completed its intake or forwarded it for review) cannot pick it up for review,
--    take it over, decide it, or release its money. Another manager must.
--
-- Demo roles (DEMO_ASSUMPTION), not Bank of Baroda's authorization structure.

INSERT INTO role_permissions (role, permission, kind, description) VALUES
 ('ASSISTANT_MANAGER', 'enquiry.manage',       'CONFIGURABLE', 'Create and manage enquiries (demo permission)'),
 ('ASSISTANT_MANAGER', 'application.intake',   'CONFIGURABLE', 'Convert an enquiry, run intake, upload documents (demo permission)'),
 ('ASSISTANT_MANAGER', 'document.verify',      'CONFIGURABLE', 'Verify documents, correct extractions, resolve discrepancies, attest (demo permission)'),
 ('ASSISTANT_MANAGER', 'application.forward',  'CONFIGURABLE', 'Forward a completed application for review (demo permission)'),
 ('ASSISTANT_MANAGER', 'application.escalate', 'CONFIGURABLE', 'Refer a file under review to the Branch Manager, with a note (demo permission)'),
 ('BRANCH_MANAGER',    'enquiry.manage',       'CONFIGURABLE', 'Create and manage enquiries (demo permission)'),
 ('BRANCH_MANAGER',    'application.intake',   'CONFIGURABLE', 'Convert an enquiry, run intake, upload documents (demo permission)'),
 ('BRANCH_MANAGER',    'document.verify',      'CONFIGURABLE', 'Verify documents, correct extractions, resolve discrepancies, attest (demo permission)'),
 ('BRANCH_MANAGER',    'application.forward',  'CONFIGURABLE', 'Forward a completed application for review (demo permission)')
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------------ referral to the Branch Manager
ALTER TABLE applications
  ADD COLUMN referred_to_role text CHECK (referred_to_role IN ('BRANCH_MANAGER')),
  ADD COLUMN referred_by      uuid REFERENCES staff_profiles(user_id),
  ADD COLUMN referred_at      timestamptz,
  ADD COLUMN referral_note    text;

INSERT INTO status_transitions (from_status, to_status, permission, actors, needs_reason) VALUES
 ('UNDER_REVIEW', 'FORWARDED', 'application.escalate', 'STAFF', true)
ON CONFLICT DO NOTHING;

-- The referral columns join the workflow columns that change only inside the workflow functions.
CREATE OR REPLACE FUNCTION applications_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('app.in_workflow_fn', true) = 'on' THEN RETURN NEW; END IF;
  IF NEW.status IS DISTINCT FROM OLD.status OR NEW.version IS DISTINCT FROM OLD.version OR NEW.assigned_to IS DISTINCT FROM OLD.assigned_to
     OR NEW.ai_recommendation IS DISTINCT FROM OLD.ai_recommendation OR NEW.ai_recommendation_run_id IS DISTINCT FROM OLD.ai_recommendation_run_id
     OR NEW.policy_rule_set_id IS DISTINCT FROM OLD.policy_rule_set_id OR NEW.forwarded_by IS DISTINCT FROM OLD.forwarded_by
     OR NEW.forwarded_at IS DISTINCT FROM OLD.forwarded_at
     OR (NEW.referred_to_role, NEW.referred_by, NEW.referred_at, NEW.referral_note)
        IS DISTINCT FROM (OLD.referred_to_role, OLD.referred_by, OLD.referred_at, OLD.referral_note) THEN
    RAISE EXCEPTION 'workflow columns can only change through app_transition, app_record_assessment, app_claim, app_take_over or app_refer_to_branch_manager'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;

-- True when this person completed the intake of, or forwarded, the application: they may not also review, decide or release it.
CREATE FUNCTION processed_by(p_app uuid, p_user uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM applications WHERE id = p_app AND p_user IN (intake_completed_by, forwarded_by))
$$;

CREATE FUNCTION app_refer_to_branch_manager(p_app uuid, p_actor uuid, p_expected_version integer, p_note text) RETURNS applications
LANGUAGE plpgsql AS $$
DECLARE a applications; v_by uuid; v_at timestamptz;
BEGIN
  SELECT * INTO a FROM applications WHERE id = p_app FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.status <> 'UNDER_REVIEW' THEN RAISE EXCEPTION 'only a file under review can be referred (status is %)', a.status USING ERRCODE = 'check_violation'; END IF;
  IF a.assigned_to IS DISTINCT FROM p_actor THEN
    RAISE EXCEPTION 'only the reviewer assigned to this file can refer it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  v_by := a.forwarded_by; v_at := a.forwarded_at;
  -- permission, version and the reason (at least 10 characters) are checked by app_transition, which also writes the audit entry
  a := app_transition(p_app, 'FORWARDED', p_expected_version, 'STAFF', p_actor, p_note);
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = NULL, forwarded_by = v_by, forwarded_at = v_at,  -- the original forwarder stays on record
         referred_to_role = 'BRANCH_MANAGER', referred_by = p_actor, referred_at = now(), referral_note = btrim(p_note)
   WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  PERFORM audit_write('STAFF', p_actor, staff_role(p_actor), 'REFERRED_TO_BRANCH_MANAGER', 'applications', p_app::text, p_app,
                      jsonb_build_object('assigned_to', p_actor), jsonb_build_object('referred_to_role', 'BRANCH_MANAGER'), btrim(p_note));
  RETURN a;
END $$;

-- Claim: four-eyes, and a file referred to the Branch Manager can be picked up only by a Branch Manager.
CREATE OR REPLACE FUNCTION app_claim(p_app uuid, p_actor uuid, p_expected_version integer) RETURNS applications LANGUAGE plpgsql AS $$
DECLARE a applications;
BEGIN
  SELECT * INTO a FROM applications WHERE id = p_app;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF processed_by(p_app, p_actor) THEN
    RAISE EXCEPTION 'four-eyes: you processed this application, so another manager must review it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF a.status = 'FORWARDED' AND a.referred_to_role IS NOT NULL AND staff_role(p_actor) IS DISTINCT FROM a.referred_to_role THEN
    RAISE EXCEPTION 'this file was referred to the Branch Manager' USING ERRCODE = 'insufficient_privilege';
  END IF;
  a := app_transition(p_app, 'UNDER_REVIEW', p_expected_version, 'STAFF', p_actor, NULL);
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = p_actor WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  RETURN a;
END $$;

CREATE OR REPLACE FUNCTION app_take_over(p_app uuid, p_actor uuid, p_expected_version integer, p_reason text) RETURNS applications
LANGUAGE plpgsql AS $$
DECLARE a applications; v_prev uuid;
BEGIN
  IF p_actor IS NULL OR NOT has_permission(p_actor, 'application.takeover') THEN
    RAISE EXCEPTION 'actor may not take over applications' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF processed_by(p_app, p_actor) THEN
    RAISE EXCEPTION 'four-eyes: you processed this application, so another manager must review it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF length(btrim(coalesce(p_reason, ''))) < 10 THEN RAISE EXCEPTION 'a reason of at least 10 characters is required' USING ERRCODE = 'check_violation'; END IF;
  SELECT * INTO a FROM applications WHERE id = p_app FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.version <> p_expected_version THEN PERFORM stale_version(); END IF;
  IF a.status NOT IN ('FORWARDED', 'UNDER_REVIEW', 'RECONSIDERATION') THEN RAISE EXCEPTION 'cannot take over an application in status %', a.status USING ERRCODE = 'check_violation'; END IF;
  v_prev := a.assigned_to;
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = p_actor, version = version + 1, updated_at = now() WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  PERFORM audit_write('STAFF', p_actor, staff_role(p_actor), 'APPLICATION_TAKEOVER', 'applications', p_app::text, p_app,
                      jsonb_build_object('assigned_to', v_prev), jsonb_build_object('assigned_to', p_actor), p_reason);
  RETURN a;
END $$;

-- Decisions: as 0009, plus four-eyes.
CREATE OR REPLACE FUNCTION decisions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  a applications;
  v_role text;
BEGIN
  v_role := staff_role(NEW.decided_by);
  IF v_role IS NULL OR v_role NOT IN ('ASSISTANT_MANAGER', 'BRANCH_MANAGER') OR NOT has_permission(NEW.decided_by, 'decision.make') THEN
    RAISE EXCEPTION 'only an active Assistant Manager or Branch Manager can record a decision' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.outcome = 'REFERRED' AND NOT has_permission(NEW.decided_by, 'application.refer') THEN
    RAISE EXCEPTION 'this role may not refer applications for reconsideration' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO a FROM applications WHERE id = NEW.application_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF NEW.decided_by IN (a.intake_completed_by, a.forwarded_by) THEN
    RAISE EXCEPTION 'four-eyes: the person who processed an application cannot decide it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF a.version <> NEW.application_version THEN PERFORM stale_version(); END IF;
  IF a.status <> 'UNDER_REVIEW' THEN RAISE EXCEPTION 'decisions are recorded only while an application is UNDER_REVIEW (status is %)', a.status USING ERRCODE = 'check_violation'; END IF;
  IF a.ai_recommendation IS NULL THEN RAISE EXCEPTION 'no AI assessment on file for this application' USING ERRCODE = 'check_violation'; END IF;
  IF a.assigned_to IS NOT NULL AND a.assigned_to <> NEW.decided_by THEN
    RAISE EXCEPTION 'application is assigned to another reviewer; a Branch Manager takes it over (with a reason) before deciding it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  NEW.decided_by_role := v_role;
  NEW.ai_recommendation_snapshot := jsonb_build_object('recommendation', a.ai_recommendation, 'run_id', a.ai_recommendation_run_id, 'application_version', a.version);
  NEW.is_override := (NEW.outcome = 'APPROVED' AND a.ai_recommendation = 'POTENTIALLY_INELIGIBLE')
                  OR (NEW.outcome = 'DECLINED' AND a.ai_recommendation = 'RECOMMENDED_FOR_APPROVAL');
  IF (NEW.outcome IN ('DECLINED', 'REFERRED') OR NEW.is_override) AND length(btrim(coalesce(NEW.reason, ''))) < 10 THEN
    RAISE EXCEPTION 'a written reason of at least 10 characters is required for a decline, a referral or an override of the AI recommendation' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- Disbursement: as 0012, plus four-eyes on release.
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
    NEW.checklist_completed_by := OLD.checklist_completed_by;
    NEW.checklist_completed_at := OLD.checklist_completed_at;
    IF EXISTS (SELECT 1 FROM unnest(ARRAY['kfs_acknowledged', 'loan_agreement_executed', 'repayment_mandate_registered', 'original_kyc_verified']) k
               WHERE coalesce((NEW.checklist ->> k)::boolean, false) IS NOT TRUE) THEN
      RAISE EXCEPTION 'an item was unticked: reopen the checklist (status PENDING) instead' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status = 'PENDING' AND OLD.status = 'CHECKLIST_COMPLETE' THEN
    NEW.checklist_completed_by := NULL;
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
    IF processed_by(NEW.application_id, NEW.released_by) THEN
      RAISE EXCEPTION 'four-eyes: the person who processed an application cannot release its disbursement' USING ERRCODE = 'insufficient_privilege';
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

REVOKE ALL ON FUNCTION processed_by(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_refer_to_branch_manager(uuid, uuid, integer, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION decisions_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION disbursements_guard() FROM PUBLIC, anon, authenticated;
