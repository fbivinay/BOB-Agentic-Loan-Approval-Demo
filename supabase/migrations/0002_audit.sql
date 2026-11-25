-- Append-only audit trail. UPDATE, DELETE and TRUNCATE are rejected by triggers and revoked from every non-owner role.
-- Limit: the database owner can still alter or drop the trigger; a hash chain for tamper evidence is not implemented.

CREATE FUNCTION audit_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % rejected', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER policy_activation_log_no_update BEFORE UPDATE OR DELETE ON policy_activation_log FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER policy_activation_log_no_truncate BEFORE TRUNCATE ON policy_activation_log FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER decisions_immutable BEFORE UPDATE OR DELETE ON decisions FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER decisions_no_truncate BEFORE TRUNCATE ON decisions FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER term_changes_immutable BEFORE UPDATE OR DELETE ON term_changes FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER term_changes_no_truncate BEFORE TRUNCATE ON term_changes FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER application_terms_immutable BEFORE UPDATE OR DELETE ON application_terms FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER application_terms_no_truncate BEFORE TRUNCATE ON application_terms FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();

CREATE FUNCTION audit_write(p_actor_type text, p_actor uuid, p_actor_role text, p_event text, p_entity_type text, p_entity_id text,
                            p_app uuid, p_before jsonb, p_after jsonb, p_reason text, p_meta jsonb DEFAULT '{}') RETURNS bigint
LANGUAGE sql AS $$
  INSERT INTO audit_events (actor_type, actor_id, actor_role, event_type, entity_type, entity_id, application_id, before_state, after_state, reason, metadata)
  VALUES (p_actor_type, p_actor, p_actor_role, p_event, p_entity_type, p_entity_id, p_app, p_before, p_after, p_reason, p_meta)
  RETURNING id
$$;

-- Generic row audit. The actor comes from per-transaction settings the backend sets (app.actor_type, app.actor_id, app.actor_role).
-- Human-decision triggers in 0003 do NOT rely on these settings: they use explicit, validated columns.
CREATE FUNCTION audit_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_type text := coalesce(nullif(current_setting('app.actor_type', true), ''), 'SYSTEM');
  v_actor uuid := nullif(current_setting('app.actor_id', true), '')::uuid;
  v_role text := nullif(current_setting('app.actor_role', true), '');
  v_app uuid;
  v_new jsonb := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
  v_old jsonb := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
  v_row jsonb := coalesce(v_new, v_old);
BEGIN
  IF v_type = 'STAFF' AND (v_actor IS NULL OR v_role IS NULL) THEN v_type := 'SYSTEM'; END IF;
  v_app := CASE TG_TABLE_NAME WHEN 'applications' THEN (v_row ->> 'id')::uuid ELSE nullif(v_row ->> 'application_id', '')::uuid END;
  PERFORM audit_write(v_type, v_actor, v_role, TG_ARGV[0] || '_' || TG_OP, TG_TABLE_NAME, v_row ->> coalesce(TG_ARGV[1], 'id'), v_app, v_old, v_new, NULL);
  RETURN NULL;
END $$;

CREATE TRIGGER staff_profiles_audit AFTER INSERT OR UPDATE ON staff_profiles FOR EACH ROW EXECUTE FUNCTION audit_row('STAFF_PROFILE', 'user_id');
CREATE TRIGGER enquiries_audit AFTER INSERT OR UPDATE ON enquiries FOR EACH ROW EXECUTE FUNCTION audit_row('ENQUIRY');
CREATE TRIGGER applications_audit_insert AFTER INSERT ON applications FOR EACH ROW EXECUTE FUNCTION audit_row('APPLICATION');
CREATE TRIGGER documents_audit AFTER INSERT OR UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION audit_row('DOCUMENT');
CREATE TRIGGER discrepancies_audit AFTER INSERT OR UPDATE ON discrepancies FOR EACH ROW EXECUTE FUNCTION audit_row('DISCREPANCY');
CREATE TRIGGER extraction_corrections_audit AFTER INSERT ON extraction_corrections FOR EACH ROW EXECUTE FUNCTION audit_row('EXTRACTION_CORRECTION');
CREATE TRIGGER disbursements_audit AFTER INSERT OR UPDATE ON disbursements FOR EACH ROW EXECUTE FUNCTION audit_row('DISBURSEMENT');
CREATE TRIGGER generated_documents_audit AFTER INSERT ON generated_documents FOR EACH ROW EXECUTE FUNCTION audit_row('GENERATED_DOCUMENT');
CREATE TRIGGER email_outbox_audit AFTER INSERT OR UPDATE OF status ON email_outbox FOR EACH ROW EXECUTE FUNCTION audit_row('EMAIL');
CREATE TRIGGER application_terms_audit AFTER INSERT ON application_terms FOR EACH ROW EXECUTE FUNCTION audit_row('TERMS');

-- Agent runs are agent activity, whatever the session says.
CREATE FUNCTION agent_run_actor() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM audit_write('AGENT', NULL, NULL, 'AGENT_RUN_' || TG_OP, 'agent_runs', NEW.id::text, NEW.application_id, NULL,
                      jsonb_build_object('graph', NEW.graph, 'status', NEW.status, 'recommendation', NEW.recommendation), NULL);
  RETURN NULL;
END $$;
CREATE TRIGGER agent_runs_audit AFTER INSERT OR UPDATE OF status ON agent_runs FOR EACH ROW EXECUTE FUNCTION agent_run_actor();
