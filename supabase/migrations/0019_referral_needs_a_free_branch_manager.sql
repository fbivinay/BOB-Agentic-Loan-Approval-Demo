-- Referral and four-eyes together (found by the demo backfill): a file a Branch Manager processed could be referred to the Branch Manager,
-- and then nobody could pick it up: the Assistant Manager because it was referred to a Branch Manager, the Branch Manager because of
-- four-eyes. Now:
--  * a file can be referred to the Branch Manager only when an active Branch Manager who did not process it exists;
--  * a referred file whose Branch Managers all processed it can be picked up by an Assistant Manager (who did not process it either).

CREATE FUNCTION free_branch_manager_for(p_app uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM staff_profiles s WHERE s.role = 'BRANCH_MANAGER' AND s.is_active AND NOT processed_by(p_app, s.user_id))
$$;

CREATE OR REPLACE FUNCTION app_refer_to_branch_manager(p_app uuid, p_actor uuid, p_expected_version integer, p_note text) RETURNS applications
LANGUAGE plpgsql AS $$
DECLARE a applications; v_by uuid; v_at timestamptz;
BEGIN
  SELECT * INTO a FROM applications WHERE id = p_app FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF a.status <> 'UNDER_REVIEW' THEN RAISE EXCEPTION 'only a file under review can be referred (status is %)', a.status USING ERRCODE = 'check_violation'; END IF;
  IF a.assigned_to IS DISTINCT FROM p_actor THEN
    RAISE EXCEPTION 'only the reviewer assigned to this file can refer it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT free_branch_manager_for(p_app) THEN
    RAISE EXCEPTION 'four-eyes: the Branch Manager processed this file, so it cannot be referred to them; decide it or ask another manager'
      USING ERRCODE = 'check_violation';
  END IF;
  v_by := a.forwarded_by; v_at := a.forwarded_at;
  a := app_transition(p_app, 'FORWARDED', p_expected_version, 'STAFF', p_actor, p_note);
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = NULL, forwarded_by = v_by, forwarded_at = v_at,
         referred_to_role = 'BRANCH_MANAGER', referred_by = p_actor, referred_at = now(), referral_note = btrim(p_note)
   WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  PERFORM audit_write('STAFF', p_actor, staff_role(p_actor), 'REFERRED_TO_BRANCH_MANAGER', 'applications', p_app::text, p_app,
                      jsonb_build_object('assigned_to', p_actor), jsonb_build_object('referred_to_role', 'BRANCH_MANAGER'), btrim(p_note));
  RETURN a;
END $$;

CREATE OR REPLACE FUNCTION app_claim(p_app uuid, p_actor uuid, p_expected_version integer) RETURNS applications LANGUAGE plpgsql AS $$
DECLARE a applications;
BEGIN
  SELECT * INTO a FROM applications WHERE id = p_app;
  IF NOT FOUND THEN RAISE EXCEPTION 'application not found'; END IF;
  IF processed_by(p_app, p_actor) THEN
    RAISE EXCEPTION 'four-eyes: you processed this application, so another manager must review it' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF a.status = 'FORWARDED' AND a.referred_to_role IS NOT NULL AND staff_role(p_actor) IS DISTINCT FROM a.referred_to_role
     AND free_branch_manager_for(p_app) THEN
    RAISE EXCEPTION 'this file was referred to the Branch Manager' USING ERRCODE = 'insufficient_privilege';
  END IF;
  a := app_transition(p_app, 'UNDER_REVIEW', p_expected_version, 'STAFF', p_actor, NULL);
  PERFORM set_config('app.in_workflow_fn', 'on', true);
  UPDATE applications SET assigned_to = p_actor WHERE id = p_app RETURNING * INTO a;
  PERFORM set_config('app.in_workflow_fn', 'off', true);
  RETURN a;
END $$;

REVOKE ALL ON FUNCTION free_branch_manager_for(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_refer_to_branch_manager(uuid, uuid, integer, text) FROM PUBLIC;
