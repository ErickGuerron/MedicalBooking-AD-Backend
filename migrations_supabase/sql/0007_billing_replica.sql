-- Billing in the replica: invoices, invoice lines, payments and refunds (kafka-replication S10).
-- Idempotent. Only the approved scope is projected: no gateway identifiers, no idempotency
-- keys, no free-text refund reason and no contact data. The reader sees a row only through the
-- patient of the account it is bound to (app.usuario_id); without it nothing matches (fail closed).

CREATE TABLE IF NOT EXISTS replica.facturas (
  id uuid PRIMARY KEY,
  paciente_id uuid NOT NULL,
  cita_id uuid NOT NULL,
  numero_factura varchar(60) NOT NULL,
  subtotal numeric(18,2) NOT NULL,
  impuestos numeric(18,2) NOT NULL,
  total numeric(18,2) NOT NULL,
  moneda varchar(3) NOT NULL,
  estado varchar(20) NOT NULL,
  creada_en timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facturas_paciente ON replica.facturas (paciente_id);

CREATE TABLE IF NOT EXISTS replica.facturas_detalle (
  id uuid PRIMARY KEY,
  factura_id uuid NOT NULL,
  servicio_id uuid NOT NULL,
  descripcion varchar(300) NOT NULL,
  cantidad integer NOT NULL,
  precio_unitario numeric(18,2) NOT NULL,
  subtotal numeric(18,2) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facturas_detalle_factura ON replica.facturas_detalle (factura_id);

CREATE TABLE IF NOT EXISTS replica.pagos (
  id uuid PRIMARY KEY,
  factura_id uuid NOT NULL,
  monto numeric(18,2) NOT NULL,
  moneda varchar(3) NOT NULL,
  estado varchar(20) NOT NULL,
  creado_en timestamptz NOT NULL,
  pagado_en timestamptz
);
CREATE INDEX IF NOT EXISTS ix_pagos_factura ON replica.pagos (factura_id);

CREATE TABLE IF NOT EXISTS replica.reembolsos (
  id uuid PRIMARY KEY,
  pago_id uuid NOT NULL,
  monto numeric(18,2) NOT NULL,
  estado varchar(20) NOT NULL,
  solicitado_en timestamptz NOT NULL,
  procesado_en timestamptz,
  motivo_codigo varchar(50) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reembolsos_pago ON replica.reembolsos (pago_id);

ALTER TABLE replica.facturas ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.facturas_detalle ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.pagos ENABLE ROW LEVEL SECURITY;
ALTER TABLE replica.reembolsos ENABLE ROW LEVEL SECURITY;

-- Privileges: authoritative for these tables, like 0002. The writer projects (SELECT, INSERT,
-- UPDATE: no DELETE); the reader only reads. Neither role may delete a billing row.
REVOKE ALL ON replica.facturas, replica.facturas_detalle, replica.pagos, replica.reembolsos
  FROM PUBLIC, anon, authenticated, replica_writer, replica_reader;
GRANT SELECT, INSERT, UPDATE ON replica.facturas, replica.facturas_detalle, replica.pagos,
  replica.reembolsos TO replica_writer;
GRANT SELECT ON replica.facturas, replica.facturas_detalle, replica.pagos,
  replica.reembolsos TO replica_reader;

DROP POLICY IF EXISTS writer_all ON replica.facturas;
DROP POLICY IF EXISTS writer_all ON replica.facturas_detalle;
DROP POLICY IF EXISTS writer_all ON replica.pagos;
DROP POLICY IF EXISTS writer_all ON replica.reembolsos;
CREATE POLICY writer_all ON replica.facturas FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.facturas_detalle FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.pagos FOR ALL TO replica_writer USING (true) WITH CHECK (true);
CREATE POLICY writer_all ON replica.reembolsos FOR ALL TO replica_writer USING (true) WITH CHECK (true);

-- Reader: the invoice of the patient bound to the account; the rest follows its parent row.
DROP POLICY IF EXISTS reader_own ON replica.facturas;
DROP POLICY IF EXISTS reader_own ON replica.facturas_detalle;
DROP POLICY IF EXISTS reader_own ON replica.pagos;
DROP POLICY IF EXISTS reader_own ON replica.reembolsos;
CREATE POLICY reader_own ON replica.facturas FOR SELECT TO replica_reader
  USING (paciente_id IN (SELECT id FROM replica.pacientes
         WHERE usuario_id = nullif(current_setting('app.usuario_id', true), '')::uuid));
CREATE POLICY reader_own ON replica.facturas_detalle FOR SELECT TO replica_reader
  USING (factura_id IN (SELECT id FROM replica.facturas));
CREATE POLICY reader_own ON replica.pagos FOR SELECT TO replica_reader
  USING (factura_id IN (SELECT id FROM replica.facturas));
CREATE POLICY reader_own ON replica.reembolsos FOR SELECT TO replica_reader
  USING (pago_id IN (SELECT id FROM replica.pagos));
