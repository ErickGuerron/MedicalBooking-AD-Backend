-- The consumer's idempotency check (INSERT ... ON CONFLICT DO NOTHING) reads the table, so
-- the writer needs SELECT on eventos_procesados. It stays insert-only otherwise: no UPDATE,
-- no DELETE, so the processed history cannot be rewritten by the consumer.

GRANT SELECT ON replica.eventos_procesados TO replica_writer;

DROP POLICY IF EXISTS writer_select ON replica.eventos_procesados;
CREATE POLICY writer_select ON replica.eventos_procesados FOR SELECT TO replica_writer
  USING (true);
