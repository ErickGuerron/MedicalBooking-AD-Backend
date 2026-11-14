-- The reader finds its own patient from the account (app.usuario_id). Only the patient
-- linked to that account matches; without app.usuario_id nothing matches (fail closed).

DROP POLICY IF EXISTS reader_self ON replica.pacientes;
CREATE POLICY reader_self ON replica.pacientes FOR SELECT TO replica_reader
  USING (usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid);
