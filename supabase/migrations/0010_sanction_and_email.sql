-- Phase 10: sanction pack and the approval email.
--
--  * Sanction documents are a record: once written they are never changed or removed (a new pack would need a new decision).
--  * An approval email exists only for an application with a human APPROVED decision, and may attach only that application's own
--    sanction documents.
--  * Synthetic customers never receive mail: an outbox row addressed to the reserved example.invalid domain is refused, so the demo
--    redirect cannot be bypassed by accident.
--  * Email events are append-only.

CREATE TRIGGER generated_documents_immutable BEFORE UPDATE OR DELETE ON generated_documents FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER generated_documents_no_truncate BEFORE TRUNCATE ON generated_documents FOR EACH STATEMENT EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER email_events_immutable BEFORE UPDATE OR DELETE ON email_events FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

ALTER TABLE email_outbox ADD CONSTRAINT email_outbox_never_synthetic CHECK (to_address !~* '@example\.invalid$' AND to_address ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$');

CREATE FUNCTION email_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'outbox rows are kept as the record of what was sent' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF TG_OP = 'UPDATE' AND (NEW.application_id IS DISTINCT FROM OLD.application_id OR NEW.kind IS DISTINCT FROM OLD.kind
                           OR NEW.attachment_ids IS DISTINCT FROM OLD.attachment_ids OR NEW.subject IS DISTINCT FROM OLD.subject) THEN
    RAISE EXCEPTION 'an email''s application, kind, subject and attachments are fixed once queued' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.kind = 'APPROVAL' AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.application_id = NEW.application_id AND d.outcome = 'APPROVED') THEN
    RAISE EXCEPTION 'an approval email needs a human APPROVED decision' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF EXISTS (SELECT 1 FROM unnest(NEW.attachment_ids) x WHERE NOT EXISTS (SELECT 1 FROM generated_documents g WHERE g.id = x AND g.application_id = NEW.application_id)) THEN
    RAISE EXCEPTION 'an email may attach only this application''s own generated documents' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER email_outbox_guard BEFORE INSERT OR UPDATE OR DELETE ON email_outbox FOR EACH ROW EXECUTE FUNCTION email_outbox_guard();

CREATE INDEX IF NOT EXISTS email_outbox_provider_idx ON email_outbox (provider_message_id);

REVOKE ALL ON FUNCTION email_outbox_guard() FROM PUBLIC, anon, authenticated;
