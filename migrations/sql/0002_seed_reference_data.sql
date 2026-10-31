-- =====================================================================
-- Idempotent reference data: roles and synchronization targets.
-- Safe to run repeatedly; never deletes or overwrites existing rows.
-- Synthetic test users and clinical data are NOT part of this script.
-- =====================================================================

MERGE [roles] AS target
USING (VALUES ('PACIENTE'), ('LABORATORISTA'), ('MEDICO'), ('ADMINISTRADOR')) AS source ([nombre])
  ON target.[nombre] = source.[nombre]
WHEN NOT MATCHED THEN
  INSERT ([nombre]) VALUES (source.[nombre]);
GO

MERGE [destinos_sincronizacion] AS target
USING (VALUES ('SUPABASE'), ('MONGODB')) AS source ([codigo])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [activo]) VALUES (source.[codigo], 1);
GO
