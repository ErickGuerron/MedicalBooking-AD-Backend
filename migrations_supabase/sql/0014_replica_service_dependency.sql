-- Read-only replica of the service dependency (servicio_adicional_id) that replaces the blood
-- sample flag (requiere_toma_sangre, dropped here). The directory sync rewrites the value; until
-- then it is NULL and no invoice is affected: invoices are built on the primary database.
-- No foreign key on the replica: the directory is replaced as a whole, row by row. Idempotent.

ALTER TABLE replica.servicios
  ADD COLUMN IF NOT EXISTS servicio_adicional_id uuid NULL;

ALTER TABLE replica.servicios
  DROP COLUMN IF EXISTS requiere_toma_sangre;
