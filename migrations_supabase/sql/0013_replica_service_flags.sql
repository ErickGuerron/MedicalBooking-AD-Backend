-- Read-only replica of the service flags the portal and the admin read: a medical order is needed
-- (result reviews) and a blood sample is charged on the invoice (lab exams). Idempotent.
-- The defaults are false, so existing rows are valid until the next directory sync writes the real
-- values.

ALTER TABLE replica.servicios
  ADD COLUMN IF NOT EXISTS requiere_orden_medica boolean NOT NULL DEFAULT false;

ALTER TABLE replica.servicios
  ADD COLUMN IF NOT EXISTS requiere_toma_sangre boolean NOT NULL DEFAULT false;
