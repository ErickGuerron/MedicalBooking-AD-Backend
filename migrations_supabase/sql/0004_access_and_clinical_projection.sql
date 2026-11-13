-- Access tables and reviewed clinical projection for the replica (spec 8.3, approved scope).
-- Idempotent. Personal contact data (email, telefono, documento) is never copied.
-- Only ACEPTADA and CORREGIDA proposals are projected: the patient never sees a pending
-- or rejected machine hypothesis (CHECK below, and the consumer applies the same rule).

-- Account: id and state only. The reader checks ACTIVO before any result.
CREATE TABLE IF NOT EXISTS replica.usuarios (
  id uuid PRIMARY KEY,
  estado varchar(20) NOT NULL,
  actualizado_en timestamptz NOT NULL
);

-- Authorization catalog, copied whole: none of these columns are personal.
CREATE TABLE IF NOT EXISTS replica.roles (
  id integer PRIMARY KEY,
  nombre varchar(40) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS replica.permisos (
  id integer PRIMARY KEY,
  codigo varchar(120) NOT NULL UNIQUE,
  recurso varchar(80) NOT NULL,
  accion varchar(40) NOT NULL,
  descripcion varchar(300)
);

CREATE TABLE IF NOT EXISTS replica.roles_permisos (
  rol_id integer NOT NULL,
  permiso_id integer NOT NULL,
  PRIMARY KEY (rol_id, permiso_id)
);

CREATE TABLE IF NOT EXISTS replica.usuarios_roles (
  usuario_id uuid NOT NULL,
  rol_id integer NOT NULL,
  PRIMARY KEY (usuario_id, rol_id)
);

-- Patient: the account link (opaque id) and birth date. No contact data.
ALTER TABLE replica.pacientes ADD COLUMN IF NOT EXISTS usuario_id uuid;
ALTER TABLE replica.pacientes ADD COLUMN IF NOT EXISTS fecha_nacimiento date;

-- Reviewed proposal. resumen_propuesto is the machine text: it is projected only when
-- the physician corrected the conclusion; the reader and the consumer use conclusion_medica.
CREATE TABLE IF NOT EXISTS replica.propuestas_clinicas (
  id uuid PRIMARY KEY,
  orden_id uuid NOT NULL,
  estado varchar(25) NOT NULL CHECK (estado IN ('ACEPTADA', 'CORREGIDA')),
  resumen_propuesto text,
  conclusion_medica text,
  revisado_en timestamptz,
  actualizado_en timestamptz NOT NULL
);

-- Findings of a projected proposal. The physician note (observacion_medica) is not copied.
CREATE TABLE IF NOT EXISTS replica.propuestas_hallazgos (
  id uuid PRIMARY KEY,
  propuesta_id uuid NOT NULL,
  tipo varchar(30) NOT NULL,
  explicacion text,
  regla_version integer NOT NULL,
  actualizado_en timestamptz NOT NULL
);

ALTER TABLE replica.usuarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.permisos ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.roles_permisos ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.usuarios_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.propuestas_clinicas ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.propuestas_hallazgos ENABLE ROW LEVEL SECURITY;

-- Privileges: authoritative for the new tables, like 0002.
REVOKE ALL ON ALL TABLES IN SCHEMA replica FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON replica.usuarios, replica.roles, replica.permisos,
  replica.roles_permisos, replica.usuarios_roles, replica.propuestas_clinicas,
  replica.propuestas_hallazgos TO replica_writer;
GRANT SELECT ON replica.usuarios, replica.roles, replica.permisos, replica.roles_permisos,
  replica.usuarios_roles, replica.propuestas_clinicas, replica.propuestas_hallazgos
  TO replica_reader;

-- Writer: the consumer owns these tables.
DROP POLICY IF EXISTS writer_all ON replica.usuarios;
DROP POLICY IF EXISTS writer_all ON replica.roles;
DROP POLICY IF EXISTS writer_all ON replica.permisos;
DROP POLICY IF EXISTS writer_all ON replica.roles_permisos;
DROP POLICY IF EXISTS writer_all ON replica.usuarios_roles;
DROP POLICY IF EXISTS writer_all ON replica.propuestas_clinicas;
DROP POLICY IF EXISTS writer_all ON replica.propuestas_hallazgos;
CREATE POLICY writer_all ON replica.usuarios FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.roles FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.permisos FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.roles_permisos FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.usuarios_roles FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.propuestas_clinicas FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.propuestas_hallazgos FOR ALL TO replica_writer USING (true) WITH CHECK (true);

-- Reader: the account and its own role links only, the catalog, and the patient's own
-- reviewed proposals. Without app.usuario_id or app.paciente_id, nothing matches.
DROP POLICY IF EXISTS reader_own ON replica.usuarios;
DROP POLICY IF EXISTS reader_own ON replica.usuarios_roles;
DROP POLICY IF EXISTS reader_catalog ON replica.roles;
DROP POLICY IF EXISTS reader_catalog ON replica.permisos;
DROP POLICY IF EXISTS reader_catalog ON replica.roles_permisos;
DROP POLICY IF EXISTS reader_own ON replica.propuestas_clinicas;
DROP POLICY IF EXISTS reader_own ON replica.propuestas_hallazgos;
CREATE POLICY reader_own ON replica.usuarios FOR SELECT TO replica_reader
  USING (id = nullif(current_setting('app.usuario_id', true), '')::uuid);
CREATE POLICY reader_own ON replica.usuarios_roles FOR SELECT TO replica_reader
  USING (usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid);
CREATE POLICY reader_catalog ON replica.roles FOR SELECT TO replica_reader USING (true);
CREATE POLICY reader_catalog ON replica.permisos FOR SELECT TO replica_reader USING (true);
CREATE POLICY reader_catalog ON replica.roles_permisos FOR SELECT TO replica_reader USING (true);
CREATE POLICY reader_own ON replica.propuestas_clinicas FOR SELECT TO replica_reader
  USING (orden_id IN (SELECT id FROM replica.ordenes_laboratorio
         WHERE paciente_id = nullif(current_setting('app.paciente_id', true), '')::uuid));
CREATE POLICY reader_own ON replica.propuestas_hallazgos FOR SELECT TO replica_reader
  USING (propuesta_id IN (SELECT id FROM replica.propuestas_clinicas));
