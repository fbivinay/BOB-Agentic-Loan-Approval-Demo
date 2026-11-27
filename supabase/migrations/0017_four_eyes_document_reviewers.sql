-- Four-eyes, widened again (security review of 0016): verifying or rejecting a document (documents.reviewed_by) and withdrawing one
-- (documents.withdrawn_by) are processing work too. Every other processing record already counts (0016).

CREATE OR REPLACE FUNCTION processed_by(p_app uuid, p_user uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT p_user IS NOT NULL AND (
       EXISTS (SELECT 1 FROM applications WHERE id = p_app AND p_user IN (intake_completed_by, forwarded_by))
    OR EXISTS (SELECT 1 FROM documents WHERE application_id = p_app AND p_user IN (uploaded_by, reviewed_by, withdrawn_by))
    OR EXISTS (SELECT 1 FROM extraction_corrections c JOIN documents d ON d.id = c.document_id WHERE d.application_id = p_app AND c.corrected_by = p_user)
    OR EXISTS (SELECT 1 FROM discrepancies WHERE application_id = p_app AND resolved_by = p_user)
    OR EXISTS (SELECT 1 FROM audit_events WHERE application_id = p_app AND event_type = 'STATUS_TRANSITION' AND actor_type = 'STAFF' AND actor_id = p_user
               AND before_state ->> 'status' IN ('INTAKE', 'DOCS_PENDING', 'DISCREPANCY', 'AI_ASSESSED')))
$$;

REVOKE ALL ON FUNCTION processed_by(uuid, uuid) FROM PUBLIC;
