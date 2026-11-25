-- Declared figures belong to each draft and each application, not to the shared applicant record.
-- At conversion the draft's declared income, other-lender EMIs and credit history are copied onto the application and frozen there,
-- so a later draft for the same customer can never change what an in-flight application was assessed on.

ALTER TABLE applications
  ADD COLUMN declared_monthly_net numeric(14, 2) CHECK (declared_monthly_net > 0),
  ADD COLUMN existing_monthly_emi numeric(14, 2) CHECK (existing_monthly_emi >= 0),
  ADD COLUMN declared_credit_history text CHECK (declared_credit_history IN ('ESTABLISHED', 'LIMITED', 'NONE', 'UNKNOWN'));

COMMENT ON COLUMN applicants.declared_monthly_net IS 'As first recorded. Current figures live on each enquiry (inputs) and are frozen on each application.';

CREATE FUNCTION applications_declared_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.declared_monthly_net, NEW.existing_monthly_emi, NEW.declared_credit_history) IS DISTINCT FROM
     (OLD.declared_monthly_net, OLD.existing_monthly_emi, OLD.declared_credit_history) THEN
    RAISE EXCEPTION 'declared figures are frozen when the application is created' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER applications_declared_frozen BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION applications_declared_frozen();

-- Applicant records were not audited; they are now.
CREATE TRIGGER applicants_audit AFTER INSERT OR UPDATE ON applicants FOR EACH ROW EXECUTE FUNCTION audit_row('APPLICANT');
