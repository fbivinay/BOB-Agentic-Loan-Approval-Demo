-- Phase 6: intake, new-to-bank registration details, the frozen document checklist, and document upload records.
--
-- * Applicants get a current address (needed for KYC and, later, the sanction letter).
-- * Completing intake freezes the document checklist on the application. It can be written only while the application is in INTAKE,
--   and once intake is complete it never changes.
-- * Each uploaded document records which checklist requirement it is for (requirement) and what the file physically is (doc_type).
-- * Documents are never deleted: a wrongly uploaded file is WITHDRAWN with a reason, by a person with the intake permission, before verification.
-- * Every role can view applications.

ALTER TABLE applicants
  ADD COLUMN address_line text CHECK (length(btrim(address_line)) BETWEEN 5 AND 200),
  ADD COLUMN city         text CHECK (length(btrim(city)) BETWEEN 2 AND 60),
  ADD COLUMN state        text CHECK (length(btrim(state)) BETWEEN 2 AND 60),
  ADD COLUMN pincode      text CHECK (pincode ~ '^[1-9][0-9]{5}$');

ALTER TABLE applications
  ADD COLUMN documents_checklist jsonb,
  ADD COLUMN intake_completed_by uuid REFERENCES staff_profiles(user_id),
  ADD COLUMN intake_completed_at timestamptz,
  ADD CONSTRAINT intake_complete_together CHECK ((intake_completed_at IS NULL) = (intake_completed_by IS NULL)
                                                 AND (intake_completed_at IS NULL OR documents_checklist IS NOT NULL));

CREATE FUNCTION applications_intake_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.documents_checklist, NEW.intake_completed_by, NEW.intake_completed_at) IS NOT DISTINCT FROM
     (OLD.documents_checklist, OLD.intake_completed_by, OLD.intake_completed_at) THEN
    RETURN NEW;
  END IF;
  IF OLD.status <> 'INTAKE' OR OLD.intake_completed_at IS NOT NULL THEN
    RAISE EXCEPTION 'the document checklist is fixed once intake is complete' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.intake_completed_by IS NOT NULL AND NOT has_permission(NEW.intake_completed_by, 'application.intake') THEN
    RAISE EXCEPTION 'intake is completed by a person with the application.intake permission' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER applications_intake_guard BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION applications_intake_guard();

-- leaving INTAKE needs a completed intake
CREATE FUNCTION applications_leave_intake() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'INTAKE' AND NEW.status <> 'INTAKE' AND NEW.intake_completed_at IS NULL THEN
    RAISE EXCEPTION 'complete intake (applicant details and document checklist) before leaving INTAKE' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER applications_leave_intake BEFORE UPDATE OF status ON applications FOR EACH ROW EXECUTE FUNCTION applications_leave_intake();

-- ------------------------------------------------------------------ documents
ALTER TABLE documents DROP CONSTRAINT documents_doc_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_doc_type_check CHECK (doc_type IN (
  'APPLICATION_FORM', 'PHOTOGRAPH', 'PAN_CARD', 'AADHAAR_MASKED', 'PASSPORT', 'VOTER_ID', 'SALARY_SLIP', 'ITR', 'BANK_STATEMENT',
  'BUSINESS_PROOF', 'PENSION_ORDER', 'OTHER'));
ALTER TABLE documents
  ADD COLUMN requirement text NOT NULL CHECK (requirement IN ('APPLICATION_FORM', 'PHOTOGRAPH', 'PAN_CARD', 'ADDRESS_PROOF', 'SALARY_SLIPS_3M',
                                                              'ITR_1Y', 'BUSINESS_PROOF', 'PENSION_PROOF', 'BANK_STATEMENT_6M')),
  ADD COLUMN withdrawn_reason text,
  ADD COLUMN withdrawn_by uuid REFERENCES staff_profiles(user_id),
  ADD COLUMN withdrawn_at timestamptz;
ALTER TABLE documents DROP CONSTRAINT documents_status_check;
ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (status IN ('UPLOADED', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW', 'VERIFIED', 'REJECTED', 'WITHDRAWN'));
ALTER TABLE documents ADD CONSTRAINT withdrawn_is_complete CHECK (
  (status = 'WITHDRAWN') = (withdrawn_at IS NOT NULL)
  AND (status <> 'WITHDRAWN' OR (withdrawn_by IS NOT NULL AND length(btrim(coalesce(withdrawn_reason, ''))) >= 10)));
ALTER TABLE documents ADD CONSTRAINT mime_type_allowed CHECK (mime_type IN ('application/pdf', 'image/png', 'image/jpeg'));
ALTER TABLE documents ADD CONSTRAINT size_within_limit CHECK (size_bytes <= 10485760);
CREATE UNIQUE INDEX documents_one_live_copy ON documents (application_id, sha256) WHERE status <> 'WITHDRAWN';

CREATE FUNCTION documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM applications WHERE id = NEW.application_id;
  IF TG_OP = 'INSERT' THEN
    IF v_status NOT IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY') THEN
      RAISE EXCEPTION 'documents can be added only during intake or document collection (status is %)', v_status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> 'UPLOADED' OR NEW.withdrawn_at IS NOT NULL THEN
      RAISE EXCEPTION 'a new document starts as UPLOADED' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  -- the file and its identity never change
  IF (NEW.application_id, NEW.requirement, NEW.doc_type, NEW.storage_path, NEW.original_name, NEW.mime_type, NEW.size_bytes, NEW.sha256, NEW.uploaded_by, NEW.uploaded_at)
     IS DISTINCT FROM (OLD.application_id, OLD.requirement, OLD.doc_type, OLD.storage_path, OLD.original_name, OLD.mime_type, OLD.size_bytes, OLD.sha256, OLD.uploaded_by, OLD.uploaded_at) THEN
    RAISE EXCEPTION 'an uploaded document is immutable; withdraw it and upload a new one' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.status = 'WITHDRAWN' THEN
    RAISE EXCEPTION 'a withdrawn document stays withdrawn' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.status = 'WITHDRAWN' THEN
    IF OLD.status NOT IN ('UPLOADED', 'NEEDS_MANUAL_REVIEW', 'REJECTED') OR v_status NOT IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY') THEN
      RAISE EXCEPTION 'only an unverified document can be withdrawn, before the documents are verified' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.withdrawn_by IS NULL OR NOT has_permission(NEW.withdrawn_by, 'application.intake') THEN
      RAISE EXCEPTION 'withdrawing a document needs a human with permission application.intake' USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER documents_guard BEFORE INSERT OR UPDATE ON documents FOR EACH ROW EXECUTE FUNCTION documents_guard();
CREATE TRIGGER documents_no_delete BEFORE DELETE ON documents FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

-- ------------------------------------------------------------------ permissions
INSERT INTO role_permissions (role, permission, kind, description) VALUES
 ('LOAN_OFFICER', 'application.view',      'FIXED', 'View applications and their documents'),
 ('ASSISTANT_MANAGER', 'application.view', 'FIXED', 'View applications and their documents'),
 ('BRANCH_MANAGER', 'application.view',    'FIXED', 'View applications and their documents')
ON CONFLICT DO NOTHING;
