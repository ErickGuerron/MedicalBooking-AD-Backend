-- Read-only screens during an outage: the doctor's name, the specialties and the services, and the
-- doctor of each appointment (citas.medico_id). Only staff names and catalog text are replicated:
-- no patient names, no document numbers, no clinical data.
-- Idempotent: tables use IF NOT EXISTS and policies are dropped before they are created.

CREATE TABLE IF NOT EXISTS replica.especialidades (
  id integer PRIMARY KEY,
  nombre varchar(100) NOT NULL,
  activo boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS replica.medicos (
  id uuid PRIMARY KEY,
  nombre varchar(100) NOT NULL,
  apellido varchar(100) NOT NULL,
  activo boolean NOT NULL
);

CREATE TABLE IF NOT EXISTS replica.servicios (
  id uuid PRIMARY KEY,
  especialidad_id integer NULL,
  nombre varchar(150) NOT NULL,
  duracion_min integer NOT NULL,
  precio_base numeric(18, 2) NOT NULL,
  moneda char(3) NOT NULL,
  activo boolean NOT NULL
);

-- The doctor of an appointment is a staff id, not patient data. Nullable: rows projected before
-- this migration have none until the directory sync fills them.
ALTER TABLE replica.citas ADD COLUMN IF NOT EXISTS medico_id uuid NULL;

ALTER TABLE replica.especialidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.medicos ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.servicios ENABLE ROW LEVEL SECURITY;

-- Privileges. The writer replaces these tables as a whole (DELETE then INSERT in one
-- transaction). The reader selects them for any signed-in caller. Nobody else may read or write.
REVOKE ALL ON replica.especialidades, replica.medicos, replica.servicios
  FROM PUBLIC, anon, authenticated, replica_writer, replica_reader;
GRANT SELECT, INSERT, DELETE ON replica.especialidades, replica.medicos, replica.servicios
  TO replica_writer;
GRANT SELECT ON replica.especialidades, replica.medicos, replica.servicios TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.especialidades;
CREATE POLICY writer_all ON replica.especialidades FOR ALL TO replica_writer
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS reader_all ON replica.especialidades;
CREATE POLICY reader_all ON replica.especialidades FOR SELECT TO replica_reader USING (true);

DROP POLICY IF EXISTS writer_all ON replica.medicos;
CREATE POLICY writer_all ON replica.medicos FOR ALL TO replica_writer
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS reader_all ON replica.medicos;
CREATE POLICY reader_all ON replica.medicos FOR SELECT TO replica_reader USING (true);

DROP POLICY IF EXISTS writer_all ON replica.servicios;
CREATE POLICY writer_all ON replica.servicios FOR ALL TO replica_writer
  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS reader_all ON replica.servicios;
CREATE POLICY reader_all ON replica.servicios FOR SELECT TO replica_reader USING (true);
