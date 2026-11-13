-- =====================================================================
-- Synchronization destination for the Kafka replication topic (kafka-replication S1).
-- Idempotent: safe to run more than once. Never deletes or overwrites existing rows.
-- =====================================================================

MERGE [destinos_sincronizacion] AS target
USING (VALUES ('KAFKA')) AS source ([codigo])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [activo]) VALUES (source.[codigo], 1);
GO