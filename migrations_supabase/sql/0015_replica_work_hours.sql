-- Read-only replica of the declared weekly work hours (scheduling). One row per doctor and week
-- (vigente_desde). The blocks are replaced as a whole by the outbox event horario_trabajo.declarado
-- and by the catalog snapshot; the version guards against a stale event. Idempotent.

CREATE TABLE IF NOT EXISTS replica.horarios_trabajo_version (
  medico_id uuid NOT NULL,
  vigente_desde date NOT NULL,
  bloques jsonb NOT NULL,
  version bigint NOT NULL,
  PRIMARY KEY (medico_id, vigente_desde)
);

ALTER TABLE replica.horarios_trabajo_version ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON replica.horarios_trabajo_version TO replica_writer;
GRANT SELECT ON replica.horarios_trabajo_version TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.horarios_trabajo_version;
CREATE POLICY writer_all ON replica.horarios_trabajo_version
  FOR ALL TO replica_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS reader_all ON replica.horarios_trabajo_version;
CREATE POLICY reader_all ON replica.horarios_trabajo_version
  FOR SELECT TO replica_reader USING (true);
