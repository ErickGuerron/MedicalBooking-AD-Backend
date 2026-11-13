-- Reader policies fail closed with zero rows, not with a cast error.
-- An unset or empty app.paciente_id must match nothing. Without nullif the cast of ''
-- to uuid raises an error instead of returning no rows.

DROP POLICY IF EXISTS reader_own ON replica.pacientes;
DROP POLICY IF EXISTS reader_own ON replica.ordenes_laboratorio;
DROP POLICY IF EXISTS reader_own ON replica.ordenes_laboratorio_detalle;
DROP POLICY IF EXISTS reader_own ON replica.resultados_laboratorio;

CREATE POLICY reader_own ON replica.pacientes FOR SELECT TO replica_reader
  USING (id = nullif(current_setting('app.paciente_id', true), '')::uuid);
CREATE POLICY reader_own ON replica.ordenes_laboratorio FOR SELECT TO replica_reader
  USING (paciente_id = nullif(current_setting('app.paciente_id', true), '')::uuid);
CREATE POLICY reader_own ON replica.ordenes_laboratorio_detalle FOR SELECT TO replica_reader
  USING (orden_id IN (SELECT id FROM replica.ordenes_laboratorio
         WHERE paciente_id = nullif(current_setting('app.paciente_id', true), '')::uuid));
CREATE POLICY reader_own ON replica.resultados_laboratorio FOR SELECT TO replica_reader
  USING (detalle_orden_id IN (SELECT d.id FROM replica.ordenes_laboratorio_detalle d
         JOIN replica.ordenes_laboratorio o ON o.id = d.orden_id
         WHERE o.paciente_id = nullif(current_setting('app.paciente_id', true), '')::uuid));
