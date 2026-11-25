-- Human-only guards. These run in the database, so they hold even if application code is wrong:
--  * decisions: only an active Assistant Manager or Branch Manager (permission decision.make); never an agent or the system
--  * application status: only through app_transition(), which checks permission, version and preconditions
--  * approved terms and term changes: human only, inside demo limits, with a recorded reason
--  * policy rule sets: become ACTIVE only with an authorised approver, a passing test result and a reason
--  * disbursement release and sanction documents: need a human decision and a human release

CREATE FUNCTION has_permission(p_user uuid, p_perm text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM staff_profiles s JOIN role_permissions rp ON rp.role = s.role
                 WHERE s.user_id = p_user AND s.is_active AND rp.permission = p_perm)
$$;

CREATE FUNCTION staff_role(p_user uuid) RETURNS text LANGUAGE sql STABLE AS $$
  SELECT role FROM staff_profiles WHERE user_id = p_user AND is_active
$$;

CREATE FUNCTION demo_limit(p_key text) RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT value FROM demo_config WHERE key = p_key
$$;

CREATE FUNCTION stale_version() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'application changed since it was read (stale version)' USING ERRCODE = 'serialization_failure';
END $$;

-- ------------------------------------------------------------------ applications: direct edits of workflow columns are rejected
CREATE FUNCTION applications_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('app.in_workflow_fn', true) = 'on' THEN RETURN NEW; END IF;
  IF NEW.status IS DISTINCT FROM OLD.status OR NEW.version IS DISTINCT FROM OLD.version OR NEW.assigned_to IS DISTINCT FROM OLD.assigned_to
     OR NEW.ai_recommendation IS DISTINCT FROM OLD.ai_recommendation OR NEW.ai_recommendation_run_id IS DISTINCT FROM OLD.ai_recommendation_run_id
     OR NEW.policy_rule_set_id IS DISTINCT FROM OLD.policy_rule_set_id OR NEW.forwarded_by IS DISTINCT FROM OLD.forwarded_by THEN
    RAISE EXCEPTION 'workflow columns can only change through app_transition, app_record_assessment, app_claim or app_take_over'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER applications_guard BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION applications_guard();
CREATE TRIGGER applications_no_delete BEFORE DELETE ON applications FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

CREATE FUNCTION app_transition(p_app uuid, p_to text, p_expected_version integer, p_actor_type text, p_actor uuid, p_reason text DEFAULT NULL)
RETURNS applications LANGUAGE plpgsql AS $$
DECLARE
  a applications;
  t status_transitions;
  v_role text;
BEGIN
  SELECT * INTO a FROM applications WHERE id = p_app FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.version <> p_expected_version THEN PERFORM stale_version(); END IF;
  SELECT * INTO t FROM status_transitions WHERE from_status = a.status AND to_status = p_to;
  IF NOT FOUND THEN RAISE EXCEPTION 'transition % -> % is not allowed', a.status, p_to USING ERRCODE = 'check_violation'; END IF;

  IF p_actor_type = 'STAFF' THEN
    IF t.actors = 'SYSTEM' THEN RAISE EXCEPTION 'transition % -> % is system-only', a.status, p_to USING ERRCODE = 'insufficient_privilege'; END IF;
    IF p_actor IS NULL OR NOT has_permission(p_actor, t.permission) THEN
      RAISE EXCEPTION 'actor lacks permission % for % -> %', t.permission, a.status, p_to USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_role := staff_role(p_actor);
  ELSIF p_actor_type = 'SYSTEM' THEN
    IF t.actors = 'STAFF' THEN RAISE EXCEPTION 'transition % -> % needs a human actor', a.status, p_to USING ERRCODE = 'insufficient_privilege'; END IF;
    IF p_actor IS NOT NULL THEN RAISE EXCEPTION 'system actor must not carry a user id'; END IF;
  ELSE
    RAISE EXCEPTION 'unknown actor type %', p_actor_type;
  END IF;
  IF t.needs_reason AND length(btrim(coalesce(p_reason, ''))) < 10 THEN
    RAISE EXCEPTION 'a reason of at least 10 characters is required for % -> %', a.status, p_to USING ERRCODE = 'check_violation';
  END IF;

  -- preconditions
  IF p_to IN ('APPROVED', 'DECLINED', 'RECONSIDERATION') THEN
    IF NOT EXISTS (SELECT 1 FROM decisions d WHERE d.application_id = p_app AND d.application_version = p_expected_version
                   AND d.outcome = CASE p_to WHEN 'APPROVED' THEN 'APPROVED' WHEN 'DECLINED' THEN 'DECLINED' ELSE 'REFERRED' END) THEN
      RAISE EXCEPTION 'no matching human decision recorded for % -> %', a.status, p_to USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF p_to = 'SANCTIONED' THEN
    IF NOT EXISTS (SELECT 1 FROM decisions d WHERE d.application_id = p_app AND d.outcome = 'APPROVED') THEN
      RAISE EXCEPTION 'sanction requires a human APPROVED decision' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (SELECT count(DISTINCT kind) FROM generated_documents WHERE application_id = p_app) <> 3 THEN
      RAISE EXCEPTION 'sanction requires the sanction letter, KFS and EMI schedule' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF p_to = 'DISBURSED' THEN
    IF NOT EXISTS (SELECT 1 FROM disbursements WHERE application_id = p_app AND status = 'RELEASED' AND released_by IS NOT NULL) THEN
      RAISE EXCEPTION 'disbursement requires a human release' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF p_to = 'AI_ASSESSED' THEN
    IF a.ai_recommendation IS NULL THEN RAISE EXCEPTION 'record the assessment with app_record_assessment' USING ERRCODE = 'check_violation'; END IF;
  END IF;

  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET status = p_to, version = version + 1, updated_at = now(),
         forwarded_by = CASE WHEN p_to = 'FORWARDED' THEN p_actor ELSE forwarded_by END,
         forwarded_at = CASE WHEN p_to = 'FORWARDED' THEN now() ELSE forwarded_at END
   WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  PERFORM audit_write(p_actor_type, p_actor, v_role, 'STATUS_TRANSITION', 'applications', p_app::text, p_app,
                      jsonb_build_object('status', t.from_status, 'version', p_expected_version), jsonb_build_object('status', p_to, 'version', a.version), p_reason);
  RETURN a;
