-- Patient-requested lab orders in the replica: no ordering doctor, and the origin flag.
-- Idempotent: DROP NOT NULL is a no-op when already nullable; ADD COLUMN IF NOT EXISTS.

ALTER TABLE replica.ordenes_laboratorio ALTER COLUMN medico_id DROP NOT NULL;

ALTER TABLE replica.ordenes_laboratorio
  ADD COLUMN IF NOT EXISTS origen varchar(20) NOT NULL DEFAULT 'MEDICO'
  CHECK (origen IN ('MEDICO', 'PACIENTE'));
