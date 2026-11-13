-- Privileges and row-level security for the replica roles (spec 8.3, E8).
-- Roles are created outside migrations because they carry passwords; this file fails
-- fast when they are missing. It is authoritative: it revokes every table privilege
-- the roles hold, then grants exactly the design. Safe to run more than once.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replica_writer')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replica_reader') THEN
    RAISE EXCEPTION 'replica_writer and replica_reader must exist before this migration';
  END IF;
END
$$;

ALTER ROLE replica_writer NOBYPASSRLS;
ALTER ROLE replica_reader NOBYPASSRLS;

REVOKE ALL ON ALL TABLES IN SCHEMA replica FROM PUBLIC, anon, authenticated, replica_writer, replica_reader;

GRANT USAGE ON SCHEMA replica TO replica_writer, replica_reader;

GRANT SELECT, INSERT, UPDATE ON replica.pacientes, replica.ordenes_laboratorio,
  replica.ordenes_laboratorio_detalle, replica.resultados_laboratorio TO replica_writer;
GRANT INSERT ON replica.eventos_procesados TO replica_writer;
GRANT SELECT, INSERT, UPDATE ON replica.watermarks TO replica_writer;

GRANT SELECT ON replica.pacientes, replica.ordenes_laboratorio,
  replica.ordenes_laboratorio_detalle, replica.resultados_laboratorio TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.pacientes;
DROP POLICY IF EXISTS writer_all ON replica.ordenes_laboratorio;
DROP POLICY IF EXISTS writer_all ON replica.ordenes_laboratorio_detalle;
DROP POLICY IF EXISTS writer_all ON replica.resultados_laboratorio;
DROP POLICY IF EXISTS writer_insert ON replica.eventos_procesados;
DROP POLICY IF EXISTS writer_all ON replica.watermarks;
DROP POLICY IF EXISTS reader_own ON replica.pacientes;
DROP POLICY IF EXISTS reader_own ON replica.ordenes_laboratorio;
DROP POLICY IF EXISTS reader_own ON replica.ordenes_laboratorio_detalle;
DROP POLICY IF EXISTS reader_own ON replica.resultados_laboratorio;

CREATE POLICY writer_all ON replica.pacientes FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.ordenes_laboratorio FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.ordenes_laboratorio_detalle FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.resultados_laboratorio FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_insert ON replica.eventos_procesados FOR INSERT TO replica_writer WITH CHECK (true);
CREATE POLICY writer_all ON replica.watermarks FOR ALL TO replica_writer USING (true) WITH CHECK (true);

-- The reader sees only rows of the patient fixed for the session (SET LOCAL app.paciente_id).
-- Without it, current_setting returns NULL and no row matches: fail closed.
CREATE POLICY reader_own ON replica.pacientes FOR SELECT TO replica_reader
  USING (id = current_setting('app.paciente_id', true)::uuid);
CREATE POLICY reader_own ON replica.ordenes_laboratorio FOR SELECT TO replica_reader
  USING (paciente_id = current_setting('app.paciente_id', true)::uuid);
CREATE POLICY reader_own ON replica.ordenes_laboratorio_detalle FOR SELECT TO replica_reader
  USING (orden_id IN (SELECT id FROM replica.ordenes_laboratorio
         WHERE paciente_id = current_setting('app.paciente_id', true)::uuid));
CREATE POLICY reader_own ON replica.resultados_laboratorio FOR SELECT TO replica_reader
  USING (detalle_orden_id IN (SELECT d.id FROM replica.ordenes_laboratorio_detalle d
         JOIN replica.ordenes_laboratorio o ON o.id = d.orden_id
         WHERE o.paciente_id = current_setting('app.paciente_id', true)::uuid));
