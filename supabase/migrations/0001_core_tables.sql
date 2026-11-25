-- Operational schema (Supabase Postgres): workflow state, staff roles, audit, outboxes.
-- Customer, account and bureau data does NOT live here: it is in the BigQuery lakehouse.
-- All data is synthetic. Rates, limits and fees held here are demo values, never Bank of Baroda policy.

-- ------------------------------------------------------------------ staff and permissions
CREATE TABLE staff_profiles (
  user_id    uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE RESTRICT,
  full_name  text NOT NULL CHECK (length(btrim(full_name)) > 0),
  email      text NOT NULL,
  role       text NOT NULL CHECK (role IN ('LOAN_OFFICER', 'ASSISTANT_MANAGER', 'BRANCH_MANAGER')),
  branch_id  text,
  is_active  boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE staff_profiles IS 'Roles live here, never in user-editable auth metadata. Three internal banking roles only.';

CREATE TABLE role_permissions (
  role        text NOT NULL CHECK (role IN ('LOAN_OFFICER', 'ASSISTANT_MANAGER', 'BRANCH_MANAGER')),
  permission  text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('FIXED', 'CONFIGURABLE')),
  description text NOT NULL,
  PRIMARY KEY (role, permission),
  -- Fixed invariants that configuration can never grant to the wrong role.
  CONSTRAINT decision_roles_only CHECK (permission <> 'decision.make' OR role IN ('ASSISTANT_MANAGER', 'BRANCH_MANAGER')),
  CONSTRAINT branch_manager_only CHECK (permission NOT IN ('application.takeover', 'application.refer', 'terms.adjust') OR role = 'BRANCH_MANAGER')
);
COMMENT ON TABLE role_permissions IS 'CONFIGURABLE rows are demo permissions, not Bank of Baroda''s actual authorization structure.';

CREATE TABLE demo_config (
  key         text PRIMARY KEY,
  value       numeric NOT NULL,
  unit        text NOT NULL,
  confidence  text NOT NULL DEFAULT 'DEMO_ASSUMPTION' CHECK (confidence IN ('DEMO_ASSUMPTION', 'VERIFIED')),
  description text NOT NULL
);
COMMENT ON TABLE demo_config IS 'Demo limits. DEMO_ASSUMPTION values are never attributed to Bank of Baroda.';