END $$;

-- The assessment graph records its recommendation (a recommendation only, never a decision). System actor only.
CREATE FUNCTION app_record_assessment(p_app uuid, p_run uuid, p_recommendation text, p_rule_set uuid, p_expected_version integer)
RETURNS applications LANGUAGE plpgsql AS $$
DECLARE a applications;
BEGIN
  IF p_recommendation NOT IN ('RECOMMENDED_FOR_APPROVAL', 'REFER_FOR_FURTHER_REVIEW', 'POTENTIALLY_INELIGIBLE') THEN
    RAISE EXCEPTION 'invalid recommendation %', p_recommendation USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO a FROM applications WHERE id = p_app FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.version <> p_expected_version THEN PERFORM stale_version(); END IF;
  IF a.status <> 'DOCS_VERIFIED' THEN RAISE EXCEPTION 'assessment needs verified documents (status is %)', a.status USING ERRCODE = 'check_violation'; END IF;
  IF NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.id = p_run AND r.application_id = p_app AND r.graph = 'ASSESSMENT' AND r.status = 'DONE' AND r.recommendation = p_recommendation) THEN
    RAISE EXCEPTION 'recommendation must match a finished assessment run for this application' USING ERRCODE = 'check_violation';
  END IF;
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET ai_recommendation = p_recommendation, ai_recommendation_run_id = p_run, policy_rule_set_id = p_rule_set,
         assessed_at = now(), updated_at = now() WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  RETURN app_transition(p_app, 'AI_ASSESSED', p_expected_version, 'SYSTEM', NULL, NULL);
END $$;

-- Branch Manager only, by permission application.takeover.
CREATE FUNCTION app_take_over(p_app uuid, p_actor uuid, p_expected_version integer, p_reason text) RETURNS applications
LANGUAGE plpgsql AS $$
DECLARE a applications; v_prev uuid;
BEGIN
  IF p_actor IS NULL OR NOT has_permission(p_actor, 'application.takeover') THEN
    RAISE EXCEPTION 'actor may not take over applications' USING ERRCODE = 'insufficient_privilege';
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

-- A reviewer picks an application up: FORWARDED (or RECONSIDERATION) -> UNDER_REVIEW and it is assigned to them.
CREATE FUNCTION app_claim(p_app uuid, p_actor uuid, p_expected_version integer) RETURNS applications LANGUAGE plpgsql AS $$
DECLARE a applications;
BEGIN
  a := app_transition(p_app, 'UNDER_REVIEW', p_expected_version, 'STAFF', p_actor, NULL);
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = p_actor WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  RETURN a;
END $$;

