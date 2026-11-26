-- Phase 13: analytics events. Every status change and every simulated disbursement release now writes an analytics_outbox row in the same
-- transaction as the change itself (decisions already did, since 0003). A batch job loads the outbox into BigQuery lh_raw.events with the
-- event id for idempotency. Payloads hold workflow facts only: no name, PAN, mobile, email or address.

ALTER TABLE analytics_outbox ADD COLUMN occurred_at timestamptz NOT NULL DEFAULT now(), ADD COLUMN sent_at timestamptz;
CREATE INDEX analytics_outbox_due_idx ON analytics_outbox (next_attempt_at) WHERE status = 'PENDING';

CREATE FUNCTION analytics_status_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a record;
BEGIN
  SELECT ap.reference, ap.requested_amount, ap.requested_tenure_months, ap.ai_recommendation, ap.created_at, ap.assigned_to IS NOT NULL AS assigned,
         p.bank_relationship, p.employment_type
    INTO a FROM applications ap JOIN applicants p ON p.id = ap.applicant_id WHERE ap.id = NEW.application_id;
  INSERT INTO analytics_outbox (application_id, event_type, occurred_at, payload)
  VALUES (NEW.application_id, 'STATUS_CHANGED', NEW.occurred_at, jsonb_build_object(
    'from', NEW.before_state ->> 'status', 'to', NEW.after_state ->> 'status', 'version', (NEW.after_state ->> 'version')::int,
    'actor_type', NEW.actor_type, 'actor_role', NEW.actor_role, 'reference', a.reference, 'bank_relationship', a.bank_relationship,
    'employment_type', a.employment_type, 'requested_amount', a.requested_amount, 'requested_tenure_months', a.requested_tenure_months,
    'ai_recommendation', a.ai_recommendation, 'application_created_at', a.created_at, 'channel', 'BRANCH'));
  RETURN NULL;
END $$;
CREATE TRIGGER audit_events_analytics AFTER INSERT ON audit_events FOR EACH ROW WHEN (NEW.event_type = 'STATUS_TRANSITION' AND NEW.application_id IS NOT NULL)
  EXECUTE FUNCTION analytics_status_event();

CREATE FUNCTION analytics_disbursement_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t application_terms;
BEGIN
  SELECT * INTO t FROM application_terms WHERE id = NEW.terms_id;
  INSERT INTO analytics_outbox (application_id, event_type, occurred_at, payload)
  VALUES (NEW.application_id, 'DISBURSEMENT_RELEASED', NEW.released_at, jsonb_build_object(
    'net_amount', NEW.net_amount, 'approved_amount', t.amount, 'rate_bp', t.rate_bp, 'tenure_months', t.tenure_months, 'released_by_role', NEW.released_by_role,
    'first_emi_date', NEW.first_emi_date, 'simulated', NEW.is_simulated));
  RETURN NULL;
END $$;
CREATE TRIGGER disbursements_analytics AFTER UPDATE ON disbursements FOR EACH ROW WHEN (NEW.status = 'RELEASED' AND OLD.status IS DISTINCT FROM 'RELEASED')
  EXECUTE FUNCTION analytics_disbursement_event();

-- the outbox is a record of what happened: rows are never deleted and their content never changes; only the delivery fields move
CREATE FUNCTION analytics_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'analytics events are kept' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'attempts', 'next_attempt_at', 'last_error', 'sent_at'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'attempts', 'next_attempt_at', 'last_error', 'sent_at']) THEN
    RAISE EXCEPTION 'an analytics event''s content is fixed' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER analytics_outbox_guard BEFORE UPDATE OR DELETE ON analytics_outbox FOR EACH ROW EXECUTE FUNCTION analytics_outbox_guard();

REVOKE ALL ON FUNCTION analytics_status_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION analytics_disbursement_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION analytics_outbox_guard() FROM PUBLIC;

-- one-time initial load: status changes recorded before this migration (from the append-only audit trail) become events too, with the
-- time they happened, so the funnel counts the applications already in the system
INSERT INTO analytics_outbox (application_id, event_type, occurred_at, payload)
SELECT e.application_id, 'STATUS_CHANGED', e.occurred_at, jsonb_build_object(
         'from', e.before_state ->> 'status', 'to', e.after_state ->> 'status', 'version', (e.after_state ->> 'version')::int,
         'actor_type', e.actor_type, 'actor_role', e.actor_role, 'reference', ap.reference, 'bank_relationship', p.bank_relationship,
         'employment_type', p.employment_type, 'requested_amount', ap.requested_amount, 'requested_tenure_months', ap.requested_tenure_months,
         'ai_recommendation', ap.ai_recommendation, 'application_created_at', ap.created_at, 'channel', 'BRANCH', 'backfilled', true)
FROM audit_events e JOIN applications ap ON ap.id = e.application_id JOIN applicants p ON p.id = ap.applicant_id
WHERE e.event_type = 'STATUS_TRANSITION';
