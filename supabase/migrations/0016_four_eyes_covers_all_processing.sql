-- Four-eyes, widened (security review of 0015). "Processed" now means anyone who did any of the checking work on the application,
-- not only the person who completed its intake or forwarded it:
--   * completed the intake or forwarded it (as in 0015);
--   * uploaded one of its documents, corrected an extraction on one, or resolved one of its discrepancies;
--   * moved it through a processing step (intake -> documents, documents verified/attested, discrepancy, forward), read from the
--     append-only audit trail, so the attester is covered even though the application row does not name them.
-- A referral by the Assistant Manager (UNDER_REVIEW -> FORWARDED) is review work, not processing, and does not count.
-- app_claim, app_take_over, decisions_guard and disbursements_guard all call processed_by(), so this one change applies everywhere.

CREATE OR REPLACE FUNCTION processed_by(p_app uuid, p_user uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT p_user IS NOT NULL AND (
       EXISTS (SELECT 1 FROM applications WHERE id = p_app AND p_user IN (intake_completed_by, forwarded_by))
    OR EXISTS (SELECT 1 FROM documents WHERE application_id = p_app AND uploaded_by = p_user)
    OR EXISTS (SELECT 1 FROM extraction_corrections c JOIN documents d ON d.id = c.document_id WHERE d.application_id = p_app AND c.corrected_by = p_user)
    OR EXISTS (SELECT 1 FROM discrepancies WHERE application_id = p_app AND resolved_by = p_user)
    OR EXISTS (SELECT 1 FROM audit_events WHERE application_id = p_app AND event_type = 'STATUS_TRANSITION' AND actor_type = 'STAFF' AND actor_id = p_user
               AND before_state ->> 'status' IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY', 'AI_ASSESSED')))
$$;

-- decisions_guard compared columns directly in 0015; use the shared definition instead.
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
  IF processed_by(NEW.application_id, NEW.decided_by) THEN
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

REVOKE ALL ON FUNCTION processed_by(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION decisions_guard() FROM PUBLIC, anon, authenticated;