-- ------------------------------------------------------------------ decisions
CREATE FUNCTION decisions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
  IF a.assigned_to IS NOT NULL AND a.assigned_to <> NEW.decided_by AND NOT has_permission(NEW.decided_by, 'application.takeover') THEN
    RAISE EXCEPTION 'application is assigned to another reviewer; only a role with takeover rights may decide it' USING ERRCODE = 'insufficient_privilege';
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
CREATE TRIGGER decisions_guard BEFORE INSERT ON decisions FOR EACH ROW EXECUTE FUNCTION decisions_guard();

CREATE FUNCTION decisions_after() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_to text := CASE NEW.outcome WHEN 'APPROVED' THEN 'APPROVED' WHEN 'DECLINED' THEN 'DECLINED' ELSE 'RECONSIDERATION' END;
BEGIN
  PERFORM audit_write('STAFF', NEW.decided_by, NEW.decided_by_role, 'DECISION_RECORDED', 'decisions', NEW.id::text, NEW.application_id, NULL,
                      jsonb_build_object('outcome', NEW.outcome, 'ai', NEW.ai_recommendation_snapshot, 'is_override', NEW.is_override), NEW.reason);
  IF NEW.is_override THEN
    PERFORM audit_write('STAFF', NEW.decided_by, NEW.decided_by_role, 'DECISION_OVERRIDE', 'decisions', NEW.id::text, NEW.application_id,
                        NEW.ai_recommendation_snapshot, jsonb_build_object('outcome', NEW.outcome), NEW.reason);
  END IF;
  INSERT INTO analytics_outbox (application_id, event_type, payload)
  VALUES (NEW.application_id, 'DECISION', jsonb_build_object('decision_id', NEW.id, 'outcome', NEW.outcome, 'decided_by_role', NEW.decided_by_role,
          'ai_recommendation', NEW.ai_recommendation_snapshot ->> 'recommendation', 'is_override', NEW.is_override, 'decided_at', NEW.decided_at));
  PERFORM app_transition(NEW.application_id, v_to, NEW.application_version, 'STAFF', NEW.decided_by, NEW.reason);
  RETURN NULL;
END $$;
CREATE TRIGGER decisions_after AFTER INSERT ON decisions FOR EACH ROW EXECUTE FUNCTION decisions_after();

-- ------------------------------------------------------------------ terms
CREATE FUNCTION terms_within_limits(p_app uuid, p_amount numeric, p_rate integer, p_tenure integer) RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE v_cap numeric;
BEGIN
  SELECT amount INTO v_cap FROM application_terms WHERE application_id = p_app AND kind = 'ELIGIBLE' ORDER BY seq DESC LIMIT 1;
  IF v_cap IS NOT NULL AND p_amount > v_cap * demo_limit('terms.max_pct_of_eligible') / 100 THEN RETURN 'amount exceeds the eligible cap'; END IF;
  IF p_rate < demo_limit('terms.rate_min_bp') OR p_rate > demo_limit('terms.rate_max_bp') THEN RETURN 'rate is outside the configured band'; END IF;
  IF p_tenure < demo_limit('terms.tenure_min_months') OR p_tenure > demo_limit('terms.tenure_max_months') THEN RETURN 'tenure is outside the configured range'; END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION application_terms_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_problem text;
BEGIN
  IF NEW.kind = 'ELIGIBLE' THEN
    IF NEW.policy_rule_set_id IS NULL THEN RAISE EXCEPTION 'eligible terms must cite the policy rule set they were computed under' USING ERRCODE = 'check_violation'; END IF;
  ELSIF NEW.kind = 'REQUESTED' THEN
    IF NEW.created_by IS NULL OR NOT has_permission(NEW.created_by, 'application.intake') THEN
      RAISE EXCEPTION 'requested terms are entered by a Loan Officer' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSE  -- APPROVED: human only, inside demo limits, tied to a human decision or a recorded term change
    IF NEW.created_by IS NULL OR NOT has_permission(NEW.created_by, 'decision.make') THEN
      RAISE EXCEPTION 'approved terms can only be set by a human reviewer' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT (EXISTS (SELECT 1 FROM decisions d WHERE d.id = NEW.decision_id AND d.application_id = NEW.application_id AND d.outcome = 'APPROVED' AND d.decided_by = NEW.created_by)
         OR EXISTS (SELECT 1 FROM term_changes c WHERE c.id = NEW.term_change_id AND c.application_id = NEW.application_id AND c.changed_by = NEW.created_by)) THEN
      RAISE EXCEPTION 'approved terms must reference the approving decision or a recorded term change' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_problem := terms_within_limits(NEW.application_id, NEW.amount, NEW.rate_bp, NEW.tenure_months);
    IF v_problem IS NOT NULL THEN RAISE EXCEPTION 'approved terms rejected: %', v_problem USING ERRCODE = 'check_violation'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER application_terms_guard BEFORE INSERT ON application_terms FOR EACH ROW EXECUTE FUNCTION application_terms_guard();