-- ------------------------------------------------------------------ applicants and enquiries
CREATE TABLE applicants (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id             text UNIQUE CHECK (customer_id ~ '^BOB-C[0-9]{4}$'),
  bank_relationship       text NOT NULL CHECK (bank_relationship IN ('ETB', 'NTB')),
  full_name               text NOT NULL CHECK (length(btrim(full_name)) > 0),
  date_of_birth           date NOT NULL CHECK (date_of_birth < CURRENT_DATE),
  gender                  text CHECK (gender IN ('M', 'F', 'O')),
  mobile                  text NOT NULL CHECK (mobile ~ '^[6-9][0-9]{9}$'),
  email                   text,
  pan                     text CHECK (pan ~ '^SYNP[0-9]{7}[A-Z]$'),  -- synthetic form only, can never be a real 10-character PAN
  residency_status        text NOT NULL DEFAULT 'RESIDENT' CHECK (residency_status IN ('RESIDENT', 'NRI')),
  is_staff                boolean NOT NULL DEFAULT false,
  employment_type         text CHECK (employment_type IN ('SALARIED_PRIVATE', 'SALARIED_GOVT', 'SALARIED_PSU', 'SELF_EMPLOYED_PROFESSIONAL', 'SELF_EMPLOYED_BUSINESS', 'PENSIONER')),
  declared_monthly_net    numeric(14, 2) CHECK (declared_monthly_net >= 0),
  existing_monthly_emi    numeric(14, 2) NOT NULL DEFAULT 0 CHECK (existing_monthly_emi >= 0),
  declared_credit_history text NOT NULL DEFAULT 'UNKNOWN' CHECK (declared_credit_history IN ('ESTABLISHED', 'LIMITED', 'NONE', 'UNKNOWN')),
  created_by              uuid REFERENCES staff_profiles(user_id),
  created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX applicants_mobile_idx ON applicants (mobile);

CREATE SEQUENCE enquiry_seq;
CREATE TABLE enquiries (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reference                text NOT NULL UNIQUE DEFAULT ('ENQ-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('enquiry_seq')::text, 6, '0')),
  applicant_id             uuid NOT NULL REFERENCES applicants(id),
  status                   text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONVERTED', 'CLOSED')),
  requested_amount         numeric(14, 2) CHECK (requested_amount > 0),
  requested_tenure_months  integer CHECK (requested_tenure_months BETWEEN 1 AND 360),
  indicative               jsonb,
  docs_checklist           jsonb,
  converted_application_id uuid,
  created_by               uuid NOT NULL REFERENCES staff_profiles(user_id),
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  version                  integer NOT NULL DEFAULT 1,
  -- Every indicative figure must carry its "not a loan offer" label, enforced in the database.
  CONSTRAINT indicative_is_labelled CHECK (indicative IS NULL OR (indicative ->> 'disclaimer') IS NOT NULL),
  CONSTRAINT converted_consistent CHECK ((status = 'CONVERTED') = (converted_application_id IS NOT NULL))
);
COMMENT ON TABLE enquiries IS 'Draft stage. Deliberately has no bureau, OCR, agent, decision or workflow columns; agent_runs and jobs reference applications only.';

-- ------------------------------------------------------------------ policy rule sets
CREATE TABLE policy_rule_sets (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scheme            text NOT NULL,
  version           integer NOT NULL CHECK (version > 0),
  status            text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED')),
  content           jsonb NOT NULL,
  content_sha256    text NOT NULL,
  source_references jsonb NOT NULL DEFAULT '[]',
  test_result       jsonb,
  created_by        uuid REFERENCES staff_profiles(user_id),
  approved_by       uuid REFERENCES staff_profiles(user_id),
  approved_at       timestamptz,
  activation_reason text,
  effective_from    timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (scheme, version),
  -- An unverified candidate figure can never be inside any rule set.
  CONSTRAINT no_unverified_candidates CHECK (content::text NOT LIKE '%UNVERIFIED_CANDIDATE%')
);
CREATE UNIQUE INDEX one_active_rule_set_per_scheme ON policy_rule_sets (scheme) WHERE status = 'ACTIVE';

-- ------------------------------------------------------------------ applications
CREATE SEQUENCE application_seq;
CREATE TABLE applications (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reference               text NOT NULL UNIQUE DEFAULT ('APP-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('application_seq')::text, 6, '0')),
  enquiry_id              uuid UNIQUE REFERENCES enquiries(id),
  applicant_id            uuid NOT NULL REFERENCES applicants(id),
  status                  text NOT NULL DEFAULT 'INTAKE' CHECK (status IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY', 'DOCS_VERIFIED', 'AI_ASSESSED', 'FORWARDED',
                            'UNDER_REVIEW', 'APPROVED', 'DECLINED', 'RECONSIDERATION', 'SANCTIONED', 'DISBURSED')),
  version                 integer NOT NULL DEFAULT 1,
  requested_amount        numeric(14, 2) NOT NULL CHECK (requested_amount > 0),
  requested_tenure_months integer NOT NULL CHECK (requested_tenure_months BETWEEN 1 AND 360),
  policy_rule_set_id      uuid REFERENCES policy_rule_sets(id),
  ai_recommendation       text CHECK (ai_recommendation IN ('RECOMMENDED_FOR_APPROVAL', 'REFER_FOR_FURTHER_REVIEW', 'POTENTIALLY_INELIGIBLE')),
  ai_recommendation_run_id uuid,
  assessed_at             timestamptz,
  assigned_to             uuid REFERENCES staff_profiles(user_id),
  forwarded_by            uuid REFERENCES staff_profiles(user_id),
  forwarded_at            timestamptz,
  created_by              uuid NOT NULL REFERENCES staff_profiles(user_id),
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE enquiries ADD CONSTRAINT enquiries_converted_fk FOREIGN KEY (converted_application_id) REFERENCES applications(id);
CREATE INDEX applications_status_idx ON applications (status);

CREATE TABLE status_transitions (
  from_status  text NOT NULL,
  to_status    text NOT NULL,
  permission   text,                       -- permission a STAFF actor needs (NULL = SYSTEM only)
  actors       text NOT NULL CHECK (actors IN ('STAFF', 'SYSTEM', 'EITHER')),
  needs_reason boolean NOT NULL DEFAULT false,
  PRIMARY KEY (from_status, to_status)
);

-- ------------------------------------------------------------------ agents, documents, discrepancies
CREATE TABLE agent_runs (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id     uuid NOT NULL REFERENCES applications(id),
  graph              text NOT NULL CHECK (graph IN ('DOCUMENT', 'ASSESSMENT')),
  status             text NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING', 'DONE', 'FAILED')),
  steps              jsonb NOT NULL DEFAULT '[]',
  output             jsonb,
  recommendation     text CHECK (recommendation IN ('RECOMMENDED_FOR_APPROVAL', 'REFER_FOR_FURTHER_REVIEW', 'POTENTIALLY_INELIGIBLE')),
  policy_rule_set_id uuid REFERENCES policy_rule_sets(id),
  model              text,
  error              text,
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz,
  CONSTRAINT recommendation_only_for_assessment CHECK (recommendation IS NULL OR graph = 'ASSESSMENT')
);
ALTER TABLE applications ADD CONSTRAINT applications_run_fk FOREIGN KEY (ai_recommendation_run_id) REFERENCES agent_runs(id);

CREATE TABLE documents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL REFERENCES applications(id),
  doc_type       text NOT NULL CHECK (doc_type IN ('PAN_CARD', 'AADHAAR_MASKED', 'SALARY_SLIP', 'ITR', 'BANK_STATEMENT', 'OTHER')),
  storage_path   text NOT NULL UNIQUE,
  original_name  text NOT NULL,
  mime_type      text NOT NULL,
  size_bytes     bigint NOT NULL CHECK (size_bytes > 0),
  sha256         text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  status         text NOT NULL DEFAULT 'UPLOADED' CHECK (status IN ('UPLOADED', 'EXTRACTED', 'NEEDS_MANUAL_REVIEW', 'VERIFIED', 'REJECTED')),
  uploaded_by    uuid NOT NULL REFERENCES staff_profiles(user_id),
  uploaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE discrepancies (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid NOT NULL REFERENCES applications(id),
  document_id    uuid REFERENCES documents(id),
  field          text NOT NULL,
  form_value     text,
  document_value text,
  status         text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
  resolution     text CHECK (resolution IN ('ACCEPT', 'CORRECT', 'REQUEST_NEW_DOCUMENT')),
  resolution_note text,
  resolved_by    uuid REFERENCES staff_profiles(user_id),
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT resolved_is_complete CHECK (status = 'OPEN' OR (resolution IS NOT NULL AND length(btrim(coalesce(resolution_note, ''))) >= 5 AND resolved_by IS NOT NULL))
);

CREATE TABLE extraction_corrections (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id    uuid NOT NULL REFERENCES documents(id),
  field          text NOT NULL,
  page_no        integer CHECK (page_no > 0),
  value_before   text,
  value_after    text NOT NULL,
  source         text NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL')),
  corrected_by   uuid NOT NULL REFERENCES staff_profiles(user_id),
  corrected_at   timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ terms, decisions, changes (human-only writes, see 0003)
CREATE TABLE decisions (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id             uuid NOT NULL REFERENCES applications(id),
  application_version        integer NOT NULL,
  outcome                    text NOT NULL CHECK (outcome IN ('APPROVED', 'DECLINED', 'REFERRED')),
  decided_by                 uuid NOT NULL REFERENCES staff_profiles(user_id),
  decided_by_role            text NOT NULL CHECK (decided_by_role IN ('ASSISTANT_MANAGER', 'BRANCH_MANAGER')),
  reason                     text,
  ai_recommendation_snapshot jsonb NOT NULL,
  is_override                boolean NOT NULL,
  decided_at                 timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decisions_app_idx ON decisions (application_id, decided_at);

CREATE TABLE application_terms (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id  uuid NOT NULL REFERENCES applications(id),
  kind            text NOT NULL CHECK (kind IN ('REQUESTED', 'ELIGIBLE', 'APPROVED')),
  seq             integer NOT NULL DEFAULT 1 CHECK (seq > 0),
  amount          numeric(14, 2) NOT NULL CHECK (amount > 0),
  rate_bp         integer NOT NULL CHECK (rate_bp BETWEEN 1 AND 6000),
  tenure_months   integer NOT NULL CHECK (tenure_months BETWEEN 1 AND 360),
  policy_rule_set_id uuid REFERENCES policy_rule_sets(id),
  decision_id     uuid REFERENCES decisions(id),
  term_change_id  uuid,
  created_by      uuid REFERENCES staff_profiles(user_id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (application_id, kind, seq)
);

CREATE TABLE term_changes (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id      uuid NOT NULL REFERENCES applications(id),
  application_version integer NOT NULL,
  changed_by          uuid NOT NULL REFERENCES staff_profiles(user_id),
  changed_by_role     text NOT NULL,
  reason              text NOT NULL,
  before_terms        jsonb NOT NULL,
  after_terms         jsonb NOT NULL,
  changed_at          timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE application_terms ADD CONSTRAINT application_terms_change_fk FOREIGN KEY (term_change_id) REFERENCES term_changes(id);

-- ------------------------------------------------------------------ sanction pack, email, disbursement
CREATE TABLE generated_documents (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id     uuid NOT NULL REFERENCES applications(id),
  kind               text NOT NULL CHECK (kind IN ('SANCTION_LETTER', 'KFS', 'EMI_SCHEDULE')),
  storage_path       text NOT NULL UNIQUE,
  sha256             text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  terms_id           uuid NOT NULL REFERENCES application_terms(id),
  policy_rule_set_id uuid REFERENCES policy_rule_sets(id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (application_id, kind)
);

CREATE TABLE email_outbox (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id      uuid NOT NULL REFERENCES applications(id),
  kind                text NOT NULL CHECK (kind IN ('APPROVAL', 'DISBURSEMENT_CONFIRMATION')),
  to_address          text NOT NULL,
  subject             text NOT NULL,
  attachment_ids      uuid[] NOT NULL DEFAULT '{}',
  status              text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'DELAYED', 'BOUNCED', 'COMPLAINED', 'FAILED')),
  attempts            integer NOT NULL DEFAULT 0,
  max_attempts        integer NOT NULL DEFAULT 5,
  next_attempt_at     timestamptz NOT NULL DEFAULT now(),
  last_error          text,
  provider_message_id text,
  idempotency_key     text NOT NULL UNIQUE,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (application_id, kind)
);

CREATE TABLE email_events (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email_id    uuid NOT NULL REFERENCES email_outbox(id),
  event_type  text NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  raw         jsonb
);

CREATE TABLE disbursements (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id          uuid NOT NULL UNIQUE REFERENCES applications(id),
  status                  text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CHECKLIST_COMPLETE', 'RELEASED')),
  is_simulated            boolean NOT NULL DEFAULT true CHECK (is_simulated),  -- no real banking transaction, ever
  acceptance_recorded_at  timestamptz,
  acceptance_recorded_by  uuid REFERENCES staff_profiles(user_id),
  checklist               jsonb NOT NULL DEFAULT '{}',
  released_by             uuid REFERENCES staff_profiles(user_id),
  released_by_role        text,
  released_at             timestamptz,
  simulated_reference     text UNIQUE CHECK (simulated_reference ~ '^SIM-[A-Z0-9]{8,}$'),
  loan_account_number     text UNIQUE,
  first_emi_date          date,
  created_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT released_is_complete CHECK (status <> 'RELEASED' OR (released_by IS NOT NULL AND released_at IS NOT NULL AND simulated_reference IS NOT NULL
                                          AND loan_account_number IS NOT NULL AND first_emi_date IS NOT NULL AND acceptance_recorded_at IS NOT NULL))
);

-- ------------------------------------------------------------------ jobs and analytics outbox
CREATE TABLE jobs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind            text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}',
  application_id  uuid REFERENCES applications(id),
  status          text NOT NULL DEFAULT 'QUEUED' CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')),
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 5,
  run_after       timestamptz NOT NULL DEFAULT now(),
  locked_at       timestamptz,
  last_error      text,
  idempotency_key text UNIQUE,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready_idx ON jobs (run_after) WHERE status = 'QUEUED';

CREATE TABLE analytics_outbox (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id        uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  application_id  uuid REFERENCES applications(id),
  event_type      text NOT NULL,
  payload         jsonb NOT NULL,
  status          text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED')),
  attempts        integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ audit (append-only, see 0002)
CREATE TABLE audit_events (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at    timestamptz NOT NULL DEFAULT now(),
  actor_type     text NOT NULL CHECK (actor_type IN ('STAFF', 'SYSTEM', 'AGENT')),
  actor_id       uuid,
  actor_role     text,
  event_type     text NOT NULL,
  entity_type    text NOT NULL,
  entity_id      text,
  application_id uuid,
  before_state   jsonb,
  after_state    jsonb,
  reason         text,
  metadata       jsonb NOT NULL DEFAULT '{}',
  CONSTRAINT staff_actor_has_id CHECK (actor_type <> 'STAFF' OR (actor_id IS NOT NULL AND actor_role IS NOT NULL))
);
CREATE INDEX audit_app_idx ON audit_events (application_id, occurred_at);
CREATE INDEX audit_event_idx ON audit_events (event_type);

CREATE TABLE policy_activation_log (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rule_set_id   uuid NOT NULL REFERENCES policy_rule_sets(id),
  scheme        text NOT NULL,
  version       integer NOT NULL,
  activated_by  uuid NOT NULL REFERENCES staff_profiles(user_id),
  activated_by_role text NOT NULL,
  reason        text NOT NULL,
  test_result   jsonb NOT NULL,
  activated_at  timestamptz NOT NULL DEFAULT now()
);
