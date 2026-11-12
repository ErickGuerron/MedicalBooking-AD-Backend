-- =====================================================================
-- Synchronization destination for patient notifications (soap-notifications-outbox S3).
-- Idempotent: safe to run more than once. Never deletes or overwrites existing rows.
-- =====================================================================

MERGE [destinos_sincronizacion] AS target
USING (VALUES ('NOTIFICACIONES')) AS source ([codigo])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [activo]) VALUES (source.[codigo], 1);
GO