CREATE FUNCTION term_changes_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a applications; v_problem text;
BEGIN
  IF NOT has_permission(NEW.changed_by, 'terms.adjust') THEN
    RAISE EXCEPTION 'this role may not adjust loan terms' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF length(btrim(coalesce(NEW.reason, ''))) < 10 THEN
    RAISE EXCEPTION 'a reason of at least 10 characters is required to adjust terms' USING ERRCODE = 'check_violation';
  END IF;
  SELECT * INTO a FROM applications WHERE id = NEW.application_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.version <> NEW.application_version THEN PERFORM stale_version(); END IF;
  IF a.status NOT IN ('UNDER_REVIEW', 'RECONSIDERATION') THEN RAISE EXCEPTION 'terms can only be adjusted during review' USING ERRCODE = 'check_violation'; END IF;
  v_problem := terms_within_limits(NEW.application_id, (NEW.after_terms ->> 'amount')::numeric, (NEW.after_terms ->> 'rate_bp')::integer, (NEW.after_terms ->> 'tenure_months')::integer);
  IF v_problem IS NOT NULL THEN RAISE EXCEPTION 'term change rejected: %', v_problem USING ERRCODE = 'check_violation'; END IF;
  NEW.changed_by_role := staff_role(NEW.changed_by);
  RETURN NEW;
END $$;
CREATE TRIGGER term_changes_guard BEFORE INSERT ON term_changes FOR EACH ROW EXECUTE FUNCTION term_changes_guard();

CREATE FUNCTION term_changes_after() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_write('STAFF', NEW.changed_by, NEW.changed_by_role, 'TERM_CHANGE', 'term_changes', NEW.id::text, NEW.application_id, NEW.before_terms, NEW.after_terms, NEW.reason);
  RETURN NULL;
END $$;
CREATE TRIGGER term_changes_after AFTER INSERT ON term_changes FOR EACH ROW EXECUTE FUNCTION term_changes_after();

-- ------------------------------------------------------------------ policy rule sets
CREATE FUNCTION policy_rule_sets_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'DRAFT' THEN RAISE EXCEPTION 'rule sets are created as DRAFT; activation is a separate human step' USING ERRCODE = 'insufficient_privilege'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status <> 'DRAFT' AND (NEW.content IS DISTINCT FROM OLD.content OR NEW.scheme IS DISTINCT FROM OLD.scheme OR NEW.version IS DISTINCT FROM OLD.version
                                OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256) THEN
    RAISE EXCEPTION 'a rule set that has been activated is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'ACTIVE' AND OLD.status = 'DRAFT' THEN
    IF NEW.approved_by IS NULL OR NOT has_permission(NEW.approved_by, 'policy.activate') THEN
      RAISE EXCEPTION 'activating a rule set needs a human with the policy.activate permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF length(btrim(coalesce(NEW.activation_reason, ''))) < 10 THEN
      RAISE EXCEPTION 'a reason of at least 10 characters is required to activate a rule set' USING ERRCODE = 'check_violation';
    END IF;
    IF coalesce(NEW.test_result ->> 'passed', 'false') <> 'true' THEN
      RAISE EXCEPTION 'the golden-case tests must pass before a rule set can be activated' USING ERRCODE = 'check_violation';
    END IF;
    NEW.approved_at := now();
    NEW.effective_from := coalesce(NEW.effective_from, now());
    UPDATE policy_rule_sets SET status = 'RETIRED' WHERE scheme = NEW.scheme AND status = 'ACTIVE' AND id <> NEW.id;
  ELSIF NEW.status IS DISTINCT FROM OLD.status AND NOT (OLD.status = 'ACTIVE' AND NEW.status = 'RETIRED') AND NOT (OLD.status = 'DRAFT' AND NEW.status = 'RETIRED') THEN
    RAISE EXCEPTION 'rule set status cannot go % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER policy_rule_sets_guard BEFORE INSERT OR UPDATE ON policy_rule_sets FOR EACH ROW EXECUTE FUNCTION policy_rule_sets_guard();
CREATE TRIGGER policy_rule_sets_no_delete BEFORE DELETE ON policy_rule_sets FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

