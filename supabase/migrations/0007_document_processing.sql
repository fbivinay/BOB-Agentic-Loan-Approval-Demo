-- Phase 7: document processing (text, AI extraction), human review and correction, verification, discrepancies and the documents attestation.
--
-- Text for each document comes from exactly one labelled method. OCR page text and raw AI output land in BigQuery lh_raw; the operational
-- copy of each extracted field is here, where people review it. Rules enforced here:
--   * a field's value changes only while its document is unverified; a human change is always paired with an extraction_corrections row
--   * PREPARED_DEMO_TEXT values are never verified evidence: a document cannot be VERIFIED while any field still carries one
--   * a document is VERIFIED or REJECTED only by a person with document.verify; processing states are set by the system
--   * the application reaches DOCS_VERIFIED only with every checklist requirement covered by a verified document, nothing still
--     processing, no open discrepancy, and a written attestation

ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (status IN ('UPLOADED', 'AWAITING_OCR', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW', 'VERIFIED', 'REJECTED', 'WITHDRAWN'));
ALTER TABLE documents
  ADD COLUMN text_method  text CHECK (text_method IN ('EMBEDDED_TEXT', 'UNLIMITED_OCR', 'PRECOMPUTED_UNLIMITED_OCR', 'MANUAL', 'PREPARED_DEMO_TEXT')),
  ADD COLUMN page_count   integer CHECK (page_count > 0),
  ADD COLUMN processing_note text,
  ADD COLUMN reviewed_by  uuid REFERENCES staff_profiles(user_id),
  ADD COLUMN reviewed_at  timestamptz,
  ADD COLUMN review_note  text,
  ADD CONSTRAINT reviewed_is_complete CHECK ((status NOT IN ('VERIFIED', 'REJECTED')) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  ADD CONSTRAINT rejection_has_reason CHECK (status <> 'REJECTED' OR length(btrim(coalesce(review_note, ''))) >= 10);

-- replaces the Phase 6 guard: adds processing states and human review
CREATE OR REPLACE FUNCTION documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_actor_type text := coalesce(nullif(current_setting('app.actor_type', true), ''), 'SYSTEM');
BEGIN
  SELECT status INTO v_status FROM applications WHERE id = NEW.application_id;
  IF TG_OP = 'INSERT' THEN
    IF v_status NOT IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY') THEN
      RAISE EXCEPTION 'documents can be added only during intake or document collection (status is %)', v_status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> 'UPLOADED' OR NEW.withdrawn_at IS NOT NULL OR NEW.reviewed_by IS NOT NULL THEN
      RAISE EXCEPTION 'a new document starts as UPLOADED' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.application_id, NEW.requirement, NEW.doc_type, NEW.storage_path, NEW.original_name, NEW.mime_type, NEW.size_bytes, NEW.sha256, NEW.uploaded_by, NEW.uploaded_at)
     IS DISTINCT FROM (OLD.application_id, OLD.requirement, OLD.doc_type, OLD.storage_path, OLD.original_name, OLD.mime_type, OLD.size_bytes, OLD.sha256, OLD.uploaded_by, OLD.uploaded_at) THEN
    RAISE EXCEPTION 'an uploaded document is immutable; withdraw it and upload a new one' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status IN ('WITHDRAWN', 'VERIFIED', 'REJECTED') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'a % document is final', lower(OLD.status) USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF v_status NOT IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY') THEN
    RAISE EXCEPTION 'documents cannot change while the application is %', v_status USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'WITHDRAWN' THEN
    IF NEW.withdrawn_by IS NULL OR NOT has_permission(NEW.withdrawn_by, 'application.intake') THEN
      RAISE EXCEPTION 'withdrawing a document needs a human with permission application.intake' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF NEW.status IN ('VERIFIED', 'REJECTED') THEN
    IF v_actor_type <> 'STAFF' OR NEW.reviewed_by IS NULL OR NOT has_permission(NEW.reviewed_by, 'document.verify') THEN
      RAISE EXCEPTION 'only a person with document.verify can verify or reject a document' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'VERIFIED' THEN
      IF OLD.status NOT IN ('EXTRACTED', 'NEEDS_MANUAL_REVIEW') THEN
        RAISE EXCEPTION 'a document is verified after its fields are extracted or keyed in (status is %)', OLD.status USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.doc_type <> 'PHOTOGRAPH' AND NOT EXISTS (SELECT 1 FROM document_fields WHERE document_id = NEW.id) THEN  -- a photograph is checked by eye
        RAISE EXCEPTION 'a document with no fields cannot be verified' USING ERRCODE = 'check_violation';
      END IF;
      IF EXISTS (SELECT 1 FROM document_fields WHERE document_id = NEW.id AND source = 'PREPARED_DEMO_TEXT') THEN
        RAISE EXCEPTION 'prepared demo data is not evidence: confirm or re-key each value from the document first' USING ERRCODE = 'check_violation';
      END IF;
    END IF;
  ELSIF NEW.status IN ('AWAITING_OCR', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW') THEN
    IF OLD.status NOT IN ('UPLOADED', 'AWAITING_OCR', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW') THEN
      RAISE EXCEPTION 'document cannot go % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
  ELSE
    RAISE EXCEPTION 'document cannot go % -> %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- ------------------------------------------------------------------ fields
CREATE TABLE document_fields (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL REFERENCES documents(id),
  field        text NOT NULL CHECK (field ~ '^[a-z][a-z0-9_]{1,40}$'),
  value        text,
  source       text NOT NULL CHECK (source IN ('AI_EXTRACTED', 'MANUAL', 'PREPARED_DEMO_TEXT')),
  text_method  text CHECK (text_method IN ('EMBEDDED_TEXT', 'UNLIMITED_OCR', 'PRECOMPUTED_UNLIMITED_OCR', 'MANUAL', 'PREPARED_DEMO_TEXT')),
  page_no      integer CHECK (page_no > 0),
  snippet      text,
  check_note   text,                        -- why the system is unsure (e.g. the quoted snippet is not in the page text)
  model        text,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, field)
);
ALTER TABLE document_fields ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER document_fields_audit AFTER INSERT OR UPDATE ON document_fields FOR EACH ROW EXECUTE FUNCTION audit_row('DOCUMENT_FIELD');
CREATE TRIGGER document_fields_no_delete BEFORE DELETE ON document_fields FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

CREATE FUNCTION document_fields_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_doc text;
  v_actor_type text := coalesce(nullif(current_setting('app.actor_type', true), ''), 'SYSTEM');
  v_actor uuid := nullif(current_setting('app.actor_id', true), '')::uuid;
BEGIN
  SELECT status INTO v_doc FROM documents WHERE id = NEW.document_id;
  IF v_doc NOT IN ('UPLOADED', 'AWAITING_OCR', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW') THEN
    RAISE EXCEPTION 'fields of a % document cannot change', lower(v_doc) USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.source = 'MANUAL' THEN
    -- a human value: the person must hold document.verify, and the change is recorded in extraction_corrections in the same transaction
    IF v_actor_type <> 'STAFF' OR v_actor IS NULL OR NOT has_permission(v_actor, 'document.verify') THEN
      RAISE EXCEPTION 'manual field values are entered by a person with document.verify' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM extraction_corrections c WHERE c.document_id = NEW.document_id AND c.field = NEW.field AND c.corrected_by = v_actor
                   AND c.value_after IS NOT DISTINCT FROM NEW.value AND c.corrected_at = now()) THEN
      RAISE EXCEPTION 'record the correction in extraction_corrections first' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF v_actor_type = 'STAFF' THEN
    RAISE EXCEPTION 'people enter values as MANUAL; % values come from the system', NEW.source USING ERRCODE = 'insufficient_privilege';
  ELSIF TG_OP = 'UPDATE' AND OLD.source = 'MANUAL' THEN
    RAISE EXCEPTION 'the system never overwrites a value a person entered' USING ERRCODE = 'insufficient_privilege';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER document_fields_guard BEFORE INSERT OR UPDATE ON document_fields FOR EACH ROW EXECUTE FUNCTION document_fields_guard();

-- ------------------------------------------------------------------ discrepancies: what was compared, and one open item per check
ALTER TABLE discrepancies
  ADD COLUMN rule   text NOT NULL DEFAULT 'MANUAL' CHECK (rule IN ('NAME', 'DOB', 'PAN', 'INCOME_SALARY_SLIP', 'INCOME_ITR', 'INCOME_BANK_CREDITS', 'MANUAL')),
  ADD COLUMN detail text;
CREATE UNIQUE INDEX discrepancies_one_open ON discrepancies (application_id, rule, coalesce(document_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE status = 'OPEN';
CREATE TRIGGER discrepancies_no_delete BEFORE DELETE ON discrepancies FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

-- ------------------------------------------------------------------ the documents attestation
UPDATE status_transitions SET needs_reason = true WHERE to_status = 'DOCS_VERIFIED';

CREATE FUNCTION applications_docs_verified_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_missing text;
BEGIN
  IF NEW.status <> 'DOCS_VERIFIED' OR OLD.status = 'DOCS_VERIFIED' THEN RETURN NEW; END IF;
  SELECT string_agg(r ->> 'code', ', ') INTO v_missing FROM jsonb_array_elements(NEW.documents_checklist -> 'required') r
   WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.application_id = NEW.id AND d.requirement = r ->> 'code' AND d.status = 'VERIFIED');
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'requirements without a verified document: %', v_missing USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM documents WHERE application_id = NEW.id AND status IN ('UPLOADED', 'AWAITING_OCR', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW')) THEN
    RAISE EXCEPTION 'every document must be verified, rejected or withdrawn first' USING ERRCODE = 'check_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM discrepancies WHERE application_id = NEW.id AND status = 'OPEN') THEN
    RAISE EXCEPTION 'open discrepancies must be resolved first' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER applications_docs_verified_guard BEFORE UPDATE OF status ON applications FOR EACH ROW EXECUTE FUNCTION applications_docs_verified_guard();

-- ------------------------------------------------------------------ daily budget counters for metered free services
CREATE TABLE usage_counters (
  service text NOT NULL,
  day     date NOT NULL,
  used    integer NOT NULL DEFAULT 0 CHECK (used >= 0),
  PRIMARY KEY (service, day)
);
ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY;

-- take one unit if the day's cap allows it; returns false (and takes nothing) at the cap
CREATE FUNCTION take_budget(p_service text, p_cap integer) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE v_used integer;
BEGIN
  INSERT INTO usage_counters (service, day) VALUES (p_service, (now() AT TIME ZONE 'Asia/Kolkata')::date) ON CONFLICT DO NOTHING;
  UPDATE usage_counters SET used = used + 1 WHERE service = p_service AND day = (now() AT TIME ZONE 'Asia/Kolkata')::date AND used < p_cap
   RETURNING used INTO v_used;
  RETURN v_used IS NOT NULL;
END $$;

CREATE INDEX jobs_application_idx ON jobs (application_id);
CREATE INDEX document_fields_document_idx ON document_fields (document_id);

-- same deny-by-default as 0004 for everything added since: the browser roles get no tables and no functions
REVOKE EXECUTE ON FUNCTION take_budget(text, integer) FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM authenticated;
  END IF;
END $$;
