-- Treatments and their follow-up notes in the replica (treatments S11, kafka-replication S10).
-- The text is replicated: descripcion (tratamiento.creado, tratamiento.actualizado) and the
-- follow-up nota (seguimiento.registrado). The doctor's user id, the audit trail and the record
-- timestamps never reach the replica.
-- version is the aggregate version of the event that wrote the row. The writer's upsert keeps
-- the stored row only while that version is older, so a stale event never overwrites a newer one.
-- Idempotent: tables use IF NOT EXISTS and policies are dropped before they are created.

CREATE TABLE IF NOT EXISTS replica.tratamientos (
  id uuid PRIMARY KEY,
  cita_id uuid NOT NULL,
  paciente_id uuid NOT NULL,
  medico_id uuid NOT NULL,
  descripcion varchar(1000) NOT NULL,
  estado varchar(20) NOT NULL CHECK (estado IN ('ACTIVO', 'FINALIZADO', 'SUSPENDIDO')),
  fecha_inicio date NOT NULL,
  fecha_fin date,
  version integer NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tratamientos_paciente ON replica.tratamientos (paciente_id);

CREATE TABLE IF NOT EXISTS replica.tratamientos_seguimientos (
  id uuid PRIMARY KEY,
  tratamiento_id uuid NOT NULL,
  secuencia bigint NOT NULL UNIQUE,
  nota varchar(2000) NOT NULL,
  creado_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tratamientos_seguimientos_tratamiento
  ON replica.tratamientos_seguimientos (tratamiento_id, secuencia);

ALTER TABLE replica.tratamientos ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.tratamientos_seguimientos ENABLE ROW LEVEL SECURITY;

-- Privileges: authoritative for these two tables, like 0008. The writer projects treatments
-- (SELECT, INSERT, UPDATE) and follow-ups (INSERT only: a note is never changed). The reader only
-- selects. No role may delete from these tables.
REVOKE ALL ON replica.tratamientos, replica.tratamientos_seguimientos
  FROM PUBLIC, anon, authenticated, replica_writer, replica_reader;
GRANT SELECT, INSERT, UPDATE ON replica.tratamientos TO replica_writer;
GRANT INSERT ON replica.tratamientos_seguimientos TO replica_writer;
GRANT SELECT ON replica.tratamientos, replica.tratamientos_seguimientos TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.tratamientos;
CREATE POLICY writer_all ON replica.tratamientos FOR ALL TO replica_writer
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS writer_insert ON replica.tratamientos_seguimientos;
CREATE POLICY writer_insert ON replica.tratamientos_seguimientos FOR INSERT TO replica_writer
  WITH CHECK (true);

-- Reader: the treatments of the patient bound to the account, and their notes (app.usuario_id).
-- Without it nothing matches (fail closed).
DROP POLICY IF EXISTS reader_own ON replica.tratamientos;
CREATE POLICY reader_own ON replica.tratamientos FOR SELECT TO replica_reader
  USING (paciente_id IN (SELECT id FROM replica.pacientes
         WHERE usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid));

DROP POLICY IF EXISTS reader_own ON replica.tratamientos_seguimientos;
CREATE POLICY reader_own ON replica.tratamientos_seguimientos FOR SELECT TO replica_reader
  USING (tratamiento_id IN (SELECT t.id FROM replica.tratamientos t
         WHERE t.paciente_id IN (SELECT id FROM replica.pacientes
           WHERE usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid)));
