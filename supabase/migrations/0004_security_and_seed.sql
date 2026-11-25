-- Row level security: deny by default. The backend (service role / owner connection) is the only reader and writer;
-- the browser uses Supabase only to sign in. anon and authenticated get no table access at all.

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename NOT IN ('schema_migrations') LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM authenticated;
  END IF;
END $$;

-- ------------------------------------------------------------------ permissions (seed)
INSERT INTO role_permissions (role, permission, kind, description) VALUES
 ('LOAN_OFFICER', 'enquiry.manage',        'FIXED',        'Create and manage enquiries (drafts)'),
 ('LOAN_OFFICER', 'application.intake',    'FIXED',        'Convert an enquiry, run intake, upload documents'),
 ('LOAN_OFFICER', 'document.verify',       'FIXED',        'Verify documents, correct extractions, resolve discrepancies, attest'),
 ('LOAN_OFFICER', 'application.forward',   'FIXED',        'Forward a completed application for review'),
 ('LOAN_OFFICER', 'assessment.view',       'FIXED',        'View the AI assessment'),
 ('LOAN_OFFICER', 'audit.view',            'FIXED',        'View the audit trail'),
 ('LOAN_OFFICER', 'disbursement.track',    'CONFIGURABLE', 'Track disbursement and record customer acceptance (demo permission)'),
 ('LOAN_OFFICER', 'disbursement.view',     'FIXED',        'View disbursement status'),
 ('LOAN_OFFICER', 'policy.view',           'FIXED',        'Read the policy knowledge base'),
 ('ASSISTANT_MANAGER', 'enquiry.view',     'FIXED',        'View enquiries'),
 ('ASSISTANT_MANAGER', 'assessment.view',  'FIXED',        'View the AI assessment'),
 ('ASSISTANT_MANAGER', 'audit.view',       'FIXED',        'View the audit trail'),
 ('ASSISTANT_MANAGER', 'decision.make',    'FIXED',        'Approve or decline an application'),
 ('ASSISTANT_MANAGER', 'disbursement.release', 'CONFIGURABLE', 'Release a simulated disbursement (demo permission)'),
 ('ASSISTANT_MANAGER', 'disbursement.view','FIXED',        'View disbursement status'),
 ('ASSISTANT_MANAGER', 'analytics.view',   'FIXED',        'View analytics'),
 ('ASSISTANT_MANAGER', 'system.view',      'FIXED',        'View system status'),
 ('ASSISTANT_MANAGER', 'policy.view',      'FIXED',        'Read the policy knowledge base'),
 ('BRANCH_MANAGER', 'enquiry.view',        'FIXED',        'View enquiries'),
 ('BRANCH_MANAGER', 'assessment.view',     'FIXED',        'View the AI assessment'),
 ('BRANCH_MANAGER', 'audit.view',          'FIXED',        'View the audit trail'),
 ('BRANCH_MANAGER', 'decision.make',       'FIXED',        'Approve or decline an application'),
 ('BRANCH_MANAGER', 'application.takeover','FIXED',        'Take over an application from another reviewer'),
 ('BRANCH_MANAGER', 'application.refer',   'FIXED',        'Refer an application for reconsideration'),
 ('BRANCH_MANAGER', 'terms.adjust',        'FIXED',        'Adjust loan terms within demo limits, with a recorded reason'),
 ('BRANCH_MANAGER', 'policy.activate',     'CONFIGURABLE', 'Activate a policy rule set (demo permission, not Bank of Baroda''s authorization structure)'),
 ('BRANCH_MANAGER', 'disbursement.release','CONFIGURABLE', 'Release a simulated disbursement (demo permission)'),
 ('BRANCH_MANAGER', 'disbursement.view',   'FIXED',        'View disbursement status'),
 ('BRANCH_MANAGER', 'analytics.view',      'FIXED',        'View analytics'),
 ('BRANCH_MANAGER', 'system.view',         'FIXED',        'View system status'),
 ('BRANCH_MANAGER', 'policy.view',         'FIXED',        'Read the policy knowledge base');

-- Demo limits: DEMO_ASSUMPTION values only. They are not Bank of Baroda policy and carry that label everywhere they are shown.
INSERT INTO demo_config (key, value, unit, description) VALUES
 ('terms.max_pct_of_eligible', 100,  'percent',      'Adjusted or approved amount may not exceed this share of the eligible amount'),
 ('terms.rate_min_bp',         1050, 'basis points', 'Lowest interest rate the demo allows (10.50%)'),
 ('terms.rate_max_bp',         1900, 'basis points', 'Highest interest rate the demo allows (19.00%)'),
 ('terms.tenure_min_months',   12,   'months',       'Shortest tenure the demo allows'),
 ('terms.tenure_max_months',   60,   'months',       'Longest tenure the demo allows');

-- Workflow: who may move an application from one status to the next.
INSERT INTO status_transitions (from_status, to_status, permission, actors, needs_reason) VALUES
 ('INTAKE',          'DOCS_PENDING',    'application.intake', 'EITHER', false),
 ('DOCS_PENDING',    'DISCREPANCY',     'document.verify',    'EITHER', false),
 ('DISCREPANCY',     'DOCS_PENDING',    'document.verify',    'STAFF',  false),
 ('DOCS_PENDING',    'DOCS_VERIFIED',   'document.verify',    'STAFF',  false),
 ('DISCREPANCY',     'DOCS_VERIFIED',   'document.verify',    'STAFF',  false),
 ('DOCS_VERIFIED',   'AI_ASSESSED',     NULL,                 'SYSTEM', false),
 ('AI_ASSESSED',     'FORWARDED',       'application.forward','STAFF',  false),
 ('FORWARDED',       'UNDER_REVIEW',    'decision.make',      'STAFF',  false),
 ('UNDER_REVIEW',    'APPROVED',        'decision.make',      'STAFF',  false),
 ('UNDER_REVIEW',    'DECLINED',        'decision.make',      'STAFF',  true),
 ('UNDER_REVIEW',    'RECONSIDERATION', 'application.refer',  'STAFF',  true),
 ('RECONSIDERATION', 'UNDER_REVIEW',    'decision.make',      'STAFF',  false),
 ('APPROVED',        'SANCTIONED',      'decision.make',      'EITHER', false),
 ('SANCTIONED',      'DISBURSED',       'disbursement.release','STAFF', false);

-- The audit-style tables cannot be changed by any application role, only appended to (triggers in 0002 also enforce this).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON audit_events, policy_activation_log, decisions, term_changes, application_terms FROM service_role;
  END IF;
  -- tables added by later migrations must not silently become readable by the browser roles
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated';
  END IF;
END $$;
