-- Scans and photos with no text layer and no imported Unlimited-OCR output (new customers, KYC updates) are read by Gemini from the image:
-- text_method GEMINI_VISION, shown as "Read by Gemini (AI, not OCR)". Values stay AI suggestions until a person verifies them.

ALTER TABLE documents DROP CONSTRAINT documents_text_method_check,
  ADD CONSTRAINT documents_text_method_check
  CHECK (text_method IN ('EMBEDDED_TEXT', 'UNLIMITED_OCR', 'PRECOMPUTED_UNLIMITED_OCR', 'GEMINI_VISION', 'MANUAL', 'PREPARED_DEMO_TEXT'));

ALTER TABLE document_fields DROP CONSTRAINT document_fields_text_method_check,
  ADD CONSTRAINT document_fields_text_method_check
  CHECK (text_method IN ('EMBEDDED_TEXT', 'UNLIMITED_OCR', 'PRECOMPUTED_UNLIMITED_OCR', 'GEMINI_VISION', 'MANUAL', 'PREPARED_DEMO_TEXT'));
