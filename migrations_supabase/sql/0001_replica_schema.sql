-- Supabase replica: read projections of lab orders and validated results (spec 8.3).
-- Idempotent: safe to run on a database where the tables already exist.

CREATE SCHEMA IF NOT EXISTS replica;

CREATE TABLE IF NOT EXISTS replica.pacientes (
  id uuid PRIMARY KEY,
  nombre varchar(100) NOT NULL,
  apellido varchar(100) NOT NULL,
  actualizado_en timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS replica.ordenes_laboratorio (
  id uuid PRIMARY KEY,
  paciente_id uuid NOT NULL,
  medico_id uuid NOT NULL,
  fecha_orden timestamptz NOT NULL,
  prioridad varchar(20) NOT NULL,
  estado varchar(25) NOT NULL,
  actualizado_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ordenes_paciente ON replica.ordenes_laboratorio (paciente_id);

CREATE TABLE IF NOT EXISTS replica.ordenes_laboratorio_detalle (
  id uuid PRIMARY KEY,
  orden_id uuid NOT NULL,
  tipo_prueba_id uuid NOT NULL,
  estado varchar(25) NOT NULL,
  fecha_resultado timestamptz,
  actualizado_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_detalle_orden ON replica.ordenes_laboratorio_detalle (orden_id);

-- Only validated results: the consumer never inserts a row without validado_en.
CREATE TABLE IF NOT EXISTS replica.resultados_laboratorio (
  id uuid PRIMARY KEY,
  detalle_orden_id uuid NOT NULL,
  nombre_analito varchar(150) NOT NULL,
  valor_texto varchar(300),
  valor_numerico numeric(18,6),
  unidad varchar(40),
  referencia_min numeric(18,6),
  referencia_max numeric(18,6),
  interpretacion varchar(25),
  validado_en timestamptz NOT NULL,
  actualizado_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_resultados_detalle ON replica.resultados_laboratorio (detalle_orden_id);

-- Idempotency evidence: one row per event and consumer (spec 8.3, line 267).
CREATE TABLE IF NOT EXISTS replica.eventos_procesados (
  evento_id uuid NOT NULL,
  consumidor varchar(40) NOT NULL,
  tipo_agregado varchar(60) NOT NULL,
  procesado_en timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (evento_id, consumidor)
);

-- Observable lag per consumer (spec line 341).
CREATE TABLE IF NOT EXISTS replica.watermarks (
  consumidor varchar(40) PRIMARY KEY,
  ultimo_evento_en timestamptz,
  ultimo_sincronizado_en timestamptz
);

ALTER TABLE replica.pacientes ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.ordenes_laboratorio ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.ordenes_laboratorio_detalle ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.resultados_laboratorio ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.eventos_procesados ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.watermarks ENABLE ROW LEVEL SECURITY;
