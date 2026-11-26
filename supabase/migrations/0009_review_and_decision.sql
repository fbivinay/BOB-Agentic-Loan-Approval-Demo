-- Phase 9: review and decision. Two guards tightened:
--
-- 1. A reviewer decides only an application assigned to them. A Branch Manager who wants another reviewer's file takes it over first
--    (app_take_over: a recorded reason and an APPLICATION_TAKEOVER audit event); deciding someone else's file directly is refused.
-- 2. Term limits always have a cap. Without eligible terms (the engine found no amount that fits), the cap is the amount the applicant
--    requested, so an adjusted or approved amount can never exceed what was asked for. Before, a missing eligible amount meant no cap.

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
  IF a.version <> NEW.application_version THEN PERFORM stale_version(); END IF;
  IF a.status <> 'UNDER_REVIEW' THEN RAISE EXCEPTION 'decisions are recorded only while an application is UNDER_REVIEW (status is %)', a.status USING ERRCODE = 'check_violation'; END IF;
  IF a.ai_recommendation IS NULL THEN RAISE EXCEPTION 'no AI assessment on file for this application' USING ERRCODE = 'check_violation'; END IF;
  IF a.assigned_to IS NOT NULL AND a.assigned_to <> NEW.decided_by THEN
    RAISE EXCEPTION 'application is assigned to another reviewer; a Branch Manager takes it over (with a reason) before deciding it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- the snapshot and the override flag are computed here, never trusted from the caller
  NEW.decided_by_role := v_role;
  NEW.ai_recommendation_snapshot := jsonb_build_object('recommendation', a.ai_recommendation, 'run_id', a.ai_recommendation_run_id, 'application_version', a.version);
  NEW.is_override := (NEW.outcome = 'APPROVED' AND a.ai_recommendation = 'POTENTIALLY_INELIGIBLE')
                  OR (NEW.outcome = 'DECLINED' AND a.ai_recommendation = 'RECOMMENDED_FOR_APPROVAL');
  IF (NEW.outcome IN ('DECLINED', 'REFERRED') OR NEW.is_override) AND length(btrim(coalesce(NEW.reason, ''))) < 10 THEN
    RAISE EXCEPTION 'a written reason of at least 10 characters is required for a decline, a referral or an override of the AI recommendation' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION terms_within_limits(p_app uuid, p_amount numeric, p_rate integer, p_tenure integer) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v_cap numeric; v_basis text := 'eligible';
BEGIN
  SELECT amount INTO v_cap FROM application_terms WHERE application_id = p_app AND kind = 'ELIGIBLE' ORDER BY seq DESC LIMIT 1;
  IF v_cap IS NULL THEN
    SELECT requested_amount INTO v_cap FROM applications WHERE id = p_app;
    v_basis := 'requested';
  END IF;
  IF p_amount IS NULL OR p_rate IS NULL OR p_tenure IS NULL THEN RETURN 'amount, rate and tenure are all required'; END IF;
  IF p_amount <= 0 THEN RETURN 'amount must be positive'; END IF;
  IF p_amount > v_cap * demo_limit('terms.max_pct_of_eligible') / 100 THEN RETURN format('amount exceeds the %s cap', v_basis); END IF;
  IF p_rate < demo_limit('terms.rate_min_bp') OR p_rate > demo_limit('terms.rate_max_bp') THEN RETURN 'rate is outside the configured band'; END IF;
  IF p_tenure < demo_limit('terms.tenure_min_months') OR p_tenure > demo_limit('terms.tenure_max_months') THEN RETURN 'tenure is outside the configured range'; END IF;
  RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION decisions_guard() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION terms_within_limits(uuid, numeric, integer, integer) FROM PUBLIC, anon, authenticated;
