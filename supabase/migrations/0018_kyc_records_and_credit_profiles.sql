-- KYC records, KYC document versions, persistent simulated credit profiles, real-document testing (owner request, 2026-11-27).
--
-- 1. kyc_profiles: the bank's KYC record for a person, keyed by PAN (and the customer ID for existing customers). Existing customers'
--    account-opening KYC is loaded by scripts/seed_kyc.py; a new-to-bank applicant's record is created from their verified loan documents;
--    an update (changed address, expired KYC) writes a new version. kyc_profile_history keeps every version (append-only).
-- 2. kyc_documents: the KYC documents behind a record, with versions. A replaced document is SUPERSEDED, never deleted.
-- 3. simulated_credit_profiles: DEMO / SIMULATED credit data per PAN and calendar month. The primary key (pan, period_start) makes it
--    impossible to hold two profiles for the same PAN in the same month, even when requests arrive together (INSERT ... ON CONFLICT).
-- 4. Real documents for testing: real 10-character PANs are accepted on applicants (the synthetic SYNP form still is), and an
--    employment proof document type is added. Aadhaar stays masked only (last 4 digits): unmasked Aadhaar is refused at verification.

CREATE TABLE kyc_profiles (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id    text UNIQUE CHECK (customer_id ~ '^BOB-C[0-9]{4}$'),
  pan            text NOT NULL UNIQUE CHECK (pan ~ '^(SYNP[0-9]{7}[A-Z]|[A-Z]{5}[0-9]{4}[A-Z])$'),
  full_name      text NOT NULL,
  date_of_birth  date NOT NULL,
  gender         text CHECK (gender IN ('M', 'F', 'O')),
  aadhaar_last4  text CHECK (aadhaar_last4 ~ '^[0-9]{4}$'),
  address_line   text,
  city           text,
  state          text,
  pincode        text,
  kyc_status     text NOT NULL CHECK (kyc_status IN ('VERIFIED', 'EXPIRED', 'PENDING')),
  verified_on    date,
  source         text NOT NULL CHECK (source IN ('ACCOUNT_OPENING', 'LOAN_ONBOARDING', 'KYC_UPDATE')),
  version        integer NOT NULL DEFAULT 1,
  updated_by     uuid REFERENCES staff_profiles(user_id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verified_has_date CHECK (kyc_status <> 'VERIFIED' OR verified_on IS NOT NULL)
);

CREATE TABLE kyc_profile_history (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  profile_id     uuid NOT NULL REFERENCES kyc_profiles(id),
  version        integer NOT NULL,
  snapshot       jsonb NOT NULL,
  changed_fields text[] NOT NULL DEFAULT '{}',
  reason         text NOT NULL,
  source         text NOT NULL,
  application_id uuid REFERENCES applications(id),
  changed_by     uuid REFERENCES staff_profiles(user_id),
  changed_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (profile_id, version)
);

CREATE TABLE kyc_documents (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  profile_id              uuid NOT NULL REFERENCES kyc_profiles(id),
  doc_type                text NOT NULL CHECK (doc_type IN ('PAN_CARD', 'AADHAAR_MASKED', 'ADDRESS_PROOF', 'PASSPORT', 'VOTER_ID')),
  version                 integer NOT NULL,
  bucket                  text NOT NULL CHECK (bucket IN ('kyc-documents', 'loan-documents')),
  storage_path            text NOT NULL,
  original_name           text NOT NULL,
  mime_type               text NOT NULL,
  sha256                  text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  source                  text NOT NULL CHECK (source IN ('ACCOUNT_OPENING', 'LOAN_APPLICATION', 'KYC_UPDATE')),
  is_sample               boolean NOT NULL DEFAULT false,  -- generated SAMPLE / DEMO document, not something a person submitted
  application_document_id uuid REFERENCES documents(id),
  status                  text NOT NULL DEFAULT 'CURRENT' CHECK (status IN ('CURRENT', 'SUPERSEDED')),
  created_by              uuid REFERENCES staff_profiles(user_id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  superseded_at           timestamptz,
  UNIQUE (profile_id, doc_type, version)
);
CREATE UNIQUE INDEX kyc_documents_one_current ON kyc_documents (profile_id, doc_type) WHERE status = 'CURRENT';

CREATE TABLE simulated_credit_profiles (
  pan            text NOT NULL,
  period_start   date NOT NULL CHECK (extract(day FROM period_start) = 1),
  valid_until    date NOT NULL,
  source         text NOT NULL CHECK (source IN ('SYNTHETIC_BUREAU', 'SIMULATED')),
  score          integer CHECK (score BETWEEN 300 AND 900),
  history_status text NOT NULL CHECK (history_status IN ('ESTABLISHED', 'THIN', 'NO_HISTORY')),
  profile        jsonb NOT NULL,
  label          text NOT NULL DEFAULT 'DEMO / SIMULATED credit profile, not a credit bureau score',
  generated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (pan, period_start),
  CHECK (valid_until >= period_start)
);

-- KYC history, documents and credit profiles are never edited or removed (a superseded document only changes its status, once).
CREATE FUNCTION kyc_documents_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'KYC documents are never deleted; supersede them' USING ERRCODE = 'insufficient_privilege'; END IF;
  IF OLD.status = 'SUPERSEDED' OR NEW.status <> 'SUPERSEDED'
     OR (NEW.id, NEW.profile_id, NEW.doc_type, NEW.version, NEW.storage_path, NEW.sha256, NEW.source)
        IS DISTINCT FROM (OLD.id, OLD.profile_id, OLD.doc_type, OLD.version, OLD.storage_path, OLD.sha256, OLD.source) THEN
    RAISE EXCEPTION 'a KYC document only changes from CURRENT to SUPERSEDED' USING ERRCODE = 'insufficient_privilege';
  END IF;
  NEW.superseded_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER kyc_documents_guard BEFORE UPDATE OR DELETE ON kyc_documents FOR EACH ROW EXECUTE FUNCTION kyc_documents_guard();
CREATE TRIGGER kyc_profile_history_append_only BEFORE UPDATE OR DELETE ON kyc_profile_history FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER simulated_credit_profiles_append_only BEFORE UPDATE OR DELETE ON simulated_credit_profiles FOR EACH ROW EXECUTE FUNCTION audit_reject_change();
CREATE TRIGGER kyc_profiles_no_delete BEFORE DELETE ON kyc_profiles FOR EACH ROW EXECUTE FUNCTION audit_reject_change();

-- ------------------------------------------------------------------ real documents for testing
ALTER TABLE applicants DROP CONSTRAINT IF EXISTS applicants_pan_check;
ALTER TABLE applicants ADD CONSTRAINT applicants_pan_check CHECK (pan ~ '^(SYNP[0-9]{7}[A-Z]|[A-Z]{5}[0-9]{4}[A-Z])$');
ALTER TABLE documents DROP CONSTRAINT documents_doc_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_doc_type_check CHECK (doc_type IN (
  'APPLICATION_FORM', 'PHOTOGRAPH', 'PAN_CARD', 'AADHAAR_MASKED', 'PASSPORT', 'VOTER_ID', 'SALARY_SLIP', 'ITR', 'BANK_STATEMENT',
  'BUSINESS_PROOF', 'PENSION_ORDER', 'EMPLOYMENT_PROOF', 'OTHER'));
-- supporting documents (e.g. an employment letter) can be added to any application; they are not on the checklist
ALTER TABLE documents DROP CONSTRAINT documents_requirement_check;
ALTER TABLE documents ADD CONSTRAINT documents_requirement_check CHECK (requirement IN ('APPLICATION_FORM', 'PHOTOGRAPH', 'PAN_CARD', 'ADDRESS_PROOF',
  'SALARY_SLIPS_3M', 'ITR_1Y', 'BUSINESS_PROOF', 'PENSION_PROOF', 'BANK_STATEMENT_6M', 'SUPPORTING'));

ALTER TABLE kyc_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_profile_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE kyc_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE simulated_credit_profiles ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON kyc_profiles, kyc_profile_history, kyc_documents, simulated_credit_profiles FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON kyc_profiles, kyc_profile_history, kyc_documents, simulated_credit_profiles FROM authenticated;
  END IF;
END $$;
REVOKE ALL ON FUNCTION kyc_documents_guard() FROM PUBLIC;