CREATE FUNCTION policy_rule_sets_after() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'ACTIVE' AND OLD.status = 'DRAFT' THEN
    INSERT INTO policy_activation_log (rule_set_id, scheme, version, activated_by, activated_by_role, reason, test_result)
    VALUES (NEW.id, NEW.scheme, NEW.version, NEW.approved_by, staff_role(NEW.approved_by), NEW.activation_reason, NEW.test_result);
    PERFORM audit_write('STAFF', NEW.approved_by, staff_role(NEW.approved_by), 'POLICY_ACTIVATED', 'policy_rule_sets', NEW.id::text, NULL,
                        jsonb_build_object('status', 'DRAFT'), jsonb_build_object('status', 'ACTIVE', 'scheme', NEW.scheme, 'version', NEW.version), NEW.activation_reason);
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER policy_rule_sets_after AFTER UPDATE ON policy_rule_sets FOR EACH ROW EXECUTE FUNCTION policy_rule_sets_after();

-- ------------------------------------------------------------------ sanction pack and disbursement
CREATE FUNCTION generated_documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM decisions d WHERE d.application_id = NEW.application_id AND d.outcome = 'APPROVED') THEN
    RAISE EXCEPTION 'sanction documents require a human APPROVED decision' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM application_terms t WHERE t.id = NEW.terms_id AND t.application_id = NEW.application_id AND t.kind = 'APPROVED') THEN
    RAISE EXCEPTION 'sanction documents must be generated from the human-approved terms' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER generated_documents_guard BEFORE INSERT ON generated_documents FOR EACH ROW EXECUTE FUNCTION generated_documents_guard();

CREATE FUNCTION disbursements_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM applications WHERE id = NEW.application_id;
  IF TG_OP = 'INSERT' THEN
    IF v_status <> 'SANCTIONED' THEN RAISE EXCEPTION 'disbursement tracking starts only for a SANCTIONED application' USING ERRCODE = 'check_violation'; END IF;
    IF NEW.status <> 'PENDING' THEN RAISE EXCEPTION 'a new disbursement starts as PENDING' USING ERRCODE = 'check_violation'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status = 'RELEASED' THEN RAISE EXCEPTION 'a released disbursement cannot change' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF NEW.acceptance_recorded_at IS NOT NULL AND OLD.acceptance_recorded_at IS NULL THEN
    IF NEW.acceptance_recorded_by IS NULL OR NOT has_permission(NEW.acceptance_recorded_by, 'disbursement.track') THEN
      RAISE EXCEPTION 'customer acceptance is recorded by a human with the disbursement.track permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  IF NEW.status = 'RELEASED' THEN
    IF NEW.released_by IS NULL OR NOT has_permission(NEW.released_by, 'disbursement.release') THEN
      RAISE EXCEPTION 'release needs a human with the disbursement.release permission' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'CHECKLIST_COMPLETE' THEN RAISE EXCEPTION 'complete the pre-disbursement checklist before release' USING ERRCODE = 'check_violation'; END IF;
    IF v_status <> 'SANCTIONED' THEN RAISE EXCEPTION 'only a SANCTIONED application can be released' USING ERRCODE = 'check_violation'; END IF;
    NEW.released_by_role := staff_role(NEW.released_by);
    NEW.released_at := now();
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER disbursements_guard BEFORE INSERT OR UPDATE ON disbursements FOR EACH ROW EXECUTE FUNCTION disbursements_guard();

-- ------------------------------------------------------------------ human work records
CREATE FUNCTION human_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor uuid; v_perm text := TG_ARGV[0];
BEGIN
  -- the acting person's column differs per table (TG_ARGV[1]); read it generically
  v_actor := nullif(to_jsonb(NEW) ->> TG_ARGV[1], '')::uuid;
  IF TG_TABLE_NAME = 'discrepancies' AND (to_jsonb(NEW) ->> 'status') = 'OPEN' THEN RETURN NEW; END IF;
  IF v_actor IS NULL OR NOT has_permission(v_actor, v_perm) THEN
    RAISE EXCEPTION '% needs a human with permission %', TG_TABLE_NAME, v_perm USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER documents_human BEFORE INSERT ON documents FOR EACH ROW EXECUTE FUNCTION human_record_guard('application.intake', 'uploaded_by');
CREATE TRIGGER discrepancies_human BEFORE INSERT OR UPDATE ON discrepancies FOR EACH ROW EXECUTE FUNCTION human_record_guard('document.verify', 'resolved_by');
CREATE TRIGGER corrections_human BEFORE INSERT ON extraction_corrections FOR EACH ROW EXECUTE FUNCTION human_record_guard('document.verify', 'corrected_by');
