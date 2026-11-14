-- Appointments in the replica: the patient's own citas and their cancellation reason
-- (kafka-replication S10). Idempotent. Only the approved scope is projected: the patient's
-- free-text booking reason, the idempotency key, the row version, the doctor and the agenda
-- never reach the replica. The slot window (inicio, fin) shows the patient when the appointment
-- is. The reader sees a row only through the patient of the account it is bound to
-- (app.usuario_id); without it nothing matches (fail closed).

CREATE TABLE IF NOT EXISTS replica.citas (
  id uuid PRIMARY KEY,
  paciente_id uuid NOT NULL,
  servicio_id uuid NOT NULL,
  estado varchar(30) NOT NULL,
  reservada_en timestamptz NOT NULL,
  confirmada_en timestamptz,
  cancelada_en timestamptz,
  motivo_cancelacion_codigo varchar(40),
  motivo_cancelacion_texto varchar(1500),
  inicio timestamptz NOT NULL,
  fin timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_citas_paciente ON replica.citas (paciente_id);

ALTER TABLE replica.citas ENABLE ROW LEVEL SECURITY;

-- Privileges: authoritative for this table, like 0002 and 0007. The writer projects (SELECT,
-- INSERT, UPDATE: no DELETE); the reader only reads. Neither role may delete an appointment.
REVOKE ALL ON replica.citas FROM PUBLIC, anon, authenticated, replica_writer, replica_reader;
GRANT SELECT, INSERT, UPDATE ON replica.citas TO replica_writer;
GRANT SELECT ON replica.citas TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.citas;
CREATE POLICY writer_all ON replica.citas FOR ALL TO replica_writer USING (true) WITH CHECK (true);

-- Reader: the appointments of the patient bound to the account.
DROP POLICY IF EXISTS reader_own ON replica.citas;
CREATE POLICY reader_own ON replica.citas FOR SELECT TO replica_reader
  USING (paciente_id IN (SELECT id FROM replica.pacientes
         WHERE usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid));
